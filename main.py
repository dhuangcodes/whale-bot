import time
import logging
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

WALLET_REFRESH  = 6 * 3600   # refresh leaderboard every 6 hours
BATCH_SIZE      = 30          # wallets to check per cycle


def parse(raw: dict, wallet: str, profile: dict) -> dict | None:
    try:
        usd    = float(raw.get("usdcSize") or 0)
        price  = float(raw.get("price") or 0)
        if usd <= 0 and price > 0:
            usd = float(raw.get("size", 0)) * price
        if usd < 1:
            return None

        outcome   = raw.get("outcome", raw.get("side", "?"))
        condition = raw.get("conditionId", "")
        title     = raw.get("title", "")
        slug      = raw.get("slug") or raw.get("eventSlug") or ""
        tx        = raw.get("transactionHash", "")
        ts        = raw.get("timestamp", 0)
        tid       = tx or f"{wallet}-{ts}-{usd}"

        return {
            "id":          tid,
            "wallet":      wallet.lower(),
            "usd":         usd,
            "price_cents": price * 100,
            "outcome":     str(outcome).upper() if outcome else "?",
            "condition":   condition,
            "market_title": title or condition[:30] + "...",
            "market_url":  f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            "pnl":         float(profile.get("pnl", 0) or 0),
            "win_rate":    float(profile.get("win_rate", 0) or 0),
            "n_trades":    int(profile.get("trades_count") or profile.get("num_trades", 0) or 0),
        }
    except Exception as e:
        log.debug(f"Parse error: {e}")
        return None


def run():
    log.info("🐳 Polymarket Whale Alert Bot Starting")
    log.info(f"Threshold: ${MIN_TRADE_USD:,.0f} | Monitoring top {TOP_WALLETS_COUNT} wallets")

    alerter       = Alerter()
    seen          = set()
    profile_cache = {}
    market_cache  = {}
    wallets       = []
    wallet_idx    = 0
    last_refresh  = 0
    last_ts       = int(datetime.now(timezone.utc).timestamp()) - 300

    while True:
        try:
            now = int(datetime.now(timezone.utc).timestamp())

            # Refresh leaderboard periodically
            if now - last_refresh > WALLET_REFRESH or not wallets:
                log.info("Refreshing leaderboard...")
                board = get_leaderboard(limit=TOP_WALLETS_COUNT)
                wallets = [
                    (e.get("proxyWallet") or e.get("address", "")).lower()
                    for e in board
                    if e.get("proxyWallet") or e.get("address")
                ]
                # Also cache profiles from leaderboard data
                for e in board:
                    addr = (e.get("proxyWallet") or e.get("address", "")).lower()
                    if addr:
                        profile_cache[addr] = {
                            "pnl":          float(e.get("pnl", 0) or 0),
                            "win_rate":     float(e.get("win_rate", 0) or 0),
                            "trades_count": int(e.get("num_trades", 0) or 0),
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
                # Ensure we have profile
                if wallet not in profile_cache:
                    profile_cache[wallet] = get_wallet_profile(wallet) or {}
                profile = profile_cache[wallet]

                for raw in trades:
                    # Filter by timestamp
                    ts = int(raw.get("timestamp", 0))
                    if ts < last_ts - 120:
                        continue

                    trade = parse(raw, wallet, profile)
                    if not trade:
                        continue
                    if trade["id"] in seen:
                        continue
                    seen.add(trade["id"])

                    if trade["usd"] < MIN_TRADE_USD:
                        continue

                    # Enrich market title if missing
                    if not trade["market_title"] or trade["market_title"].endswith("..."):
                        cid = trade["condition"]
                        if cid and cid not in market_cache:
                            market_cache[cid] = get_market_by_condition(cid) or {}
                        info = market_cache.get(cid, {})
                        title = info.get("question") or info.get("title") or ""
                        if title:
                            trade["market_title"] = title
                        slug = info.get("market_slug") or info.get("slug") or ""
                        if slug:
                            trade["market_url"] = f"https://polymarket.com/event/{slug}"

                    new_whales.append(trade)

            for trade in new_whales:
                s = score(
                    usd         = trade["usd"],
                    price_cents = trade["price_cents"],
                    pnl         = trade["pnl"],
                    win_rate    = trade["win_rate"],
                    n_trades    = trade["n_trades"],
                )
                alerter.send(trade, s)

            # Advance timestamp every full cycle
            if wallet_idx < BATCH_SIZE:
                last_ts = now - 60

            if not new_whales:
                batch_num = (wallet_idx // BATCH_SIZE) or 1
                total_batches = (len(wallets) // BATCH_SIZE) + 1
                log.info(f"No whale trades (batch {batch_num}/{total_batches})")

            # Cleanup caches
            if len(seen) > 20_000:
                seen = set(list(seen)[-5000:])
            if len(profile_cache) > 1000:
                profile_cache.clear()
            if len(market_cache) > 1000:
                market_cache.clear()

        except Exception as e:
            log.error(f"Loop error: {e}", exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
