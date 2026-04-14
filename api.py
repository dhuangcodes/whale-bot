"""
All Polymarket API calls in one place.
Uses only public, no-auth endpoints:
  - data-api.polymarket.com  (profiles, leaderboard, activity)
  - gamma-api.polymarket.com (market titles/slugs)
"""
import logging
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)
DATA  = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def _get(url: str, params: dict = {}, retries: int = 3):
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=12)
            log.debug(f"GET {url} {params} -> {r.status_code}")
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            code = e.response.status_code
            log.warning(f"HTTP {code} for {url} params={params}: {e.response.text[:100]}")
            if code == 429:
                time.sleep(2 ** i)
            elif code in (400, 404):
                return None
            else:
                if i == retries - 1:
                    return None
                time.sleep(1)
        except Exception as e:
            log.warning(f"Request error {url}: {e}")
            if i == retries - 1:
                return None
            time.sleep(1)
    return None


def get_leaderboard(limit: int = 300) -> list[dict]:
    """
    Returns top traders sorted by all-time PnL.
    Response shape: {"leaderboard": [...]}
    Each entry has: proxyWallet, pnl, vol, name
    """
    data = _get(f"{GAMMA}/leaderboard", {"limit": limit})
    
    if data is None:
        log.error("Leaderboard returned None")
        return []

    log.debug(f"Leaderboard raw type={type(data)} keys={list(data.keys()) if isinstance(data, dict) else 'list'}")

    # Response is {"leaderboard": [...]}
    if isinstance(data, dict):
        for key in ("leaderboard", "data", "results", "traders"):
            entries = data.get(key)
            if entries and isinstance(entries, list):
                log.info(f"Leaderboard: {len(entries)} entries (key={key})")
                return entries
        # Maybe the dict itself is one entry — shouldn't happen but handle it
        log.error(f"Leaderboard dict had no known key. Keys: {list(data.keys())}")
        return []

    if isinstance(data, list):
        log.info(f"Leaderboard: {len(data)} entries (direct list)")
        return data

    log.error(f"Unexpected leaderboard response type: {type(data)}")
    return []


def get_wallet_activity(address: str, limit: int = 20) -> list[dict]:
    """
    Returns recent trades for a wallet.
    Each entry has: proxyWallet, side, size, price, usdcSize,
                    conditionId, title, slug, eventSlug, outcome,
                    transactionHash, timestamp
    """
    data = _get(f"{DATA}/activity", {
        "user": address,
        "type": "TRADE",
        "limit": limit,
    })
    if isinstance(data, list):
        return data
    return []


def get_wallet_profile(address: str) -> dict:
    """Returns pnl, win_rate, trades_count etc for a wallet."""
    data = _get(f"{DATA}/profile", {"user": address})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def get_market_by_condition(condition_id: str) -> dict:
    """Returns market title, slug from Gamma API."""
    data = _get(f"{GAMMA}/markets", {"id": condition_id})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data.get("markets", [{}])[0] if data.get("markets") else data
    return {}


def batch_get_activity(wallets: list[str], limit: int = 10) -> dict[str, list]:
    """Fetch activity for multiple wallets in parallel."""
    results = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(get_wallet_activity, w, limit): w for w in wallets}
        for future in as_completed(futures):
            wallet = futures[future]
            try:
                results[wallet] = future.result() or []
            except Exception:
                results[wallet] = []
    return results
