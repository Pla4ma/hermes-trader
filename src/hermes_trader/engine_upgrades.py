"""Engine Upgrades — research-backed improvements.

Based on:
- tastylive research (10,000+ backtested trades)
- Option Alpha backtesting
- Kelly Criterion analysis
- Risk of ruin analysis
- Complete Greeks hierarchy
"""

import json
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("hermes_trader.engine_upgrades")


class EngineUpgrades:
    """Research-backed engine improvements."""

    def __init__(self):
        from .engine_config import (
            DELTA, WIDTH, DTE, EXIT, SIZING, VIX_FILTERS,
            DAY_FILTERS, TIME_FILTERS, UNDERLYINGS, RISK, GROWTH
        )
        self.delta = DELTA
        self.width = WIDTH
        self.dte = DTE
        self.exit = EXIT
        self.sizing = SIZING
        self.vix = VIX_FILTERS
        self.day = DAY_FILTERS
        self.time = TIME_FILTERS
        self.underlyings = UNDERLYINGS
        self.risk = RISK
        self.growth = GROWTH

    def get_optimal_delta(self, vix_level: float) -> float:
        """Get optimal delta based on VIX level (Option Alpha research)."""
        if vix_level < 15:
            return self.delta["vix_adjustment"]["low_vol"]
        elif vix_level < 25:
            return self.delta["vix_adjustment"]["normal"]
        elif vix_level < 30:
            return self.delta["vix_adjustment"]["high_vol"]
        else:
            return self.delta["vix_adjustment"]["crisis"]

    def get_kelly_size(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Calculate Kelly Criterion position sizing."""
        if avg_loss <= 0 or avg_win <= 0:
            return 0
        b = avg_win / avg_loss
        full_kelly = (b * win_rate - (1 - win_rate)) / b
        fractional = full_kelly * self.sizing["kelly_fraction"]
        return max(0, min(fractional, self.sizing["max_risk_per_trade"]))

    def check_vix_term_structure(self, vix: float, vix3m: float) -> dict:
        """Check VIX term structure (THE #1 edge)."""
        ratio = vix3m / vix if vix > 0 else 1.0
        is_contango = ratio >= self.vix["term_structure"]["contango_threshold"]
        is_strong = ratio >= self.vix["term_structure"]["strong_contango"]
        is_backward = ratio < self.vix["term_structure"]["backwardation_threshold"]

        if is_strong:
            regime = "STRONG_CONTANGO"
            win_rate_boost = 0.04  # +4% WR
        elif is_contango:
            regime = "CONTANGO"
            win_rate_boost = 0.02  # +2% WR
        elif is_backward:
            regime = "BACKWARDATION"
            win_rate_boost = -0.10  # -10% WR
        else:
            regime = "NEUTRAL"
            win_rate_boost = 0

        return {
            "regime": regime,
            "ratio": round(ratio, 3),
            "is_contango": is_contango,
            "should_trade": is_contango,  # Only trade in contango
            "win_rate_boost": win_rate_boost,
        }

    def check_day_filter(self) -> dict:
        """Check day-of-week filter (research: Tuesday best, Friday never)."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        et_hour = now.hour - 4
        et_time = et_hour + now.minute / 60
        now_et = now - timedelta(days=1) if et_time < 0 else now
        day = now_et.weekday()

        is_best = day in self.day["best"]
        is_good = day in self.day["good"]
        is_avoid = day in self.day["avoid"]

        return {
            "day": day,
            "is_best": is_best,
            "is_good": is_good,
            "is_avoid": is_avoid,
            "should_trade": not is_avoid,
            "rating": "BEST" if is_best else ("GOOD" if is_good else ("AVOID" if is_avoid else "OK")),
        }

    def check_time_filter(self) -> dict:
        """Check time-of-day filter (research: 9:45-10:30 AM optimal)."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        et_hour = now.hour - 4
        et_time = et_hour + now.minute / 60

        is_optimal = self.time["optimal_start"] <= et_time <= self.time["optimal_end"]
        is_good = self.time["good_start"] <= et_time <= self.time["good_end"]
        is_avoid = et_time >= self.time["avoid_start"]
        is_market_open = 9.5 <= et_time <= 16.0

        return {
            "et_time": round(et_time, 2),
            "is_optimal": is_optimal,
            "is_good": is_good,
            "is_avoid": is_avoid,
            "is_market_open": is_market_open,
            "should_trade": not is_avoid and is_market_open,
        }

    def check_position_sizing(self, account_value: float) -> dict:
        """Check position sizing limits (research: Kelly for $50)."""
        max_risk = account_value * self.sizing["max_risk_per_trade"]
        max_positions = self.sizing["max_positions"]
        max_portfolio_risk = account_value * self.sizing["max_portfolio_risk"]

        return {
            "account_value": account_value,
            "max_risk_per_trade": round(max_risk, 2),
            "max_positions": max_positions,
            "max_portfolio_risk": round(max_portfolio_risk, 2),
            "kelly_fraction": self.sizing["kelly_fraction"],
        }

    def select_strategy(self, regime: str, vix: float) -> str:
        """Select optimal strategy (research: directional spreads outperform)."""
        if regime == "BULL_LOW_VOL":
            return "bull_put_spread"
        elif regime == "BULL_HIGH_VOL":
            return "bull_put_spread"
        elif regime == "BEAR_LOW_VOL":
            return "bear_call_spread"
        elif regime == "BEAR_HIGH_VOL":
            return "bear_call_spread"
        elif vix > 25:
            return "iron_condor"  # Only use ICs in high vol
        else:
            return "bull_put_spread"  # Default: bullish bias

    def get_exit_rules(self) -> dict:
        """Get exit rules (research: 50% profit = optimal)."""
        return {
            "profit_target": self.exit["profit_target"],
            "stop_loss": self.exit["stop_loss"],
            "time_exit": self.exit["time_exit"],
            "gamma_exit": self.exit["gamma_exit"],
        }

    def get_risk_rules(self) -> dict:
        """Get risk management rules (research: essential for $50)."""
        return self.risk

    def get_growth_targets(self) -> dict:
        """Get growth targets (research: realistic expectations)."""
        return self.growth

    def full_check(self, vix: float, vix3m: float, account_value: float) -> dict:
        """Run full engine check with all research-backed filters."""
        term = self.check_vix_term_structure(vix, vix3m)
        day = self.check_day_filter()
        time = self.check_time_filter()
        sizing = self.check_position_sizing(account_value)
        strategy = self.select_strategy("BULL_LOW_VOL", vix)
        delta = self.get_optimal_delta(vix)
        exit_rules = self.get_exit_rules()
        risk_rules = self.get_risk_rules()
        growth = self.get_growth_targets()

        should_trade = (
            term["should_trade"]
            and day["should_trade"]
            and time["should_trade"]
            and vix >= self.vix["min"]
            and vix <= self.vix["max"]
        )

        return {
            "should_trade": should_trade,
            "strategy": strategy,
            "delta": delta,
            "width": self.width["default"],
            "dte_entry": self.dte["entry"],
            "exit_rules": exit_rules,
            "term_structure": term,
            "day_filter": day,
            "time_filter": time,
            "sizing": sizing,
            "risk_rules": risk_rules,
            "growth_targets": growth,
        }
