import re
import logging
import requests
from datetime import datetime, timezone, timedelta
from config import DISCORD_WEBHOOK_URL, DISCORD_BOT_AUTH as BOT_TOKEN
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

def _market_key(title: str) -> str:
    return re.split(r'[:\|]', title)[0].strip() or title



def _format_est(ts: int) -> str:
    if not ts:
        return "unknown"
    est = timezone(timedelta(hours=-5))
    dt = datetime.fromtimestamp(ts, tz=est)
    return dt.strftime("%b %d %I:%M %p EST")

class Alerter:
    def __init__(self):
        self.active_threads: dict[str, str] = {}
        self._channel_id: str | None = None

    def _get_channel_id(self) -> str | None:
        if self._channel_id:
            return self._channel_id
        try:
            r = requests.get(DISCORD_WEBHOOK_URL, timeout=5)
            r.raise_for_status()
            data = r.json()
            self._channel_id = str(data.get("channel_id", ""))
            log.info(f"Got channel_id: {self._channel_id}")
            return self._channel_id
        except Exception as e:
            log.error(f"Could not fetch channel ID: {e}")
            return None

    def _bot_headers(self) -> dict:
        return {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}

    def _post_to_channel(self, embed: dict) -> str | None:
        try:
            r = requests.post(
                DISCORD_WEBHOOK_URL,
                json={"embeds": [embed]},
                params={"wait": "true"},
                timeout=5
            )
            r.raise_for_status()
            msg_id = str(r.json().get("id", ""))
            log.info(f"Posted to channel, msg_id={msg_id}")
            return msg_id
        except Exception as e:
            log.error(f"Failed to post to channel: {e}")
            return None

    def _create_thread(self, message_id: str, thread_name: str) -> str | None:
        if not BOT_TOKEN:
            log.error("BOT_TOKEN (DISCORD_BOT_AUTH) is empty — cannot create threads")
            return None

        channel_id = self._get_channel_id()
        if not channel_id:
            log.error("No channel_id — cannot create thread")
            return None

        log.info(f"Creating thread '{thread_name}' on msg {message_id} in channel {channel_id}")
        try:
            r = requests.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/threads",
                headers=self._bot_headers(),
                json={
                    "name": thread_name[:100],
                    "auto_archive_duration": 1440,
                },
                timeout=5
            )
            log.info(f"Thread creation response: {r.status_code} {r.text[:200]}")
            r.raise_for_status()
            thread_id = str(r.json().get("id", ""))
            log.info(f"✅ Created thread '{thread_name}' id={thread_id}")
            return thread_id
        except Exception as e:
            log.error(f"Thread creation failed: {e}")
            return None

    def _post_to_thread(self, thread_id: str, embed: dict) -> bool:
        try:
            r = requests.post(
                DISCORD_WEBHOOK_URL,
                json={"embeds": [embed]},
                params={"thread_id": thread_id},
                timeout=5
            )
            if r.status_code == 404:
                log.warning(f"Thread {thread_id} not found (404)")
                return False
            r.raise_for_status()
            log.info(f"Posted to thread {thread_id}")
            return True
        except Exception as e:
            log.warning(f"Failed to post to thread {thread_id}: {e}")
            return False

    def send(self, trade: dict, s: Score):
        if not DISCORD_WEBHOOK_URL:
            self._console(trade, s)
            return

        embed = self._build_embed(trade, s)
        market_key = _market_key(trade["market_title"])
        thread_id = self.active_threads.get(market_key)

        log.info(f"Sending alert for '{market_key}' (thread_id={thread_id})")

        if thread_id:
            success = self._post_to_thread(thread_id, embed)
            if not success:
                del self.active_threads[market_key]
                thread_id = None

        if not thread_id:
            msg_id = self._post_to_channel(embed)
            if msg_id:
                new_thread_id = self._create_thread(msg_id, f"🐋 {market_key}")
                if new_thread_id:
                    self.active_threads[market_key] = new_thread_id
                    log.info(f"Stored thread for '{market_key}'")

    def _build_embed(self, trade: dict, s: Score) -> dict:
        usd    = trade["usd"]
        side   = trade["outcome"]
        wallet = trade["wallet"]
        pnl    = trade["pnl"]
        side_e = "🟢" if "YES" in side.upper() else "🔴"

        vol = trade.get("volume_24h", 0)
        vol_str = f"${vol:,.0f} 24h vol" if vol > 0 else "volume unknown"

        pa = trade.get("price_after", 0)
        pc = trade["price_cents"]
        if pa > 0 and pc > 0:
            diff = (pa - pc) if "YES" in side.upper() else (pc - pa)
            move_str = f"{'▲' if diff > 0 else '▼'} {abs(diff):.1f}¢ after trade"
        else:
            move_str = "price data unavailable"

        sw = trade.get("same_side_whales", 0)
        cons_str = f"{sw + 1} whales on this side" if sw > 0 else "first whale on this side"

        return {
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
        print(f"Link   : {trade['market_url']}")
        print(f"{'='*60}\n")
