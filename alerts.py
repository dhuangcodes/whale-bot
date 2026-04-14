import logging
import requests
from config import DISCORD_WEBHOOK_URL
from scorer import Score

log = logging.getLogger(__name__)

COLORS = {
    "STRONG SIGNAL": 0xFF4500,
    "DECENT SIGNAL": 0xFFD700,
    "MILD SIGNAL":   0x00BFFF,
    "INFORMATIONAL": 0x888888,
}

def _bar(n: int) -> str:
    return "█" * round(n/10) + "░" * (10 - round(n/10))

def _pnl(v: float) -> str:
    return f"+${v:,.0f}" if v >= 0 else f"-${abs(v):,.0f}"

def _short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr


class Alerter:
    def send(self, trade: dict, s: Score):
        if not DISCORD_WEBHOOK_URL:
            self._console(trade, s)
            return

        usd    = trade["usd"]
        side   = trade["outcome"]
        wallet = trade["wallet"]
        pnl    = trade["pnl"]
        side_e = "🟢" if "YES" in side.upper() else "🔴"

        # Volume context line
        vol = trade.get("volume_24h", 0)
        vol_str = f"${vol:,.0f} 24h vol" if vol > 0 else "volume unknown"

        # Price movement line
        pa = trade.get("price_after", 0)
        pc = trade["price_cents"]
        if pa > 0 and pc > 0:
            if "YES" in side.upper():
                diff = pa - pc
            else:
                diff = pc - pa
            move_str = f"{'▲' if diff > 0 else '▼'} {abs(diff):.1f}¢ after trade"
        else:
            move_str = "price data unavailable"

        # Consensus line
        sw = trade.get("same_side_whales", 0)
        cons_str = f"{sw + 1} whale{'s' if sw > 0 else ''} on this side" if sw > 0 else "first whale on this side"

        embed = {
            "title": f"{s.emoji} {s.label} — Polymarket Whale",
            "color": COLORS.get(s.label, 0x888888),
            "fields": [
                {"name": "📌 Market",
                 "value": trade["market_title"],
                 "inline": False},
                {"name": f"{side_e} Side & Price",
                 "value": f"**{side}** @ **{trade['price_cents']:.1f}¢**",
                 "inline": True},
                {"name": "💰 Size",
                 "value": f"**${usd:,.0f}**",
                 "inline": True},
                {"name": "👛 Wallet",
                 "value": f"`{_short(wallet)}`  |  All-time PnL: **{_pnl(pnl)}**",
                 "inline": False},
                {"name": "📊 Confidence Score",
                 "value": f"`{_bar(s.total)}` **{s.total}/100**\n{s.reason}",
                 "inline": False},
                {"name": "🔬 Breakdown",
                 "value": (
                     f"Credibility: `{s.credibility}/30` • "
                     f"Vol Share: `{s.dominance}/25` • "
                     f"Conviction: `{s.conviction}/20` • "
                     f"Mkt Move: `{s.price_move}/15` • "
                     f"Consensus: `{s.consensus}/10`"
                 ),
                 "inline": False},
                {"name": "📈 Context",
                 "value": f"{vol_str}  |  {move_str}  |  {cons_str}",
                 "inline": False},
                {"name": "🔗 Links",
                 "value": (f"[Market]({trade['market_url']}) • "
                           f"[Wallet](https://polymarket.com/profile/{wallet})"),
                 "inline": False},
            ],
            "footer": {"text": "Polymarket Whale Alert"},
        }

        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
            r.raise_for_status()
            log.info(f"✅ Alert: ${usd:,.0f} {side} @ {trade['price_cents']:.1f}¢ "
                     f"[{s.total}/100] — {trade['market_title'][:50]}")
        except Exception as e:
            log.error(f"Discord failed: {e}")
            self._console(trade, s)

    def _console(self, trade: dict, s: Score):
        print(f"\n{'='*60}")
        print(f"{s.emoji} {s.label} [{s.total}/100]")
        print(f"Market : {trade['market_title']}")
        print(f"Side   : {trade['outcome']} @ {trade['price_cents']:.1f}¢")
        print(f"Size   : ${trade['usd']:,.0f}")
        print(f"Wallet : {_short(trade['wallet'])} | {_pnl(trade['pnl'])}")
        print(f"Reason : {s.reason}")
        print(f"Link   : {trade['market_url']}")
        print(f"{'='*60}\n")
