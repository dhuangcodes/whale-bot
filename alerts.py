import logging
import requests
from datetime import datetime, timezone, timedelta
from config import WEBHOOK_NBA, WEBHOOK_WORLDCUP
from scorer import Score

log = logging.getLogger(__name__)

COLORS = {
    "STRONG SIGNAL": 0xFF4500,
    "DECENT SIGNAL": 0xFFD700,
    "MILD SIGNAL":   0x00BFFF,
    "INFORMATIONAL": 0x888888,
}

# ── Sport classifiers ────────────────────────────────────────────────────────

NHL_TEAMS = [
    "avalanche", "bruins", "sabres", "flames", "hurricanes", "blackhawks",
    "blue jackets", "stars", "red wings", "oilers", "panthers", "wild",
    "canadiens", "predators", "devils", "islanders", "rangers", "senators",
    "flyers", "penguins", "sharks", "kraken", "blues", "lightning",
    "maple leafs", "canucks", "golden knights", "capitals", "jets",
    "coyotes", "ducks", "nhl", "stanley cup", "puck", "power play"
]

NBA_TEAMS = [
    "hawks", "celtics", "nets", "hornets", "bulls", "cavaliers", "cavs",
    "mavericks", "mavs", "nuggets", "pistons", "warriors", "rockets",
    "pacers", "clippers", "lakers", "grizzlies", "heat", "bucks",
    "timberwolves", "wolves", "pelicans", "knicks", "thunder", "magic",
    "76ers", "sixers", "suns", "trail blazers", "blazers", "kings",
    "spurs", "raptors", "jazz", "wizards", "nba finals", "nba playoffs",
    "nba championship"
]

# World Cup 2026 — all 48 qualified nations + tournament keywords
WORLDCUP_KEYWORDS = [
    # Tournament keywords
    "world cup", "fifa", "worldcup", "2026 world cup", "world cup 2026",
    "group stage", "round of 32", "round of 16", "quarterfinal",
    "semifinal", "semi-final", "wc 2026",
    # Nations
    "argentina", "brazil", "france", "england", "germany", "spain",
    "portugal", "netherlands", "belgium", "italy", "croatia", "denmark",
    "switzerland", "uruguay", "mexico", "usa", "united states", "canada",
    "australia", "japan", "south korea", "morocco", "senegal", "nigeria",
    "ghana", "cameroon", "ecuador", "colombia", "chile", "peru",
    "venezuela", "paraguay", "bolivia", "saudi arabia", "iran", "qatar",
    "south africa", "egypt", "algeria", "tunisia", "mali", "ivory coast",
    "new zealand", "austria", "poland", "czech republic", "hungary",
    "slovakia", "serbia", "ukraine", "turkey", "scotland", "wales",
    "ireland", "norway", "sweden", "finland", "greece", "romania",
    "panama", "costa rica", "honduras", "jamaica", "el salvador",
    "cuba", "haiti", "new caledonia", "fiji", "indonesia", "iraq",
    "uzbekistan", "bahrain", "jordan", "oman", "palestine",
    "democratic republic of congo", "zimbabwe", "zambia", "tanzania",
    "guinea", "angola", "cabo verde"
]


# Things to completely ignore — don't post to any channel
IGNORE_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
    "up or down", "price up", "price down", "binance", "coinbase",
    "xai", "openai", "anthropic", "best ai", "ai model", "gpt",
    "counter-strike", "cs2", "csgo", "valorant", "dota", "esport",
    "iem", "blast", "pgl", "esl", "major stage",
    "corners", "yellow card", "red card", "total goals", "both teams to score",
    "first goalscorer", "anytime scorer", "half time",
]


def _get_webhook(title: str) -> str:
    t = title.lower()
    # Hard ignore — crypto, AI, esports, prop bets
    if any(kw in t for kw in IGNORE_KEYWORDS):
        return ""
    # NHL — ignore
    if any(kw in t for kw in NHL_TEAMS):
        return ""
    # NBA
    if any(kw in t for kw in NBA_TEAMS):
        return WEBHOOK_NBA
    # World Cup
    if any(kw in t for kw in WORLDCUP_KEYWORDS):
        return WEBHOOK_WORLDCUP
    # Everything else — ignore
    return ""


def _route_name(title: str) -> str:
    t = title.lower()
    if any(kw in t for kw in NBA_TEAMS):
        return "NBA"
    if any(kw in t for kw in WORLDCUP_KEYWORDS):
        return "WORLDCUP"
    return "OTHER"


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


class Alerter:
    def __init__(self):
        pass

    def send(self, trade: dict, s: Score):
        webhook = _get_webhook(trade["market_title"])
        if not webhook:
            self._console(trade, s)
            return

        embed = self._build_embed(trade, s)
        try:
            r = requests.post(webhook, json={"embeds": [embed]}, timeout=5)
            r.raise_for_status()
            log.info(f"✅ [{_route_name(trade['market_title'])}] "
                     f"${trade['usd']:,.0f} {trade['outcome']} "
                     f"@ {trade['price_cents']:.1f}¢ [{s.total}/100] "
                     f"— {trade['market_title'][:50]}")
        except Exception as e:
            log.error(f"Discord failed: {e}")
            self._console(trade, s)

    def _build_embed(self, trade: dict, s: Score) -> dict:
        usd    = trade["usd"]
        side   = trade["outcome"]
        wallet = trade["wallet"]
        pnl    = trade["pnl"]
        side_e = "🟢" if side not in ("NO", "UNDER") else "🔴"
        route  = _route_name(trade["market_title"])

        pa = trade.get("price_after", 0)
        pc = trade["price_cents"]
        if pa > 0 and pc > 0:
            diff = (pa - pc) if side == "YES" else (pc - pa)
            move_str = f"{'▲' if diff > 0 else '▼'} {abs(diff):.1f}¢ after trade"
        else:
            move_str = "price data unavailable"

        sw       = trade.get("same_side_whales", 0)
        cons_str = f"{sw + 1} whales on this side" if sw > 0 else "first whale on this side"

        # Label differs by sport
        sport_label = "⚽ World Cup Whale" if route in ("WORLDCUP", "OTHER", "NHL") else "🏀 Polymarket Whale"

        return {
            "title": f"{s.emoji} {s.label} — {sport_label}",
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
