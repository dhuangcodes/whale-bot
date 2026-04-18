import time
import logging
from collections import defaultdict
from datetime import datetime, timezone
from api import get_leaderboard, batch_get_activity, get_wallet_profile, get_market_by_condition
from scorer import score
from alerts import Alerter
from config import MIN_TRADE_USD, POLL_INTERVAL, TOP_WALLETS_COUNT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("whale.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

WALLET_REFRESH  = 6 * 3600
BATCH_SIZE      = 30
CONSENSUS_WINDOW = 3600  # 1 hour in seconds


def parse(raw: dict, wallet: str, profile: dict) -> dict | None:
    try:
        usd    = float(raw.get("usdcSize") or 0)
        price  = float(raw.get("price") or 0)
        if usd <= 0 and price > 0:
            usd = float(raw.get("size", 0)) * price
        if usd < 1:
            return None

        raw_outcome = raw.get("outcome", raw.get("side", "?"))
        # Keep original outcome label — player/team names are intentional on sports markets
        outcome = str(raw_outcome).upper() if raw_outcome else "?"

        condition = raw.get("conditionId", "")
        title     = raw.get("title", "")
        slug      = raw.get("slug") or raw.get("eventSlug") or ""
        tx        = raw.get("transactionHash", "")
        ts        = int(raw.get("timestamp", 0))
        tid       = tx or f"{wallet}-{ts}-{usd}"

        return {
            "id":           tid,
            "wallet":       wallet.lower(),
            "usd":          usd,
            "price_cents":  price * 100,
            "outcome":      outcome,
            "condition":    condition,
            "timestamp":    ts,
            "market_title": title or condition[:30] + "...",
            "market_url":   f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            "pnl":          float(profile.get("pnl", 0) or 0),
            "win_rate":     0.0,
            "n_trades":     0,
        }
    except Exception as e:
        log.debug(f"Parse error: {e}")
        return None


def run():
    log.info("🐳 Polymarket Whale Alert Bot Starting")
    log.info(f"Threshold: ${MIN_TRADE_USD:,.0f} | Top {TOP_WALLETS_COUNT} wallets")

    alerter       = Alerter()
    seen          = set()
    profile_cache = {}
    market_cache  = {}
    wallets       = []
    wallet_idx    = 0
    last_refresh  = 0
    last_ts       = int(datetime.now(timezone.utc).timestamp()) - 300

    # Whale consensus tracker: condition -> list of (timestamp, side, wallet)
    consensus_log: dict[str, list] = defaultdict(list)

    while True:
        try:
            now = int(datetime.now(timezone.utc).timestamp())

            # Refresh leaderboard every 6 hours
            if now - last_refresh > WALLET_REFRESH or not wallets:
                log.info("Refreshing leaderboard...")
                board = get_leaderboard(limit=TOP_WALLETS_COUNT)
                wallets = []
                for e in board:
                    addr = (e.get("proxyWallet") or e.get("address", "")).lower()
                    if addr:
                        wallets.append(addr)
                        profile_cache[addr] = {
                            "pnl": float(e.get("pnl", 0) or 0),
                        }
                last_refresh = now
                log.info(f"Monitoring {len(wallets)} wallets")

            if not wallets:
                time.sleep(60)
                continue

            # Check a batch in parallel
            batch = wallets[wallet_idx: wallet_idx + BATCH_SIZE]
            wallet_idx = (wallet_idx + BATCH_SIZE) % len(wallets)

            activity_map = batch_get_activity(batch, limit=10)
            new_whales   = []

            for wallet, trades in activity_map.items():
                if wallet not in profile_cache:
                    profile_cache[wallet] = {"pnl": 0}
                profile = profile_cache[wallet]

                for raw in trades:
                    ts = int(raw.get("timestamp", 0))
                    if ts < last_ts - 120:
                        continue

                    trade = parse(raw, wallet, profile)
                    if not trade or trade["id"] in seen:
                        continue
                    seen.add(trade["id"])

                    if trade["usd"] < MIN_TRADE_USD:
                        continue

                    new_whales.append(trade)

            for trade in new_whales:
                # Enrich market info
                cid = trade["condition"]
                if cid and cid not in market_cache:
                    fetched = get_market_by_condition(cid) or {}
                    # Only cache if we got a real title back
                    if fetched.get("question") or fetched.get("title"):
                        market_cache[cid] = fetched
                    else:
                        fetched = {}
                    info = fetched
                else:
                    info = market_cache.get(cid, {})

                title = info.get("question") or info.get("title") or ""
                if title:
                    trade["market_title"] = title
                slug = info.get("market_slug") or info.get("slug") or ""
                if slug:
                    trade["market_url"] = f"https://polymarket.com/event/{slug}"

                # Get 24hr volume from market info — try all known field names
                volume_24h = 0.0
                try:
                    volume_24h = float(
                        info.get("volume24hr") or
                        info.get("volume_24hr") or
                        info.get("volumeNum") or
                        info.get("volume") or
                        0
                    )
                except Exception:
                    pass
                if volume_24h == 0:
                    log.debug(f"No volume for {cid}: info keys={list(info.keys())[:10]}")

                # Get current price for price movement signal
                price_after = 0.0
                try:
                    outcome_prices = info.get("outcomePrices")
                    if outcome_prices:
                        import json
                        prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                        if trade["outcome"] == "YES" and len(prices) > 0:
                            price_after = float(prices[0]) * 100
                        elif trade["outcome"] == "NO" and len(prices) > 1:
                            price_after = float(prices[1]) * 100
                except Exception:
                    pass

                # Whale consensus — count same side on same market in last hour
                cutoff = now - CONSENSUS_WINDOW
                consensus_log[cid] = [
                    (t, s, w) for t, s, w in consensus_log[cid]
                    if t > cutoff and w != trade["wallet"]
                ]
                same_side = sum(
                    1 for t, s, w in consensus_log[cid]
                    if s == trade["outcome"]
                )
                # Log this whale
                consensus_log[cid].append((now, trade["outcome"], trade["wallet"]))

                s = score(
                    usd              = trade["usd"],
                    price_cents      = trade["price_cents"],
                    pnl              = trade["pnl"],
                    volume_24h       = volume_24h,
                    price_after_cents = price_after,
                    side             = trade["outcome"],
                    same_side_whales = same_side,
                )

                # Add extra context for alert display
                trade["volume_24h"]   = volume_24h
                trade["price_after"]  = price_after
                trade["same_side_whales"] = same_side

                alerter.send(trade, s)

            if wallet_idx < BATCH_SIZE:
                last_ts = now - 60

            if not new_whales:
                batch_num = max(1, wallet_idx // BATCH_SIZE)
                total_batches = max(1, len(wallets) // BATCH_SIZE)
                log.info(f"No whale trades (batch {batch_num}/{total_batches})")

            # Cleanup
            if len(seen) > 20_000:
                seen = set(list(seen)[-5000:])
            if len(profile_cache) > 1000:
                profile_cache.clear()
            if len(market_cache) > 1000:
                market_cache.clear()
            # Trim consensus log
            for cid in list(consensus_log.keys()):
                consensus_log[cid] = [
                    (t, s, w) for t, s, w in consensus_log[cid]
                    if t > now - CONSENSUS_WINDOW
                ]

        except Exception as e:
            log.error(f"Loop error: {e}", exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
