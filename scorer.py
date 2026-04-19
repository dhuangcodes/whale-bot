"""
Whale trade confidence scorer (0-100).

Factors:
  1. Wallet credibility (all-time PnL)  — 50 pts  (most important)
  2. Whale consensus (same side)        — 30 pts
  3. Price conviction zone              — 20 pts

Bonus/penalty (capped at 100):
  +8  market confirmed after trade
  +4  market moving their way
  -5  market moved against them

Score bands:
  80+   🔥 STRONG SIGNAL
  60-79 ⚡ DECENT SIGNAL
  40-59 👀 MILD SIGNAL
  <40   📊 INFORMATIONAL
"""
from dataclasses import dataclass


@dataclass
class Score:
    total: int
    credibility: int
    dominance: int   # kept for compatibility, unused
    conviction: int
    price_move: int
    consensus: int
    label: str
    emoji: str
    reason: str


def score(
    usd: float,
    price_cents: float,
    pnl: float,
    volume_24h: float,
    price_after_cents: float,
    side: str,
    same_side_whales: int,
) -> Score:

    # --- 1. Wallet Credibility (50 pts) ---
    if pnl >= 500_000:   cred = 50
    elif pnl >= 200_000: cred = 42
    elif pnl >= 100_000: cred = 34
    elif pnl >= 50_000:  cred = 25
    elif pnl >= 10_000:  cred = 15
    elif pnl >= 0:       cred = 6
    else:                cred = 0

    # --- 2. Whale Consensus (30 pts) ---
    if same_side_whales >= 4:   cons = 30
    elif same_side_whales >= 3: cons = 22
    elif same_side_whales >= 2: cons = 14
    elif same_side_whales == 1: cons = 7
    else:                       cons = 0

    # --- 3. Price Conviction (20 pts) ---
    p = price_cents
    if   p <= 15 or p >= 85: conv = 20
    elif p <= 25 or p >= 75: conv = 16
    elif p <= 35 or p >= 65: conv = 11
    elif p <= 45 or p >= 55: conv = 6
    else:                    conv = 3  # dead 50/50

    # --- Market confirmation bonus ---
    pm = 0
    if price_after_cents > 0 and price_cents > 0:
        movement = (price_after_cents - price_cents) if "YES" in side.upper() else (price_cents - price_after_cents)
        if movement >= 3:    pm = 8
        elif movement >= 1:  pm = 4
        elif movement < -1:  pm = -5

    total = min(100, max(0, cred + cons + conv + pm))

    if total >= 80:   label, emoji = "STRONG SIGNAL", "🔥"
    elif total >= 60: label, emoji = "DECENT SIGNAL", "⚡"
    elif total >= 40: label, emoji = "MILD SIGNAL",   "👀"
    else:             label, emoji = "INFORMATIONAL", "📊"

    parts = []
    if cred >= 42:   parts.append(f"elite wallet (+${pnl:,.0f})")
    elif cred >= 34: parts.append(f"strong wallet (+${pnl:,.0f})")
    elif cred >= 25: parts.append(f"profitable wallet (+${pnl:,.0f})")
    elif cred >= 15: parts.append(f"emerging wallet (+${pnl:,.0f})")
    elif cred == 0 and pnl < 0: parts.append(f"losing wallet (${pnl:,.0f})")
    else:            parts.append("limited track record")

    if cons >= 22:   parts.append(f"{same_side_whales + 1} whales agree 🐋")
    elif cons >= 14: parts.append(f"{same_side_whales} other whales agree")
    elif cons >= 7:  parts.append("1 other whale agrees")

    if conv >= 16:   parts.append(f"high conviction ({price_cents:.0f}¢)")
    elif conv <= 3:  parts.append(f"dead 50/50 ({price_cents:.0f}¢)")

    if pm >= 8:      parts.append("market confirmed ✓✓")
    elif pm >= 4:    parts.append("market moving with them ✓")
    elif pm == -5:   parts.append("market moved against ✗")

    return Score(
        total, cred, 0, conv, pm, cons,
        label, emoji,
        ", ".join(parts) or "no standout factors"
    )
