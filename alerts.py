import re
import logging
import requests
from datetime import datetime, timezone, timedelta
from config import (WEBHOOK_NBA, WEBHOOK_MLB, WEBHOOK_TENNIS,
                    WEBHOOK_VIDEOGAMES, WEBHOOK_OTHER, DISCORD_BOT_AUTH)
from scorer import Score

log = logging.getLogger(__name__)

COLORS = {
    "STRONG SIGNAL": 0xFF4500,
    "DECENT SIGNAL": 0xFFD700,
    "MILD SIGNAL":   0x00BFFF,
    "INFORMATIONAL": 0x888888,
}

NBA_TEAMS = [
    "hawks", "celtics", "nets", "hornets", "bulls", "cavaliers", "mavericks",
    "nuggets", "pistons", "warriors", "rockets", "pacers", "clippers", "lakers",
    "grizzlies", "heat", "bucks", "timberwolves", "pelicans", "knicks", "thunder",
    "magic", "76ers", "suns", "trail blazers", "blazers", "kings", "spurs",
    "raptors", "jazz", "wizards", "nba", "spread", "o/u"
]
MLB_TEAMS = [
    "yankees", "red sox", "dodgers", "giants", "cubs", "white sox", "reds",
    "guardians", "rockies", "tigers", "astros", "royals", "angels", "marlins",
    "brewers", "twins", "mets", "phillies", "pirates", "padres", "cardinals",
    "rays", "rangers", "blue jays", "nationals", "orioles", "athletics",
    "mariners", "braves", "mlb"
]
TENNIS_KEYWORDS = [
    "atp", "wta", "wimbledon", "roland garros", "us open", "australian open",
    "challenger", "wuning", "tennis", "grand slam"
]
VIDEOGAME_KEYWORDS = [
    "cs2", "csgo", "valorant", "league of legends", "lol", "dota", "fortnite",
    "overwatch", "call of duty", "cod", "navi", "natus vincere", "faze",
    "vitality", "astralis", "g2", "fnatic", "team liquid", "esport",
    "blast", "pgl", "iem", "esl"
]


def _get_webhook(title: str) -> str:
    t = title.lower()
    for kw in NBA_TEAMS:
        if kw in t: return WEBHOOK_NBA
    for kw in MLB_TEAMS:
        if kw in t: return WEBHOOK_MLB
    for kw in VIDEOGAME_KEYWORDS:
        if kw in t: return WEBHOOK_VIDEOGAMES
    for kw in TENNIS_KEYWORDS:
        if kw in t: return WEBHOOK_TENNIS
    return WEBHOOK_OTHER


def _is_nba(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in NBA_TEAMS)


def _market_key(title: str) -> str:
    """Strip O/U lines so related markets group into same thread."""
    return re.split(r'[:\|]', title)[0].strip() or title


def _bar(n: int) -> str:
    return "█" * round(n / 10) + "░" * (10 - round(n / 10))

def _pnl(v: float) -> str:
    return f"+${v:,.0f}" if v >= 0 else f"-${abs(v):,.0f}"

def _short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr

def _format_est(ts: int) -> str:
    if not ts: return "unknown"
    est = timezone(timedelta(hours=-5))
    dt = datetime.fromtimestamp(ts, tz=est)
    return dt.strftime("%b %d %I:%M %p EST")

def _route_name(title: str) -> str:
    t = title.lower()
    for kw in NBA_TEAMS:
        if kw in t: return "NBA"
    for kw in MLB_TEAMS:
        if kw in t: return "MLB"
    for kw in VIDEOGAME_KEYWORDS:
        if kw in t: return "GAMES"
    for kw in TENNIS_KEYWORDS:
        if kw in t: return "TENNIS"
    return "OTHER"


class Alerter:
    def __init__(self, active_threads: dict):
        # active_threads is passed in from main so it persists across calls
        self.active_threads = active_threads
        self._channel_ids: dict[str, str] = {}  # webhook_url -> channel_id

    def _get_channel_id(self, webhook_url: str) -> str | None:
        if webhook_url in self._channel_ids:
            return self._channel_ids[webhook_url]
        try:
            r = requests.get(webhook_url, timeout=5)
            r.raise_for_status()
            cid = str(r.json().get("channel_id", ""))
            if cid:
                self._channel_ids[webhook_url] = cid
            return cid
        except Exception as e:
            log.warning(f"Could not fetch channel ID: {e}")
            return None

    def _bot_headers(self) -> dict:
        return {"Authorization": f"Bot {DISCORD_BOT_AUTH}",
                "Content-Type": "application/json"}

    def _post_to_channel(self, webhook_url: str, embed: dict) -> str | None:
        try:
            r = requests.post(
                webhook_url,
                json={"embeds": [embed]},
                params={"wait": "true"},
                timeout=5
            )
            r.raise_for_status()
            return str(r.json().get("id", ""))
        except Exception as e:
            log.error(f"Failed to post to channel: {e}")
            return None

    def _create_thread(self, webhook_url: str, message_id: str,
                       thread_name: str) -> str | None:
        if not DISCORD_BOT_AUTH:
            return None
        channel_id = self._get_channel_id(webhook_url)
        if not channel_id:
            return None
        try:
            r = requests.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/threads",
                headers=self._bot_headers(),
                json={"name": thread_name[:100], "auto_archive_duration": 1440},
                timeout=5
            )
            r.raise_for_status()
            tid = str(r.json().get("id", ""))
            log.info(f"Created thread '{thread_name}' id={tid}")
            return tid
        except Exception as e:
            log.warning(f"Thread creation failed: {e}")
            return None

    def _post_to_thread(self, webhook_url: str, thread_id: str,
                        embed: dict) -> bool:
        try:
            r = requests.post(
                webhook_url,
                json={"embeds": [embed]},
                params={"thread_id": thread_id},
                timeout=5
            )
            if r.status_code == 404:
                return False
            r.raise_for_status()
            return True
        except Exception as e:
            log.warning(f"Failed to post to thread {thread_id}: {e}")
            return False

    def send(self, trade: dict, s: Score) -> bool:
        """Returns True if a new thread was created (so caller can save)."""
        webhook = _get_webhook(trade["market_title"])
        if not webhook:
            self._console(trade, s)
            return False

        embed       = self._build_embed(trade, s)
        use_threads = _is_nba(trade["market_title"])
        new_thread  = False

        if use_threads:
            market_key = _market_key(trade["market_title"])
            thread_id  = self.active_threads.get(market_key)

            if thread_id:
                success = self._post_to_thread(webhook, thread_id, embed)
                if not success:
                    del self.active_threads[market_key]
                    thread_id = None

            if not thread_id:
                msg_id = self._post_to_channel(webhook, embed)
                if msg_id:
                    new_tid = self._create_thread(webhook, msg_id,
                                                  f"🐋 {market_key}")
                    if new_tid:
                        self.active_threads[market_key] = new_tid
                        new_thread = True
        else:
            # Non-NBA: just post directly, no threads
            try:
                r = requests.post(webhook, json={"embeds": [embed]}, timeout=5)
                r.raise_for_status()
            except Exception as e:
                log.error(f"Discord failed: {e}")
                self._console(trade, s)

        log.info(f"✅ [{_route_name(trade['market_title'])}] "
                 f"${trade['usd']:,.0f} {trade['outcome']} "
                 f"@ {trade['price_cents']:.1f}¢ [{s.total}/100] "
                 f"— {trade['market_title'][:50]}")
        return new_thread

    def _build_embed(self, trade: dict, s: Score) -> dict:
        usd    = trade["usd"]
        side   = trade["outcome"]
        wallet = trade["wallet"]
        pnl    = trade["pnl"]
        side_e = "🟢" if side not in ("NO", "UNDER") else "🔴"

        pa = trade.get("price_after", 0)
        pc = trade["price_cents"]
        if pa > 0 and pc > 0:
            diff = (pa - pc) if side in ("YES",) else (pc - pa)
            move_str = f"{'▲' if diff > 0 else '▼'} {abs(diff):.1f}¢ after trade"
        else:
            move_str = "price data unavailable"

        sw       = trade.get("same_side_whales", 0)
        cons_str = f"{sw + 1} whales on this side" if sw > 0 else "first whale on this side"

        return {
            "title": f"{s.emoji} {s.label} — Polymarket Whale",
            "color": COLORS.get(s.label, 0x888888),
            "fields": [
                {"name": "📌 Market",
                 "value": trade["market_title"], "inline": False},
                {"name": f"{side_e} Side & Price",
                 "value": f"**{side}** @ **{trade['price_cents']:.1f}¢**",
                 "inline": True},
                {"name": "💰 Size",
                 "value": f"**${usd:,.0f}**", "inline": True},
                {"name": "👛 Wallet",
                 "value": f"`{_short(wallet)}`  |  All-time PnL: **{_pnl(pnl)}**",
                 "inline": False},
                {"name": "📊 Confidence Score",
                 "value": f"`{_bar(s.total)}` **{s.total}/100**\n{s.reason}",
                 "inline": False},
                {"name": "🔬 Breakdown",
                 "value": (f"Credibility: `{s.credibility}/50` • "
                           f"Consensus: `{s.consensus}/30` • "
                           f"Conviction: `{s.conviction}/20` • "
                           f"Mkt Move: `{s.price_move:+d}`"),
                 "inline": False},
                {"name": "📈 Context",
                 "value": f"{move_str}  |  {cons_str}", "inline": False},
                {"name": "🔗 Links",
                 "value": (f"[Market]({trade['market_url']}) • "
                           f"[Wallet](https://polymarket.com/profile/{wallet})"),
                 "inline": False},
            ],
            "footer": {"text": f"Polymarket Whale Alert  •  Trade placed: {_format_est(trade.get('timestamp', 0))}"},
        }

    def _console(self, trade: dict, s: Score):
        print(f"\n{'='*60}")
        print(f"{s.emoji} {s.label} [{s.total}/100]")
        print(f"Market : {trade['market_title']}")
        print(f"Side   : {trade['outcome']} @ {trade['price_cents']:.1f}¢")
        print(f"Size   : ${trade['usd']:,.0f}")
        print(f"Wallet : {_short(trade['wallet'])} | {_pnl(trade['pnl'])}")
        print(f"Reason : {s.reason}")
        print(f"{'='*60}\n")
