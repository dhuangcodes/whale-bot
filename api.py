"""
All Polymarket API calls in one place.
Uses only public, no-auth endpoints:
  - data-api.polymarket.com  (profiles, leaderboard, activity)
  - gamma-api.polymarket.com (market titles/slugs/volume)
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
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            code = e.response.status_code
            if code == 429:
                time.sleep(2 ** i)
            elif code in (400, 404):
                return None
            else:
                if i == retries - 1:
                    return None
                time.sleep(1)
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(1)
    return None


def get_leaderboard(limit: int = 300) -> list[dict]:
    data = _get(f"{DATA}/v1/leaderboard", {"limit": limit})
    if data is None:
        log.error("Leaderboard returned None")
        return []
    if isinstance(data, list):
        log.info(f"Leaderboard: {len(data)} wallets loaded")
        return data
    if isinstance(data, dict):
        for key in ("leaderboard", "data", "results", "traders"):
            entries = data.get(key)
            if entries and isinstance(entries, list):
                log.info(f"Leaderboard: {len(entries)} wallets loaded")
                return entries
    log.error(f"Unexpected leaderboard response: {str(data)[:100]}")
    return []


def get_wallet_activity(address: str, limit: int = 20) -> list[dict]:
    data = _get(f"{DATA}/activity", {
        "user": address,
        "type": "TRADE",
        "limit": limit,
    })
    if isinstance(data, list):
        return data
    return []


def get_wallet_profile(address: str) -> dict:
    data = _get(f"{DATA}/profile", {"user": address})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def get_market_by_slug(slug: str) -> dict:
    """Fetch market info by slug — most reliable method."""
    data = _get(f"{GAMMA}/markets", {"slug": slug})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data.get("markets"):
        return data["markets"][0]
    return {}


def get_market_by_condition(condition_id: str) -> dict:
    """Fetch market info by conditionId."""
    for param in [
        {"id": condition_id},
        {"condition_id": condition_id},
    ]:
        data = _get(f"{GAMMA}/markets", param)
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict) and data.get("markets"):
            return data["markets"][0]
    return {}


def get_market_by_event_slug(event_slug: str) -> dict:
    """Fetch market info by eventSlug — often more reliable than conditionId."""
    data = _get(f"{GAMMA}/events", {"slug": event_slug})
    if isinstance(data, list) and data:
        event = data[0]
        markets = event.get("markets", [])
        if markets:
            # Return the event-level volume which is more accurate
            result = markets[0].copy()
            result["volume24hr"] = float(event.get("volume24hr") or event.get("volume") or 0)
            result["question"] = event.get("title") or result.get("question", "")
            return result
    if isinstance(data, dict):
        markets = data.get("markets", [])
        if markets:
            result = markets[0].copy()
            result["volume24hr"] = float(data.get("volume24hr") or data.get("volume") or 0)
            result["question"] = data.get("title") or result.get("question", "")
            return result
    return {}


def batch_get_activity(wallets: list[str], limit: int = 10) -> dict[str, list]:
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
