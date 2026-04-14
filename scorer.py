"""
Whale trade confidence scorer (0-100).

Factors:
  1. Wallet credibility (all-time PnL)  — 35 pts
  2. Trade size                          — 25 pts
  3. Price conviction zone               — 20 pts
  4. Wallet win rate                     — 20 pts
"""
from dataclasses import dataclass


@dataclass
class Score:
    total: int
    credibility: int
    size: int
    conviction: int
    winrate: int
    label: str
    emoji: str
    reason: str


def score(usd: float, price_cents: float, pnl: float,
          win_rate: float, n_trades: int) -> Score:

    # 1. Credibility (35)
    trade_factor = min(n_trades / 20, 1.0)
    if pnl >= 500_000:   raw = 35
    elif pnl >= 100_000: raw = 28
    elif pnl >= 10_000:  raw = 18
    elif pnl >= 0:       raw = 8
    else:                raw = 0
    cred = round(raw * trade_factor)

    # 2. Size (25)
    if usd >= 50_000:    sz = 25
    elif usd >= 25_000:  sz = 20
    elif usd >= 10_000:  sz = 14
    elif usd >= 2_500:   sz = 8
    else:                sz = 4

    # 3. Price conviction (20)
    p = price_cents
    if   p <= 15 or p >= 85: conv = 20
    elif p <= 25 or p >= 75: conv = 14
    elif p <= 35 or p >= 65: conv = 8
    else:                    conv = 3

    # 4. Win rate (20)
    if n_trades < 5:     wr = 0
    elif win_rate >= .65: wr = 20
    elif win_rate >= .60: wr = 15
    elif win_rate >= .55: wr = 9
    elif win_rate >= .50: wr = 4
    else:                wr = 0

    total = cred + sz + conv + wr

    if total >= 80:   label, emoji = "STRONG SIGNAL", "🔥"
    elif total >= 60: label, emoji = "DECENT SIGNAL", "⚡"
    elif total >= 40: label, emoji = "MILD SIGNAL",   "👀"
    else:             label, emoji = "INFORMATIONAL", "📊"

    parts = []
    if cred >= 28:  parts.append(f"elite wallet (+${pnl:,.0f})")
    elif cred >= 18: parts.append(f"profitable wallet (+${pnl:,.0f})")
    elif cred == 0 and pnl < 0: parts.append(f"losing wallet (${pnl:,.0f})")
    elif cred == 0: parts.append("new/unproven wallet")

    if sz >= 20:  parts.append(f"huge bet (${usd:,.0f})")
    elif sz >= 14: parts.append(f"large bet (${usd:,.0f})")

    if conv >= 14: parts.append(f"high conviction ({price_cents:.0f}¢)")
    elif conv <= 3: parts.append("near 50/50")

    if wr >= 15:  parts.append(f"strong win rate ({win_rate*100:.0f}%)")
    elif wr == 0 and n_trades >= 5: parts.append(f"poor win rate ({win_rate*100:.0f}%)")
    elif n_trades < 5: parts.append("few trades on record")

    return Score(total, cred, sz, conv, wr, label, emoji,
                 ", ".join(parts) or "no standout factors")
