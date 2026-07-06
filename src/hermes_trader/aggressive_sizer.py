#!/usr/bin/env python3
"""
Aggressive 0DTE Position Sizer
===============================
Kelly-criterion-based sizing tuned for small-account aggressive growth.

Rules (from /root task spec):
  1. Kelly criterion for optimal bet sizing
  2. $100 account → risk 50% ($50) per trade
  3. Scale down as account grows: 50%@$100, 40%@$200, 30%@$500, 20%@$1000+
  4. 0DTE theta decay: reduce size later in the trading day
  5. Consecutive-loss cooldown: halve size after 2 consecutive losses
  6. Hard ceiling: never risk >80% of account on one trade

Architecture
------------
AggressiveSizer is a *stateless evaluator* (like IntelligentRiskLayer).
It takes a signal + account snapshot and returns a sizing recommendation.
Caller is responsible for placing the order.

Usage::

    sizer = AggressiveSizer()
    rec = sizer.recommend(
        win_prob=0.55,
        avg_win=0.80,       # avg gain as fraction of premium
        avg_loss=0.50,       # avg loss as fraction of premium
        premium_per_contract=0.45,
        account_value=100.0,
        consecutive_losses=0,
    )
    # rec.risk_dollars     → how much to risk ($)
    # rec.num_contracts    → contracts to buy (int)
    # rec.position_value   → total debit ($)
    # rec.kelly_fraction   → raw Kelly %
    # rec.adjusted_risk_pct → final risk % after all adjustments
    # rec.signals          → human-readable reasoning
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone, timedelta
from typing import List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# US Eastern offset (EDT = UTC-4, EST = UTC-5).  We use UTC-4 for daylight
# since 0DTE trading happens mostly during EDT months.
_ET = timezone(timedelta(hours=-4))

# Account-size → max risk % tiers (sorted ascending by threshold)
_RISK_TIERS: list[tuple[float, float]] = [
    (100.0,  0.50),   # $100–199 → 50%
    (200.0,  0.40),   # $200–499 → 40%
    (500.0,  0.30),   # $500–999 → 30%
    (1000.0, 0.20),   # $1000+   → 20%
]

# Hard ceiling
MAX_RISK_PCT = 0.80

# Time-of-day theta decay multiplier schedule
# Key: hour in ET (24h), Value: multiplier applied to base risk %
_THETA_DECAY_SCHEDULE: list[tuple[int, float]] = [
    (9,   1.00),   # 09:00-09:59 → full size (market open, max opportunity)
    (10,  1.00),   # 10:00-10:59 → full size
    (11,  0.95),   # 11:00-11:59 → slight taper
    (12,  0.85),   # 12:00-12:59 → lunch hour, thinner
    (13,  0.75),   # 13:00-13:59 → afternoon decay accelerating
    (14,  0.65),   # 14:00-14:59 → heavy theta, smaller
    (15,  0.50),   # 15:00-15:59 → max decay zone
    (16,  0.00),   # 16:00+ → no new 0DTE trades (market closed)
]

# Consecutive loss thresholds
_CONSEC_LOSS_THRESHOLDS: list[tuple[int, float]] = [
    (0, 1.00),
    (1, 0.75),   # 1 loss → 75% of normal
    (2, 0.50),   # 2 losses → 50% of normal
    (3, 0.25),   # 3 losses → 25% of normal (emergency brake)
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SizeRecommendation:
    """Result of AggressiveSizer.recommend()."""
    risk_dollars: float          # dollar amount to risk
    num_contracts: int           # integer contracts to buy
    position_value: float        # total debit = contracts × premium × 100
    kelly_fraction: float        # raw Kelly %
    kelly_half: float            # half-Kelly (safer variant)
    base_risk_pct: float         # account-size tier risk %
    theta_multiplier: float      # time-of-day decay multiplier
    loss_cooldown_multiplier: float  # consecutive loss multiplier
    adjusted_risk_pct: float     # final risk % after all adjustments
    max_allowed_risk: float      # hard ceiling ($)
    signals: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sizer
# ---------------------------------------------------------------------------

class AggressiveSizer:
    """Stateless aggressive position sizer for 0DTE options."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Kelly criterion
    # ------------------------------------------------------------------

    @staticmethod
    def kelly(win_prob: float, avg_win: float, avg_loss: float) -> float:
        """Compute Kelly fraction:  f* = (p * b - q) / b

        Args:
            win_prob: probability of win (0-1)
            avg_win:  average win as fraction of risk (e.g. 0.80 = 80% gain)
            avg_loss: average loss as fraction of risk (e.g. 0.50 = 50% loss)

        Returns:
            Kelly fraction (may be negative = don't trade).
        """
        if avg_loss <= 0 or win_prob <= 0 or win_prob >= 1:
            return 0.0
        b = avg_win / avg_loss  # payoff ratio
        q = 1.0 - win_prob
        return max((win_prob * b - q) / b, 0.0)

    # ------------------------------------------------------------------
    # Account-size risk tier
    # ------------------------------------------------------------------

    @staticmethod
    def base_risk_pct(account_value: float) -> float:
        """Return the risk % for the given account size.

        Tiers:
            $100–$199  → 50%
            $200–$499  → 40%
            $500–$999  → 30%
            $1000+     → 20%
            <$100      → 50% (aggressive small account)
        """
        for threshold, risk in reversed(_RISK_TIERS):
            if account_value >= threshold:
                return risk
        return _RISK_TIERS[0][1]

    # ------------------------------------------------------------------
    # Time-of-day multiplier
    # ------------------------------------------------------------------

    @staticmethod
    def theta_multiplier(now_utc: Optional[datetime] = None) -> float:
        """Return the time-of-day sizing multiplier (0.0 – 1.0).

        Uses US Eastern time. After 4 PM ET returns 0 (no new trades).
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        et_hour = now_utc.astimezone(_ET).hour

        prev_mult = 1.0
        for hour, mult in _THETA_DECAY_SCHEDULE:
            if et_hour < hour:
                break
            prev_mult = mult
        return prev_mult

    # ------------------------------------------------------------------
    # Consecutive-loss cooldown
    # ------------------------------------------------------------------

    @staticmethod
    def loss_cooldown_multiplier(consecutive_losses: int) -> float:
        """Multiplier based on consecutive recent losses."""
        mult = 0.25  # worst case
        for threshold, m in _CONSEC_LOSS_THRESHOLDS:
            if consecutive_losses <= threshold:
                mult = m
                break
        return mult

    # ------------------------------------------------------------------
    # Full recommendation
    # ------------------------------------------------------------------

    def recommend(
        self,
        win_prob: float,
        avg_win: float,
        avg_loss: float,
        premium_per_contract: float,
        account_value: float,
        consecutive_losses: int = 0,
        now_utc: Optional[datetime] = None,
        contract_multiplier: int = 100,
    ) -> SizeRecommendation:
        """Compute the aggressive position sizing recommendation.

        Args:
            win_prob:            estimated probability of profit (0-1)
            avg_win:             avg gain per $1 risked (e.g. 0.80)
            avg_loss:            avg loss per $1 risked (e.g. 0.50)
            premium_per_contract: debit per contract (e.g. 0.45 → $45)
            account_value:       total account value ($)
            consecutive_losses:  how many losses in a row (0 = fresh)
            now_utc:             current UTC time (for theta schedule)
            contract_multiplier: typically 100 for equity options

        Returns:
            SizeRecommendation with all sizing details.
        """
        signals: list[str] = []

        # --- 1. Kelly ---
        kelly_f = self.kelly(win_prob, avg_win, avg_loss)
        kelly_half = kelly_f / 2.0  # half-Kelly for safety
        signals.append(
            f"Kelly: f*={kelly_f:.1%}, half-Kelly={kelly_half:.1%} "
            f"(win_prob={win_prob:.0%}, avg_win={avg_win:.2f}, avg_loss={avg_loss:.2f})"
        )

        # --- 2. Base risk % from account tier ---
        base_pct = self.base_risk_pct(account_value)
        signals.append(f"Account tier risk: {base_pct:.0%} (account=${account_value:.2f})")

        # --- 3. Theta-of-day multiplier ---
        theta_mult = self.theta_multiplier(now_utc)
        signals.append(f"Theta decay multiplier: {theta_mult:.0%}")

        # --- 4. Consecutive-loss cooldown ---
        loss_mult = self.loss_cooldown_multiplier(consecutive_losses)
        signals.append(
            f"Loss cooldown: {loss_mult:.0%} ({consecutive_losses} consecutive losses)"
        )

        # --- 5. Combine: take the LESS aggressive of Kelly and account tier,
        #     then apply theta and loss adjustments ---
        # Kelly provides the theoretical max; account tier is the practical max.
        # We use min(theoretical, practical) as the base, then multiply by
        # time and loss adjustments.
        raw_pct = min(kelly_half, base_pct) if kelly_f > 0 else 0.0
        adjusted_pct = raw_pct * theta_mult * loss_mult

        # --- 6. Hard ceiling ---
        max_risk_dollars = account_value * MAX_RISK_PCT
        risk_dollars = account_value * adjusted_pct

        if risk_dollars > max_risk_dollars:
            risk_dollars = max_risk_dollars
            adjusted_pct = MAX_RISK_PCT
            signals.append(f"⚠ Hard ceiling hit: capped at {MAX_RISK_PCT:.0%} (${max_risk_dollars:.2f})")

        # --- 7. Contract count ---
        cost_per_contract = premium_per_contract * contract_multiplier
        if cost_per_contract <= 0:
            num_contracts = 0
            signals.append("⚠ Zero premium — no position")
        else:
            num_contracts = max(int(risk_dollars // cost_per_contract), 0)

        position_value = num_contracts * cost_per_contract

        # --- 8. Zero-trade guard ---
        if kelly_f <= 0:
            signals.append("🚫 Kelly says don't trade (negative edge)")
        if num_contracts == 0 and risk_dollars > 0:
            signals.append(f"⚠ Risk ${risk_dollars:.2f} but contracts cost ${cost_per_contract:.2f} each — need larger account")

        # --- 9. Final signal ---
        signals.append(
            f"→ Size: {num_contracts} contracts × ${premium_per_contract:.2f} = ${position_value:.2f} "
            f"({adjusted_pct:.1%} of ${account_value:.2f})"
        )

        return SizeRecommendation(
            risk_dollars=round(risk_dollars, 2),
            num_contracts=num_contracts,
            position_value=round(position_value, 2),
            kelly_fraction=round(kelly_f, 4),
            kelly_half=round(kelly_half, 4),
            base_risk_pct=base_pct,
            theta_multiplier=theta_mult,
            loss_cooldown_multiplier=loss_mult,
            adjusted_risk_pct=round(adjusted_pct, 4),
            max_allowed_risk=round(max_risk_dollars, 2),
            signals=signals,
        )


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sizer = AggressiveSizer()

    scenarios = [
        ("Fresh $100, 55% edge",     0.55, 0.80, 0.50, 0.45, 100.0, 0),
        ("Fresh $100, strong 60%",   0.60, 1.00, 0.50, 0.55, 100.0, 0),
        ("$100, 2 losses streak",    0.55, 0.80, 0.50, 0.45, 100.0, 2),
        ("$250, morning",            0.55, 0.80, 0.50, 0.45, 250.0, 0),
        ("$600, afternoon",          0.55, 0.80, 0.50, 0.45, 600.0, 0),
        ("$1500, fresh",             0.55, 0.80, 0.50, 0.45, 1500.0, 0),
    ]

    for label, wp, aw, al, prem, acct, losses in scenarios:
        rec = sizer.recommend(wp, aw, al, prem, acct, losses)
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        for sig in rec.signals:
            print(f"  {sig}")
