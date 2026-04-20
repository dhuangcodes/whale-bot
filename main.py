import json
import time
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from api import (get_leaderboard, batch_get_activity,
                 get_market_by_condition, get_market_by_event_slug)
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

WALLET_REFRESH   = 6 * 3600
BATCH_SIZE       = 30
CONSENSUS_WINDOW = 3600

# Use /tmp for persistence — survives Railway restarts better than working dir
WALLETS_FILE = "/tmp/whale_wallets.json"


def load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Could not load {path}: {e}")
    return default


def save_json(path: str, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning(f"Could not save {path}: {e}")


def parse(raw: dict, wallet: str, profile: dict) -> dict | None:
    try:
        usd   = float(raw.get("usdcSize") or 0)
        price = float(raw.get("price") or 0)
        if usd <= 0 and price > 0:
            usd = float(raw.get("size", 0)) * price
        if usd < 1:
            return None

        raw_outcome = raw.get("outcome", raw.get("side", "?"))
        outcome     = str(raw_outcome).upper() if raw_outcome else "?"

        condition  = raw.get("conditionId", "")
        event_slug = raw.get("eventSlug") or raw.get("slug") or ""
        title      = raw.get("title", "")
        tx         = raw.get("transactionHash", "")
        ts         = int(raw.get("timestamp", 0))
        # Use tx hash if available, otherwise bucket by wallet+condition+timestamp+rounded size
        # Rounding usd to nearest 100 groups Polymarket order splits (same order, multiple fills)
        tid = tx or f"{wallet}-{condition}-{ts}-{round(usd, -2)}"

        return {
            "id":           tid,
            "wallet":       wallet.lower(),
            "usd":          usd,
            "price_cents":  price * 100,
            "outcome":      outcome,
            "condition":    condition,
            "event_slug":   event_slug,
            "timestamp":    ts,
            "market_title": title or condition[:30] + "...",
            "market_url":   f"https://polymarket.com/event/{event_slug}" if event_slug else "https://polymarket.com",
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

    # Load persisted state
    known_wallets  = load_json(WALLETS_FILE, {})  # addr -> pnl
    log.info(f"Loaded {len(known_wallets)} tracked wallets")

    alerter        = Alerter()
    seen           = set()
    profile_cache  = {}
    market_cache   = {}
    wallets        = []
    wallet_idx     = 0
    last_refresh   = 0
    last_ts        = int(datetime.now(timezone.utc).timestamp()) - 60
    consensus_log: dict[str, list] = defaultdict(list)

    while True:
        try:
            now = int(datetime.now(timezone.utc).timestamp())

            # Refresh leaderboard + merge with known wallets
            if now - last_refresh > WALLET_REFRESH or not wallets:
                log.info("Refreshing leaderboard...")
                board = get_leaderboard(limit=TOP_WALLETS_COUNT)

                # Build fresh profile cache from leaderboard
                for e in board:
                    addr = (e.get("proxyWallet") or e.get("address", "")).lower()
                    if addr:
                        pnl = float(e.get("pnl", 0) or 0)
                        profile_cache[addr] = {"pnl": pnl}
                        known_wallets[addr] = pnl  # update known wallets

                # Merge leaderboard + known wallets into poll list
                leaderboard_addrs = set(profile_cache.keys())
                extra_addrs = set(known_wallets.keys()) - leaderboard_addrs
                wallets = list(leaderboard_addrs) + list(extra_addrs)

                # Make sure extra wallets have a profile entry
                for addr in extra_addrs:
                    if addr not in profile_cache:
                        profile_cache[addr] = {"pnl": known_wallets[addr]}

                last_refresh = now
                save_json(WALLETS_FILE, known_wallets)
                log.info(f"Monitoring {len(wallets)} wallets "
                         f"({len(leaderboard_addrs)} leaderboard + "
                         f"{len(extra_addrs)} auto-tracked)")

            if not wallets:
                time.sleep(60)
                continue

            batch       = wallets[wallet_idx: wallet_idx + BATCH_SIZE]
            wallet_idx  = (wallet_idx + BATCH_SIZE) % len(wallets)

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
                event_slug = trade["event_slug"]
                cache_key  = event_slug or trade["condition"]
                info       = {}

                if cache_key and cache_key not in market_cache:
                    if event_slug:
                        info = get_market_by_event_slug(event_slug) or {}
                    if not info and trade["condition"]:
                        info = get_market_by_condition(trade["condition"]) or {}
                    if info:
                        market_cache[cache_key] = info
                else:
                    info = market_cache.get(cache_key, {})

                gamma_title = info.get("question") or info.get("title") or ""
                if gamma_title and len(gamma_title) > len(trade["market_title"]):
                    trade["market_title"] = gamma_title

                volume_24h = 0.0
                try:
                    volume_24h = float(
                        info.get("volume24hr") or
                        info.get("volume_24hr") or
                        info.get("volumeNum") or 0
                    )
                except Exception:
                    pass

                price_after = 0.0
                try:
                    outcome_prices = info.get("outcomePrices")
                    if outcome_prices:
                        prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                        outcome_up = trade["outcome"].upper()
                        if outcome_up in ("YES", "NO"):
                            idx = 0 if outcome_up == "YES" else 1
                            if len(prices) > idx:
                                price_after = float(prices[idx]) * 100
                except Exception:
                    pass

                cid    = trade["condition"]
                cutoff = now - CONSENSUS_WINDOW
                consensus_log[cid] = [
                    (t, s, w) for t, s, w in consensus_log[cid]
                    if t > cutoff and w != trade["wallet"]
                ]
                same_side = sum(1 for t, s, w in consensus_log[cid]
                                if s == trade["outcome"])
                consensus_log[cid].append((now, trade["outcome"], trade["wallet"]))

                s = score(
                    usd               = trade["usd"],
                    price_cents       = trade["price_cents"],
                    pnl               = trade["pnl"],
                    volume_24h        = volume_24h,
                    price_after_cents = price_after,
                    side              = trade["outcome"],
                    same_side_whales  = same_side,
                )

                trade["volume_24h"]       = volume_24h
                trade["price_after"]      = price_after
                trade["same_side_whales"] = same_side

                alerter.send(trade, s)

                # Auto-grow: save wallet to known list
                wallet_addr = trade["wallet"]
                if wallet_addr not in known_wallets:
                    known_wallets[wallet_addr] = trade["pnl"]
                    log.info(f"Auto-tracked new wallet: {wallet_addr} (PnL: {trade['pnl']:,.0f})")
                    save_json(WALLETS_FILE, known_wallets)


            if wallet_idx < BATCH_SIZE:
                last_ts = now - 60

            if not new_whales:
                batch_num     = max(1, wallet_idx // BATCH_SIZE)
                total_batches = max(1, len(wallets) // BATCH_SIZE)
                log.info(f"No whale trades (batch {batch_num}/{total_batches})")

            # Cleanup
            if len(seen) > 20_000:
                seen = set(list(seen)[-5000:])
            if len(profile_cache) > 2000:
                profile_cache = {k: v for k, v in profile_cache.items()
                                 if k in known_wallets}
            if len(market_cache) > 500:
                market_cache.clear()
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
