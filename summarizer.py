"""
Game Summary Aggregator for Polymarket Whale Bot
- Collects all alerts grouped by game
- Posts clean pre-game summaries to a dedicated Discord channel
- Auto-posts 30 min before tip-off for known NBA schedule
- Also triggered via !summary command in Discord
"""

import re
import requests
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# ── Routing helpers (mirrors alerts.py) ─────────────────────────────────────

NHL_TEAMS = [
    "avalanche", "bruins", "sabres", "flames", "hurricanes", "blackhawks",
    "avalanche", "blue jackets", "stars", "red wings", "oilers", "panthers",
    "kings", "wild", "canadiens", "predators", "devils", "islanders",
    "rangers", "senators", "flyers", "penguins", "sharks", "kraken",
    "blues", "lightning", "maple leafs", "canucks", "golden knights",
    "capitals", "jets", "coyotes", "ducks", "nhl"
]

NBA_TEAMS = [
    "hawks", "celtics", "nets", "hornets", "bulls", "cavaliers", "cavs",
    "mavericks", "mavs", "nuggets", "pistons", "warriors", "rockets",
    "pacers", "clippers", "lakers", "grizzlies", "heat", "bucks",
    "timberwolves", "wolves", "pelicans", "knicks", "thunder", "magic",
    "76ers", "sixers", "suns", "trail blazers", "blazers", "kings",
    "spurs", "raptors", "jazz", "wizards"
]


def _is_nba(title: str) -> bool:
    t = title.lower()
    # NHL check first — kings/avalanche etc
    if any(kw in t for kw in NHL_TEAMS):
        return False
    return any(kw in t for kw in NBA_TEAMS)


def _extract_game_key(title: str) -> str | None:
    """Extract a normalized game key from market title e.g. 'Knicks vs Hawks'"""
    if not _is_nba(title):
        return None
    t = title.lower()

    # Strip common suffixes
    for suffix in [": o/u", " o/u", ": spread", " spread", ": 1h", " 1h",
                   " moneyline", ": moneyline", " series", " finals",
                   " nba finals", " playoffs"]:
        t = t.replace(suffix, "")

    # Try to find "Team vs Team" pattern
    vs_match = re.search(
        r'([\w\s]+?)\s+vs\.?\s+([\w\s]+?)(?:\s*[:\-\|]|$)', t
    )
    if vs_match:
        team1 = vs_match.group(1).strip()
        team2 = vs_match.group(2).strip()
        # Normalize — sort alphabetically so "Hawks vs Knicks" == "Knicks vs Hawks"
        teams = sorted([team1, team2])
        return f"{teams[0].title()} vs {teams[1].title()}"

    # Try to find a single NBA team name (e.g. "Will the Lakers win the Finals?")
    for team in NBA_TEAMS:
        if team in t:
            return f"{team.title()} (futures)"

    return "Other NBA"


# ── Alert store ──────────────────────────────────────────────────────────────

class GameSummaryStore:
    """
    Stores whale alerts grouped by game.
    Call .add_alert() every time an alert fires.
    Call .get_summary(game_key) to get a formatted summary.
    Call .get_all_games() to list active games.
    """

    def __init__(self, ttl_hours: int = 20):
        # game_key -> list of alert dicts
        self._alerts: dict[str, list] = defaultdict(list)
        self._ttl = ttl_hours * 3600

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def _purge_old(self):
        cutoff = self._now() - self._ttl
        for key in list(self._alerts.keys()):
            self._alerts[key] = [
                a for a in self._alerts[key] if a["ts"] > cutoff
            ]
            if not self._alerts[key]:
                del self._alerts[key]

    def add_alert(self, title: str, side: str, price_cents: float,
                  usd: float, wallet: str, pnl: float, score_total: int,
                  score_label: str, ts: int):
        game_key = _extract_game_key(title)
        if not game_key:
            return
        self._purge_old()
        self._alerts[game_key].append({
            "title":       title,
            "side":        side,
            "price_cents": price_cents,
            "usd":         usd,
            "wallet":      wallet,
            "pnl":         pnl,
            "score":       score_total,
            "label":       score_label,
            "ts":          ts,
        })

    def get_all_games(self) -> list[str]:
        self._purge_old()
        return sorted(self._alerts.keys())

    def get_summary(self, game_key: str) -> str | None:
        self._purge_old()
        alerts = self._alerts.get(game_key)
        if not alerts:
            return None

        # Group by side
        sides: dict[str, list] = defaultdict(list)
        for a in alerts:
            sides[a["side"]].append(a)

        lines = [f"**📊 Whale Summary — {game_key}**",
                 f"*{len(alerts)} alerts across {len(sides)} sides*\n"]

        # Sort sides by total USD descending
        for side, side_alerts in sorted(
            sides.items(), key=lambda x: sum(a["usd"] for a in x[1]), reverse=True
        ):
            total_usd   = sum(a["usd"] for a in side_alerts)
            wallets     = {a["wallet"] for a in side_alerts}
            top_pnl     = max(a["pnl"] for a in side_alerts)
            avg_price   = sum(a["price_cents"] for a in side_alerts) / len(side_alerts)
            best_score  = max(a["score"] for a in side_alerts)
            emoji       = "🟢" if "YES" in side or any(
                t in side.upper() for t in NBA_TEAMS
            ) else "🔴"

            lines.append(
                f"{emoji} **{side}** @ avg {avg_price:.1f}¢\n"
                f"  💰 Total: **${total_usd:,.0f}** | "
                f"👛 {len(wallets)} wallet(s) | "
                f"🏆 Best PnL: +${top_pnl:,.0f} | "
                f"📊 Best score: {best_score}/100"
            )

            # Show top 3 individual bets by USD
            top_bets = sorted(side_alerts, key=lambda x: x["usd"], reverse=True)[:3]
            for b in top_bets:
                lines.append(
                    f"    • ${b['usd']:,.0f} @ {b['price_cents']:.1f}¢ "
                    f"[{b['wallet'][:10]}… +${b['pnl']:,.0f} PnL] "
                    f"({b['score']}/100)"
                )
            lines.append("")

        # Overall lean
        totals = {
            side: sum(a["usd"] for a in side_alerts)
            for side, side_alerts in sides.items()
        }
        top_side = max(totals, key=totals.get)
        lines.append(f"**Lean: {top_side}** (${totals[top_side]:,.0f} dominant)")

        return "\n".join(lines)

    def get_all_summaries_text(self) -> str:
        """Get all active game summaries as one block of text."""
        games = self.get_all_games()
        if not games:
            return "No active game data yet."
        parts = []
        for game in games:
            summary = self.get_summary(game)
            if summary:
                parts.append(summary)
                parts.append("─" * 40)
        return "\n".join(parts)


# ── Discord command listener ─────────────────────────────────────────────────

def post_summary_to_discord(webhook_url: str, text: str):
    """Post a summary as a Discord message (split if too long)."""
    chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
    for chunk in chunks:
        try:
            r = requests.post(
                webhook_url,
                json={"content": f"```\n{chunk}\n```"},
                timeout=5
            )
            r.raise_for_status()
        except Exception as e:
            log.error(f"Failed to post summary: {e}")


def listen_for_commands(bot_token: str, channel_id: str,
                        store: GameSummaryStore,
                        summary_webhook: str,
                        poll_interval: int = 10):
    """
    Poll a Discord channel for !summary commands.
    Requires a bot token with read message permissions.
    """
    last_message_id = None
    headers = {"Authorization": f"Bot {bot_token}"}

    while True:
        try:
            params = {"limit": 5}
            if last_message_id:
                params["after"] = last_message_id

            r = requests.get(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=headers,
                params=params,
                timeout=5
            )
            if r.status_code == 200:
                messages = r.json()
                for msg in reversed(messages):
                    mid = msg.get("id")
                    if mid and (not last_message_id or mid > last_message_id):
                        last_message_id = mid
                    content = msg.get("content", "").strip().lower()
                    if content.startswith("!summary"):
                        parts = content.split()
                        if len(parts) > 1:
                            # !summary knicks — find matching game
                            query = " ".join(parts[1:]).lower()
                            games  = store.get_all_games()
                            match  = next(
                                (g for g in games if query in g.lower()), None
                            )
                            if match:
                                text = store.get_summary(match)
                            else:
                                text = f"No data for '{query}'. Active: {', '.join(games) or 'none'}"
                        else:
                            # !summary — all games
                            text = store.get_all_summaries_text()
                        post_summary_to_discord(summary_webhook, text)
        except Exception as e:
            log.error(f"Command listener error: {e}")

        import time
        time.sleep(poll_interval)
