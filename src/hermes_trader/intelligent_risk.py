#!/usr/bin/env python3
"""
Intelligent Risk Management Layer
==================================
Dynamic risk management using higher-order Greeks and IV surface analytics.

Wraps the existing RiskGate and AdvancedPositionMonitor with intelligence from:

1. IV-Based Stops — wider stops in high-vol environments (more noise),
   tighter stops in low-vol environments (less noise).
2. GEX-Aware Exits — when GEX flips negative (dealers short gamma), tighten
   stops because price moves accelerate.
3. Charm Decay Exits — when delta is decaying fast (high |charm|), the position
   is losing its directional edge; consider exiting.
4. Vanna-Based Hedging — when vanna is large, delta will shift significantly
   on vol changes; adjust position size accordingly.
5. Risk-of-Ruin Position Sizing — Kelly-adjacent sizing that keeps the
   probability of ruin below a configurable threshold (default <5%).

Architecture
------------
IntelligentRiskLayer is a *stateless evaluator*. It takes snapshots and
returns signals — no side effects, no order placement. The caller
(auto_trader / workflow) is responsible for acting on the returned signals.

The layer plugs into two existing seams:
  • PolicyResult.risk_summary  (set at trade candidate evaluation time)
  • PositionMonitor.update()   (called every tick / bar during monitoring)

Usage (new-trade evaluation)::

    layer = IntelligentRiskLayer()
    result = layer.evaluate_new_trade(candidate, account, market, iv_data)
    # result.stop_pct       → dynamic stop % to wire into ExitPlan
    # result.size_multiplier → fraction of base size to actually order
    # result.signals         → human-readable signal log

Usage (live position monitoring)::

    action = layer.evaluate_live_position(position, market, iv_data)
    # action.should_exit     → bool
    # action.suggested_stop  → dynamic stop price
    # action.size_adjustment → fraction to scale in/out
    # action.signals         → human-readable signal log

References:
  - Gatheral (2004): SVI parametrization
  - Gatheral & Jacquier (2014): SSVI arbitrage-free surface
  - Carr & Madan (1998): Risk of ruin formula
  - Kelly (1956): Criterion for optimal bet sizing
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hermes_trader.intelligent_risk")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SignalType(str, Enum):
    IV_STOP = "iv_stop"
    GEX_EXIT = "gex_exit"
    CHARM_EXIT = "charm_exit"
    VANNA_HEDGE = "vanna_hedge"
    RISK_OF_RUIN = "risk_of_ruin"
    VOL_REGIME = "vol_regime"


class SignalAction(str, Enum):
    WIDEN_STOP = "widen_stop"
    TIGHTEN_STOP = "tighten_stop"
    EXIT = "exit"
    HEDGE = "hedge"
    REDUCE_SIZE = "reduce_size"
    INCREASE_SIZE = "increase_size"
    HOLD = "hold"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class IntelligentRiskConfig:
    """All tunables for the intelligent risk layer.

    Every field can be overridden via environment variables with the prefix
    ``INTELLIGENT_RISK_<FIELD_UPPER>`` — loaded at config init time.
    """

    # --- IV-based stop adjustment ---
    iv_stop_base_pct: float = 0.02           # 2 % baseline stop
    iv_stop_max_pct: float = 0.06            # 6 % max stop in extreme IV
    iv_stop_min_pct: float = 0.005           # 0.5 % min stop in calm IV
    iv_percentile_high: float = 75.0         # IV above this → widen stops
    iv_percentile_low: float = 25.0          # IV below this → tighten stops
    iv_atm_reference_sigma: float = 0.25     # "normal" IV for z-score mapping

    # --- GEX-aware exit ---
    gex_tighten_factor: float = 0.6          # 40 % tighter when GEX < 0
    gex_magnitude_threshold: float = 5e5     # |GEX| above which we act
    gex_flip_detection_window: int = 3       # bars to confirm flip

    # --- Charm decay exit ---
    charm_abs_threshold: float = 0.008       # |charm| above which delta decay is fast
    charm_exit_pct_of_delta: float = 0.30    # exit when daily charm > 30 % of delta
    charm_min_dte_for_exit: int = 5          # don't exit on charm if DTE < 5

    # --- Vanna hedging ---
    vanna_hedge_abs_threshold: float = 0.04  # |vanna| above which delta is volatile
    vanna_size_reduction_pct: float = 0.30   # reduce 30 % when vanna extreme
    vanna_recalc_after_dte: int = 7          # recalc vanna hedge below this DTE

    # --- Risk-of-ruin sizing ---
    risk_per_trade_pct: float = 0.02         # 2 % of equity per trade
    target_ruin_rate: float = 0.05           # < 5 % acceptable ruin prob
    default_win_rate: float = 0.55           # fallback win rate
    default_payoff_ratio: float = 1.5        # fallback reward:risk
    min_position_usd: float = 50.0           # minimum position size
    max_position_pct_equity: float = 0.10    # max 10 % of equity in one trade

    # --- Vol regime multipliers ---
    vol_regime_high_threshold: float = 1.5   # IV/AVG IV > 1.5 → high vol regime
    vol_regime_low_threshold: float = 0.7    # IV/AVG IV < 0.7 → low vol regime
    vol_regime_high_stop_mult: float = 1.3   # 30 % wider stop in high vol
    vol_regime_low_stop_mult: float = 0.85   # 15 % tighter stop in low vol

    @classmethod
    def from_env(cls) -> "IntelligentRiskConfig":
        """Create config from environment variables."""
        import os
        cfg = cls()
        prefix = "INTELLIGENT_RISK_"
        for fld_name, fld_val in cfg.__dataclass_fields__.items():
            env_key = prefix + fld_name.upper()
            env_val = os.environ.get(env_key)
            if env_val is not None:
                try:
                    setattr(cfg, fld_name, type(fld_val.default)(env_val))
                except (ValueError, TypeError):
                    logger.warning(
                        "Could not parse %s=%r for field %s", env_key, env_val, fld_name
                    )
        return cfg


# ---------------------------------------------------------------------------
# Signal data classes
# ---------------------------------------------------------------------------

@dataclass
class RiskSignal:
    """A single risk signal emitted by the intelligent layer."""
    signal_type: SignalType
    action: SignalAction
    current_value: float
    threshold: float
    adjustment_factor: float   # multiplicative: >1 widens, <1 tightens
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.signal_type.value,
            "action": self.action.value,
            "current": round(self.current_value, 6),
            "threshold": round(self.threshold, 6),
            "adj_factor": round(self.adjustment_factor, 4),
            "reason": self.reason,
            **self.metadata,
        }


@dataclass
class TradeRiskResult:
    """Output of evaluate_new_trade()."""
    stop_pct: float
    size_multiplier: float
    position_usd: float
    risk_of_ruin: float
    win_rate_used: float
    payoff_ratio_used: float
    signals: List[RiskSignal] = field(default_factory=list)
    kill_trade: bool = False
    kill_reason: str = ""

    def to_risk_summary(self) -> dict:
        """Serialize for PolicyResult.risk_summary."""
        return {
            "intelligent_stop_pct": round(self.stop_pct, 4),
            "size_multiplier": round(self.size_multiplier, 4),
            "position_usd": round(self.position_usd, 2),
            "risk_of_ruin_pct": round(self.risk_of_ruin * 100, 2),
            "signals": [s.to_dict() for s in self.signals],
            "kill_trade": self.kill_trade,
            "kill_reason": self.kill_reason,
        }


@dataclass
class LivePositionAction:
    """Output of evaluate_live_position()."""
    should_exit: bool
    exit_reason: str
    suggested_stop_price: Optional[float]
    suggested_stop_pct: float
    size_adjustment: float       # 1.0 = no change, 0.7 = reduce 30 %
    signals: List[RiskSignal] = field(default_factory=list)
    urgency: str = "normal"      # "normal" | "high" | "critical"

    def to_dict(self) -> dict:
        return {
            "should_exit": self.should_exit,
            "exit_reason": self.exit_reason,
            "suggested_stop_price": round(self.suggested_stop_price, 2)
            if self.suggested_stop_price else None,
            "suggested_stop_pct": round(self.suggested_stop_pct, 4),
            "size_adjustment": round(self.size_adjustment, 4),
            "urgency": self.urgency,
            "signals": [s.to_dict() for s in self.signals],
        }


# ---------------------------------------------------------------------------
# Core intelligence layer
# ---------------------------------------------------------------------------

class IntelligentRiskLayer:
    """Dynamic risk management using higher-order Greeks and IV surface analytics.

    Stateless evaluator — call methods, read signals, act elsewhere.
    """

    def __init__(self, config: Optional[IntelligentRiskConfig] = None):
        self.cfg = config or IntelligentRiskConfig.from_env()
        # Lazy imports to avoid circular deps at module load
        self._greeks_cls = None
        self._hol_cls = None

    # -- lazy helpers --
    def _greek(self):
        if self._greeks_cls is None:
            from .greeks_engine import BlackScholesGreeks
            self._greeks_cls = BlackScholesGreeks
        return self._greeks_cls

    def _hog(self):
        if self._hol_cls is None:
            from .hol_greeks import BlackScholesMertonModel
            self._hol_cls = BlackScholesMertonModel
        return self._hol_cls

    # ======================================================================
    # PUBLIC API — New Trade Evaluation
    # ======================================================================

    def evaluate_new_trade(
        self,
        spot: float,
        strike: float,
        days_to_expiry: int,
        option_type: str,
        iv: float,
        rate: float = 0.05,
        dividend_yield: float = 0.0,
        account_equity: float = 5000.0,
        base_stop_pct: Optional[float] = None,
        base_position_usd: Optional[float] = None,
        gex_data: Optional[dict] = None,
        iv_surface_data: Optional[dict] = None,
        win_rate: Optional[float] = None,
        payoff_ratio: Optional[float] = None,
        portfolio_gamma_exposure: Optional[float] = None,
    ) -> TradeRiskResult:
        """Evaluate a proposed trade with intelligent risk adjustments.

        Parameters
        ----------
        spot : float
            Current underlying price.
        strike : float
            Option strike price.
        days_to_expiry : int
            Calendar days to expiration.
        option_type : str
            "call" or "put".
        iv : float
            Current implied volatility (annualised decimal, e.g. 0.25).
        rate : float
            Risk-free rate.
        dividend_yield : float
            Continuous dividend yield.
        account_equity : float
            Total account equity in USD.
        base_stop_pct : float, optional
            Initial stop % (default: config iv_stop_base_pct).
        base_position_usd : float, optional
            Proposed position size in USD.
        gex_data : dict, optional
            Output from GammaPositioning.calculate_gex() or similar.
            Keys: total_gex, regime, flip_strike.
        iv_surface_data : dict, optional
            IV surface snapshot. Keys: iv_percentile, iv_rank, iv_regime,
            avg_iv, current_iv, term_structure_slope, skew.
        win_rate : float, optional
            Historical win rate for this strategy (0-1).
        payoff_ratio : float, optional
            Average win / average loss for this strategy.
        portfolio_gamma_exposure : float, optional
            Net portfolio GEX (negative = dealers short gamma).

        Returns
        -------
        TradeRiskResult
        """
        signals: List[RiskSignal] = []
        tau = max(days_to_expiry / 365.0, 1 / 365.0)

        # Compute Greeks via the existing engine
        g = self._greek()
        delta = g.delta(spot, strike, rate, dividend_yield, iv, tau, option_type)
        gamma = g.gamma(spot, strike, rate, dividend_yield, iv, tau)
        vega = g.vega(spot, strike, rate, dividend_yield, iv, tau)
        theta = g.theta(spot, strike, rate, dividend_yield, iv, tau, option_type)

        # Higher-order Greeks via hol_greeks
        hog = self._hog()()
        try:
            ho = hog.compute(
                flag="c" if option_type == "call" else "p",
                S=spot, K=strike, t=tau, r=rate, sigma=iv, q=dividend_yield,
            )
            vanna = ho.vanna
            # hol_greeks computes charm based on flag ('c'/'p'), already put-aware
            charm = ho.charm
            vomma = ho.vomma
            speed = ho.speed
        except Exception:
            # Fallback: compute vanna/charm via greeks_engine
            vanna = g.vanna(spot, strike, rate, dividend_yield, iv, tau)
            charm = g.charm(spot, strike, rate, dividend_yield, iv, tau, option_type)
            vomma = g.vomma(spot, strike, rate, dividend_yield, iv, tau)
            speed = g.speed(spot, strike, rate, dividend_yield, iv, tau)

        # ---------------------------------------------------------------
        # 1. IV-Based Stop Adjustment
        # ---------------------------------------------------------------
        stop_pct = base_stop_pct if base_stop_pct is not None else self.cfg.iv_stop_base_pct
        iv_signal = self._iv_stop_adjustment(iv, iv_surface_data, stop_pct)
        if iv_signal:
            stop_pct *= iv_signal.adjustment_factor
            signals.append(iv_signal)

        # ---------------------------------------------------------------
        # 2. Vol Regime Multiplier
        # ---------------------------------------------------------------
        regime_signal = self._vol_regime_adjustment(iv, iv_surface_data)
        if regime_signal:
            stop_pct *= regime_signal.adjustment_factor
            signals.append(regime_signal)

        # ---------------------------------------------------------------
        # 3. GEX-Aware Stop Tightening
        # ---------------------------------------------------------------
        gex_signal = self._gex_adjustment(
            gex_data, portfolio_gamma_exposure, spot
        )
        if gex_signal:
            stop_pct *= gex_signal.adjustment_factor
            signals.append(gex_signal)

        # ---------------------------------------------------------------
        # 4. Charm Decay Exit Signal
        # ---------------------------------------------------------------
        charm_signal = self._charm_exit_signal(charm, delta, days_to_expiry)
        if charm_signal:
            signals.append(charm_signal)

        # ---------------------------------------------------------------
        # 5. Vanna-Based Position Size Adjustment
        # ---------------------------------------------------------------
        vanna_signal = self._vanna_size_adjustment(vanna, days_to_expiry)
        size_multiplier = vanna_signal.adjustment_factor if vanna_signal else 1.0
        if vanna_signal:
            signals.append(vanna_signal)

        # ---------------------------------------------------------------
        # 6. Risk-of-Ruin Position Sizing
        # ---------------------------------------------------------------
        wr = win_rate if win_rate is not None else self.cfg.default_win_rate
        pr = payoff_ratio if payoff_ratio is not None else self.cfg.default_payoff_ratio
        ror = self._risk_of_ruin(wr, pr)

        # Determine base position size
        base_size = base_position_usd if base_position_usd is not None else (
            account_equity * self.cfg.risk_per_trade_pct
        )

        # Apply size multiplier from vanna + risk-of-ruin
        if ror > self.cfg.target_ruin_rate:
            # Kelly-style reduction
            kelly_mult = self._kelly_fraction(wr, pr)
            ruin_signal = RiskSignal(
                signal_type=SignalType.RISK_OF_RUIN,
                action=SignalAction.REDUCE_SIZE,
                current_value=ror,
                threshold=self.cfg.target_ruin_rate,
                adjustment_factor=kelly_mult,
                reason=(
                    f"Risk of ruin {ror*100:.1f}% exceeds target {self.cfg.target_ruin_rate*100:.1f}%. "
                    f"Applying Kelly fraction {kelly_mult:.2f}."
                ),
                metadata={"kelly_fraction": kelly_mult, "win_rate": wr, "payoff_ratio": pr},
            )
            size_multiplier *= kelly_mult
            signals.append(ruin_signal)

        position_usd = base_size * size_multiplier
        position_usd = max(self.cfg.min_position_usd, position_usd)

        # Cap at max % of equity
        max_usd = account_equity * self.cfg.max_position_pct_equity
        if position_usd > max_usd:
            position_usd = max_usd

        # Clamp stop
        stop_pct = max(self.cfg.iv_stop_min_pct, min(stop_pct, self.cfg.iv_stop_max_pct))

        # Kill trade check: if stop exceeds max and no exit edge
        kill = False
        kill_reason = ""
        if stop_pct >= self.cfg.iv_stop_max_pct and charm_signal and charm_signal.action == SignalAction.EXIT:
            kill = True
            kill_reason = (
                f"Stop widened to max ({stop_pct*100:.1f}%) AND charm decay signal active — "
                "risk/reward deteriorated; recommend no new position."
            )

        return TradeRiskResult(
            stop_pct=stop_pct,
            size_multiplier=size_multiplier,
            position_usd=position_usd,
            risk_of_ruin=ror,
            win_rate_used=wr,
            payoff_ratio_used=pr,
            signals=signals,
            kill_trade=kill,
            kill_reason=kill_reason,
        )

    # ======================================================================
    # PUBLIC API — Live Position Monitoring
    # ======================================================================

    def evaluate_live_position(
        self,
        spot: float,
        strike: float,
        days_to_expiry: int,
        option_type: str,
        iv: float,
        entry_price: float,
        current_option_price: float,
        rate: float = 0.05,
        dividend_yield: float = 0.0,
        current_stop_pct: float = 0.02,
        unrealised_pnl_pct: float = 0.0,
        gex_data: Optional[dict] = None,
        iv_surface_data: Optional[dict] = None,
        portfolio_gamma_exposure: Optional[float] = None,
    ) -> LivePositionAction:
        """Evaluate a live position for intelligent exit / adjustment decisions.

        Parameters
        ----------
        spot : float
            Current underlying price.
        strike : float
            Option strike price.
        days_to_expiry : int
            Calendar days to expiry.
        option_type : str
            "call" or "put".
        iv : float
            Current implied volatility.
        entry_price : float
            Price at which the option was purchased.
        current_option_price : float
            Current mid / mark price of the option.
        rate, dividend_yield : float
            Rate and continuous dividend yield.
        current_stop_pct : float
            Current stop-loss percentage (may have been set at entry).
        unrealised_pnl_pct : float
            Current P&L as % of entry cost (positive = profit).
        gex_data : dict, optional
            Gamma exposure snapshot.
        iv_surface_data : dict, optional
            IV surface snapshot.
        portfolio_gamma_exposure : float, optional
            Portfolio-level GEX.

        Returns
        -------
        LivePositionAction
        """
        signals: List[RiskSignal] = []
        tau = max(days_to_expiry / 365.0, 1 / 365.0)

        # Compute Greeks
        g = self._greek()
        delta = g.delta(spot, strike, rate, dividend_yield, iv, tau, option_type)
        gamma_val = g.gamma(spot, strike, rate, dividend_yield, iv, tau)
        vega = g.vega(spot, strike, rate, dividend_yield, iv, tau)

        hog = self._hog()()
        try:
            ho = hog.compute(
                flag="c" if option_type == "call" else "p",
                S=spot, K=strike, t=tau, r=rate, sigma=iv, q=dividend_yield,
            )
            vanna = ho.vanna
            # hol_greeks computes charm based on flag ('c'/'p'), already put-aware
            charm = ho.charm
            color = ho.color
        except Exception:
            vanna = g.vanna(spot, strike, rate, dividend_yield, iv, tau)
            charm = g.charm(spot, strike, rate, dividend_yield, iv, tau, option_type)
            color = g.color(spot, strike, rate, dividend_yield, iv, tau)

        should_exit = False
        exit_reason = ""
        suggested_stop_pct = current_stop_pct
        suggested_stop_price = None
        size_adjustment = 1.0
        urgency = "normal"

        # ---------------------------------------------------------------
        # 1. Dynamic Stop Recalculation
        # ---------------------------------------------------------------
        iv_signal = self._iv_stop_adjustment(iv, iv_surface_data, current_stop_pct)
        if iv_signal:
            suggested_stop_pct *= iv_signal.adjustment_factor
            signals.append(iv_signal)

        regime_signal = self._vol_regime_adjustment(iv, iv_surface_data)
        if regime_signal:
            suggested_stop_pct *= regime_signal.adjustment_factor
            signals.append(regime_signal)

        gex_signal = self._gex_adjustment(gex_data, portfolio_gamma_exposure, spot)
        if gex_signal:
            suggested_stop_pct *= gex_signal.adjustment_factor
            signals.append(gex_signal)

        # Clamp
        suggested_stop_pct = max(
            self.cfg.iv_stop_min_pct, min(suggested_stop_pct, self.cfg.iv_stop_max_pct)
        )

        # Convert to price
        # Stop price = entry * (1 - stop_pct) for long options
        suggested_stop_price = entry_price * (1 - suggested_stop_pct)

        # ---------------------------------------------------------------
        # 2. Charm Decay Exit
        # ---------------------------------------------------------------
        charm_signal = self._charm_exit_signal(charm, delta, days_to_expiry)
        if charm_signal:
            signals.append(charm_signal)
            if charm_signal.action == SignalAction.EXIT:
                should_exit = True
                exit_reason = charm_signal.reason
                urgency = "high"

        # ---------------------------------------------------------------
        # 3. GEX Flip — Emergency Tightening
        # ---------------------------------------------------------------
        gex_exit_signal = self._gex_exit_signal(gex_data, portfolio_gamma_exposure)
        if gex_exit_signal:
            signals.append(gex_exit_signal)
            if gex_exit_signal.action == SignalAction.EXIT:
                should_exit = True
                exit_reason = gex_exit_signal.reason
                urgency = "critical"
            elif gex_exit_signal.action == SignalAction.TIGHTEN_STOP:
                # Force stop to be tighter
                forced_stop = suggested_stop_pct * gex_exit_signal.adjustment_factor
                suggested_stop_pct = max(self.cfg.iv_stop_min_pct, forced_stop)
                suggested_stop_price = entry_price * (1 - suggested_stop_pct)

        # ---------------------------------------------------------------
        # 4. Vanna Hedge — Size Adjustment
        # ---------------------------------------------------------------
        vanna_signal = self._vanna_size_adjustment(vanna, days_to_expiry)
        if vanna_signal:
            signals.append(vanna_signal)
            size_adjustment = vanna_signal.adjustment_factor

        # ---------------------------------------------------------------
        # 5. P&L-based urgency
        # ---------------------------------------------------------------
        if unrealised_pnl_pct < -0.30:
            # Deep loss — check if stop should be enforced immediately
            should_exit = True
            exit_reason = (
                f"Unrealised P&L {unrealised_pnl_pct*100:.1f}% exceeds -30% "
                f"threshold — forced exit."
            )
            urgency = "critical"

        # Compute suggested stop price from the final pct
        if not should_exit:
            suggested_stop_price = entry_price * (1 - suggested_stop_pct)

        return LivePositionAction(
            should_exit=should_exit,
            exit_reason=exit_reason,
            suggested_stop_price=suggested_stop_price,
            suggested_stop_pct=suggested_stop_pct,
            size_adjustment=size_adjustment,
            signals=signals,
            urgency=urgency,
        )

    # ======================================================================
    # INTERNAL — Signal Generators
    # ======================================================================

    def _iv_stop_adjustment(
        self,
        iv: float,
        surface_data: Optional[dict],
        base_stop: float,
    ) -> Optional[RiskSignal]:
        """Widen/tighten stop based on IV percentile or z-score.

        If ``surface_data`` has ``iv_percentile``, use that directly.
        Otherwise, compute a simple z-score against ``iv_atm_reference_sigma``.
        """
        if surface_data and "iv_percentile" in surface_data:
            pct = surface_data["iv_percentile"]
            # Map percentile to adjustment factor linearly
            # pctl 0 → min (tighten), pctl 100 → max (widen)
            if pct >= self.cfg.iv_percentile_high:
                # High IV: widen stop proportionally
                # factor = 1 + (pct - 75)/25 * (max/base - 1)
                max_factor = self.cfg.iv_stop_max_pct / self.cfg.iv_stop_base_pct
                factor = 1.0 + ((pct - self.cfg.iv_percentile_high) /
                                (100 - self.cfg.iv_percentile_high)) * (max_factor - 1.0)
                factor = min(factor, max_factor)
                return RiskSignal(
                    signal_type=SignalType.IV_STOP,
                    action=SignalAction.WIDEN_STOP,
                    current_value=pct,
                    threshold=self.cfg.iv_percentile_high,
                    adjustment_factor=factor,
                    reason=(
                        f"IV percentile {pct:.0f}% > {self.cfg.iv_percentile_high:.0f}%. "
                        f"Stop widened by {factor:.2f}x (more noise expected)."
                    ),
                    metadata={"iv_percentile": pct, "base_stop": base_stop},
                )
            elif pct <= self.cfg.iv_percentile_low:
                # Low IV: tighten stop
                min_factor = self.cfg.iv_stop_min_pct / self.cfg.iv_stop_base_pct
                factor = 1.0 - ((self.cfg.iv_percentile_low - pct) /
                                self.cfg.iv_percentile_low) * (1.0 - min_factor)
                factor = max(factor, min_factor)
                return RiskSignal(
                    signal_type=SignalType.IV_STOP,
                    action=SignalAction.TIGHTEN_STOP,
                    current_value=pct,
                    threshold=self.cfg.iv_percentile_low,
                    adjustment_factor=factor,
                    reason=(
                        f"IV percentile {pct:.0f}% < {self.cfg.iv_percentile_low:.0f}%. "
                        f"Stop tightened to {factor:.2f}x (less noise expected)."
                    ),
                    metadata={"iv_percentile": pct, "base_stop": base_stop},
                )

        # Fallback: z-score against reference sigma
        ref = self.cfg.iv_atm_reference_sigma
        if ref > 0:
            z = (iv - ref) / ref
            if abs(z) > 0.5:
                # z > 0.5 → high vol → widen; z < -0.5 → low vol → tighten
                factor = 1.0 + 0.3 * z  # ±30% per z-score unit
                factor = max(0.5, min(factor, 2.0))
                action = SignalAction.WIDEN_STOP if z > 0 else SignalAction.TIGHTEN_STOP
                return RiskSignal(
                    signal_type=SignalType.IV_STOP,
                    action=action,
                    current_value=iv,
                    threshold=ref,
                    adjustment_factor=factor,
                    reason=f"IV {iv*100:.1f}% vs reference {ref*100:.1f}% (z={z:+.2f}). Stop {('widened' if z > 0 else 'tightened')} by {factor:.2f}x.",
                )

        return None

    def _vol_regime_adjustment(
        self, iv: float, surface_data: Optional[dict]
    ) -> Optional[RiskSignal]:
        """Adjust stop based on vol regime (IV / rolling average IV)."""
        if not surface_data:
            return None

        avg_iv = surface_data.get("avg_iv") or surface_data.get("iv_20d_avg")
        if not avg_iv or avg_iv <= 0:
            return None

        ratio = iv / avg_iv

        if ratio >= self.cfg.vol_regime_high_threshold:
            factor = self.cfg.vol_regime_high_stop_mult
            return RiskSignal(
                signal_type=SignalType.VOL_REGIME,
                action=SignalAction.WIDEN_STOP,
                current_value=ratio,
                threshold=self.cfg.vol_regime_high_threshold,
                adjustment_factor=factor,
                reason=(
                    f"Vol regime HIGH: IV/avg = {ratio:.2f} > {self.cfg.vol_regime_high_threshold}. "
                    f"Stop widened by {factor:.2f}x."
                ),
                metadata={"iv": iv, "avg_iv": avg_iv, "ratio": ratio},
            )
        elif ratio <= self.cfg.vol_regime_low_threshold:
            factor = self.cfg.vol_regime_low_stop_mult
            return RiskSignal(
                signal_type=SignalType.VOL_REGIME,
                action=SignalAction.TIGHTEN_STOP,
                current_value=ratio,
                threshold=self.cfg.vol_regime_low_threshold,
                adjustment_factor=factor,
                reason=(
                    f"Vol regime LOW: IV/avg = {ratio:.2f} < {self.cfg.vol_regime_low_threshold}. "
                    f"Stop tightened to {factor:.2f}x."
                ),
                metadata={"iv": iv, "avg_iv": avg_iv, "ratio": ratio},
            )

        return None

    def _gex_adjustment(
        self,
        gex_data: Optional[dict],
        portfolio_gex: Optional[float],
        spot: float,
    ) -> Optional[RiskSignal]:
        """Tighten stops when GEX is negative (dealers short gamma → moves accelerate).

        Uses either:
        - gex_data["total_gex"] from GammaPositioning.calculate_gex(), or
        - portfolio_gex from portfolio-level aggregation.
        """
        total_gex = None
        if gex_data and "total_gex" in gex_data:
            total_gex = gex_data["total_gex"]
        elif portfolio_gex is not None:
            total_gex = portfolio_gex

        if total_gex is None:
            return None

        if total_gex < -self.cfg.gex_magnitude_threshold:
            factor = self.cfg.gex_tighten_factor
            regime = gex_data.get("regime", "negative_gamma") if gex_data else "negative_gamma"
            flip = gex_data.get("flip_strike") if gex_data else None
            flip_str = f" (flip near ${flip:.0f})" if flip else ""
            return RiskSignal(
                signal_type=SignalType.GEX_EXIT,
                action=SignalAction.TIGHTEN_STOP,
                current_value=total_gex,
                threshold=-self.cfg.gex_magnitude_threshold,
                adjustment_factor=factor,
                reason=(
                    f"GEX is strongly negative (${total_gex:,.0f}){flip_str} — "
                    f"dealers short gamma, moves accelerate. Stop tightened to {factor:.0f}% of base."
                ),
                metadata={"total_gex": total_gex, "regime": regime, "flip_strike": flip},
            )
        elif total_gex > self.cfg.gex_magnitude_threshold:
            # Positive GEX: mild widening (pinning expected, less tail risk)
            factor = 1.0 + 0.1  # 10 % wider
            return RiskSignal(
                signal_type=SignalType.GEX_EXIT,
                action=SignalAction.WIDEN_STOP,
                current_value=total_gex,
                threshold=self.cfg.gex_magnitude_threshold,
                adjustment_factor=factor,
                reason=(
                    f"GEX strongly positive (${total_gex:,.0f}) — pinning expected, "
                    f"slight stop widening ({factor:.1f}x)."
                ),
                metadata={"total_gex": total_gex},
            )

        return None

    def _gex_exit_signal(
        self,
        gex_data: Optional[dict],
        portfolio_gex: Optional[float],
    ) -> Optional[RiskSignal]:
        """Check if GEX regime flip demands an immediate exit.

        This is a more aggressive version of _gex_adjustment — used for
        live position monitoring where a regime flip can be catastrophic.
        """
        total_gex = None
        regime = None
        if gex_data:
            total_gex = gex_data.get("total_gex")
            regime = gex_data.get("regime")
        if portfolio_gex is not None:
            total_gex = portfolio_gex

        if total_gex is None:
            return None

        # Check if GEX just flipped negative
        if regime == "negative_gamma" or total_gex < -self.cfg.gex_magnitude_threshold * 2:
            return RiskSignal(
                signal_type=SignalType.GEX_EXIT,
                action=SignalAction.EXIT,
                current_value=total_gex,
                threshold=-self.cfg.gex_magnitude_threshold * 2,
                adjustment_factor=0.0,
                reason=(
                    f"GEX regime FLIP to negative (${total_gex:,.0f}) — "
                    "dealers forced to sell into declines. Recommended exit."
                ),
                metadata={"total_gex": total_gex, "regime": regime or "negative_gamma"},
            )
        elif total_gex < -self.cfg.gex_magnitude_threshold:
            return RiskSignal(
                signal_type=SignalType.GEX_EXIT,
                action=SignalAction.TIGHTEN_STOP,
                current_value=total_gex,
                threshold=-self.cfg.gex_magnitude_threshold,
                adjustment_factor=self.cfg.gex_tighten_factor,
                reason=(
                    f"GEX negative (${total_gex:,.0f}) — "
                    "acceleration risk. Tightening stops."
                ),
                metadata={"total_gex": total_gex},
            )

        return None

    def _charm_exit_signal(
        self,
        charm: float,
        delta: float,
        days_to_expiry: int,
    ) -> Optional[RiskSignal]:
        """Detect when charm decay is eroding the position's directional edge.

        Charm measures ∂Δ/∂τ — how much delta changes per unit time.
        When |charm| is large relative to |delta|, the position is rapidly
        losing (or gaining) its directional exposure, which may invalidate
        the trade thesis.
        """
        if days_to_expiry < self.cfg.charm_min_dte_for_exit:
            return None  # Too close to expiry, theta/charm dominate anyway

        abs_charm = abs(charm)
        abs_delta = abs(delta)

        # Absolute charm threshold
        if abs_charm < self.cfg.charm_abs_threshold:
            return None

        # Relative charm: is charm eroding more than 30% of delta per day?
        if abs_delta > 0.01 and abs_charm / abs_delta > self.cfg.charm_exit_pct_of_delta:
            decay_pct = (abs_charm / abs_delta) * 100
            return RiskSignal(
                signal_type=SignalType.CHARM_EXIT,
                action=SignalAction.EXIT,
                current_value=abs_charm,
                threshold=self.cfg.charm_abs_threshold,
                adjustment_factor=0.0,
                reason=(
                    f"Charm decay {decay_pct:.1f}% of delta per day "
                    f"(|charm|={abs_charm:.4f}, |delta|={abs_delta:.4f}) — "
                    "directional edge eroding fast. Consider exit."
                ),
                metadata={
                    "charm": charm,
                    "delta": delta,
                    "decay_pct_of_delta": decay_pct,
                    "dte": days_to_expiry,
                },
            )

        # High absolute charm (near expiry, deep ITM/OTM)
        if abs_charm > self.cfg.charm_abs_threshold * 2 and days_to_expiry <= 14:
            direction = "approaching 1" if (charm > 0 and delta > 0) or (charm < 0 and delta < 0) else "approaching 0"
            return RiskSignal(
                signal_type=SignalType.CHARM_EXIT,
                action=SignalAction.EXIT,
                current_value=abs_charm,
                threshold=self.cfg.charm_abs_threshold * 2,
                adjustment_factor=0.0,
                reason=(
                    f"Extreme charm ({charm:.4f}) at {days_to_expiry}DTE — "
                    f"delta rapidly {direction}. Take profits / cut losses."
                ),
                metadata={"charm": charm, "delta": delta, "dte": days_to_expiry},
            )

        return None

    def _vanna_size_adjustment(
        self, vanna: float, days_to_expiry: int
    ) -> Optional[RiskSignal]:
        """Adjust position size when vanna exposure is high.

        High |vanna| means delta will shift significantly when vol changes.
        This increases gamma-gamma risk (double convexity) and can cause
        sudden delta imbalances. Reduce position size to compensate.
        """
        abs_vanna = abs(vanna)

        # Only care when vanna is above threshold
        if abs_vanna < self.cfg.vanna_hedge_abs_threshold:
            return None

        # Scale reduction by vanna magnitude
        # vanna=0.04 → factor=0.70, vanna=0.08 → factor=0.49, etc.
        excess = abs_vanna - self.cfg.vanna_hedge_abs_threshold
        reduction = self.cfg.vanna_size_reduction_pct * (excess / self.cfg.vanna_hedge_abs_threshold + 1)
        reduction = min(reduction, 0.50)  # Never reduce more than 50 %
        factor = 1.0 - reduction

        # Near expiry, vanna matters more (vol shifts are imminent)
        if days_to_expiry <= self.cfg.vanna_recalc_after_dte:
            factor *= 0.85  # Additional 15 % reduction near expiry

        return RiskSignal(
            signal_type=SignalType.VANNA_HEDGE,
            action=SignalAction.REDUCE_SIZE,
            current_value=abs_vanna,
            threshold=self.cfg.vanna_hedge_abs_threshold,
            adjustment_factor=factor,
            reason=(
                f"Vanna exposure {vanna:.4f} (|vanna|={abs_vanna:.4f}) above threshold "
                f"{self.cfg.vanna_hedge_abs_threshold:.4f} — delta will shift on vol moves. "
                f"Position size reduced to {factor*100:.0f}%."
            ),
            metadata={
                "vanna": vanna,
                "dte": days_to_expiry,
                "reduction_pct": reduction * 100,
            },
        )

    # ======================================================================
    # RISK OF RUIN
    # ======================================================================

    def _risk_of_ruin(self, win_rate: float, payoff_ratio: float) -> float:
        """Compute probability of ruin using the closed-form formula.

        Uses the classic risk-of-ruin formula for a fixed-fraction bettor:

            P(ruin) = ((1-edge) / (1+edge))^k

        where edge = 2*win_rate - 1 and k = account / (bet * risk_frac).

        For the simplified version (Carr & Madan 1998):

            P(ruin) ≈ exp(-2 * R * edge / sigma²)

        where R = account risk budget, edge = WR*payoff - (1-WR), sigma² = variance.

        We use a simpler approximation that is widely used in practice:

            q = 1 - win_rate
            p = win_rate
            RR = payoff_ratio
            P(ruin) = (q / p)^(bankroll_units / (RR * bet_size))

        For a single trade, we compute the marginal contribution to ruin probability.
        """
        p = max(0.01, min(0.99, win_rate))
        q = 1.0 - p
        rr = max(0.1, payoff_ratio)

        # Edge per trade
        edge = p * rr - q  # Expected value per unit risked

        if edge <= 0:
            # Negative expectation — high ruin risk
            # Approximate: P(ruin) ≈ 1 - edge_normalized
            return min(0.99, 1.0 - edge)  # crude bound

        # Number of "units" of risk to ruin
        # Using Kelly: optimal fraction = edge / variance
        # Variance of payoff per unit risk = p * rr² + q * 1² - edge²
        variance = p * rr**2 + q * 1.0**2 - edge**2

        if variance <= 0:
            return 0.01  # shouldn't happen

        # Risk of ruin approximation (Thorp 2006):
        #   P(ruin) ≈ exp(-2 * edge / variance * risk_units)
        # For 1 unit risk per trade with target_ruin_rate:
        #   risk_units = -ln(target) * variance / (2 * edge)
        # We invert: given 1 trade, what's the ruin contribution?
        risk_units = self.cfg.risk_per_trade_pct * 100  # normalize

        exponent = -2.0 * edge * risk_units / variance
        ruin_prob = math.exp(exponent)

        # Clamp to reasonable range
        return max(0.001, min(0.99, ruin_prob))

    def _kelly_fraction(self, win_rate: float, payoff_ratio: float) -> float:
        """Kelly criterion optimal fraction.

        f* = (p * b - q) / b

        where p = win_rate, q = 1-p, b = payoff_ratio.
        Capped at 0.5 (half-Kelly for safety).
        """
        p = max(0.01, min(0.99, win_rate))
        q = 1.0 - p
        b = max(0.1, payoff_ratio)

        if b <= 0:
            return 0.0

        f_star = (p * b - q) / b
        # Half-Kelly for safety
        return max(0.0, min(0.5, f_star * 0.5))

    # ======================================================================
    # UTILITY
    # ======================================================================

    def compute_dynamic_stop(
        self,
        base_stop_pct: float,
        spot: float,
        strike: float,
        days_to_expiry: int,
        option_type: str,
        iv: float,
        rate: float = 0.05,
        dividend_yield: float = 0.0,
        gex_data: Optional[dict] = None,
        iv_surface_data: Optional[dict] = None,
        portfolio_gamma_exposure: Optional[float] = None,
    ) -> Tuple[float, List[RiskSignal]]:
        """Compute the final dynamic stop-loss percentage.

        Convenience method for callers who just need the stop value
        without the full trade evaluation.

        Returns
        -------
        (stop_pct, signals)
            Final stop percentage and list of adjustment signals applied.
        """
        signals: List[RiskSignal] = []
        stop = base_stop_pct

        iv_signal = self._iv_stop_adjustment(iv, iv_surface_data, stop)
        if iv_signal:
            stop *= iv_signal.adjustment_factor
            signals.append(iv_signal)

        regime_signal = self._vol_regime_adjustment(iv, iv_surface_data)
        if regime_signal:
            stop *= regime_signal.adjustment_factor
            signals.append(regime_signal)

        gex_signal = self._gex_adjustment(gex_data, portfolio_gamma_exposure, spot)
        if gex_signal:
            stop *= gex_signal.adjustment_factor
            signals.append(gex_signal)

        stop = max(self.cfg.iv_stop_min_pct, min(stop, self.cfg.iv_stop_max_pct))
        return stop, signals

    def get_position_greeks_summary(
        self,
        spot: float,
        strike: float,
        days_to_expiry: int,
        option_type: str,
        iv: float,
        rate: float = 0.05,
        dividend_yield: float = 0.0,
    ) -> dict:
        """Compute a full Greek summary including higher-order Greeks.

        Useful for logging / dashboard display alongside risk signals.
        """
        tau = max(days_to_expiry / 365.0, 1 / 365.0)
        g = self._greek()

        result = {
            "delta": g.delta(spot, strike, rate, dividend_yield, iv, tau, option_type),
            "gamma": g.gamma(spot, strike, rate, dividend_yield, iv, tau),
            "theta": g.theta(spot, strike, rate, dividend_yield, iv, tau, option_type),
            "vega": g.vega(spot, strike, rate, dividend_yield, iv, tau),
            "rho": g.rho(spot, strike, rate, dividend_yield, iv, tau, option_type),
            "vanna": g.vanna(spot, strike, rate, dividend_yield, iv, tau),
            "charm": g.charm(spot, strike, rate, dividend_yield, iv, tau, option_type),
            "vomma": g.vomma(spot, strike, rate, dividend_yield, iv, tau),
            "speed": g.speed(spot, strike, rate, dividend_yield, iv, tau),
        }

        # Higher-order via hol_greeks
        hog = self._hog()()
        try:
            ho = hog.compute(
                flag="c" if option_type == "call" else "p",
                S=spot, K=strike, t=tau, r=rate, sigma=iv, q=dividend_yield,
            )
            result["zomma"] = ho.zomma
            result["color"] = ho.color
            result["ultima"] = ho.ultima
            result["veta"] = ho.veta
        except Exception:
            pass

        return result


# ---------------------------------------------------------------------------
# Module-level singleton for convenience
# ---------------------------------------------------------------------------

_default_layer: Optional[IntelligentRiskLayer] = None


def get_intelligent_risk_layer(
    config: Optional[IntelligentRiskConfig] = None,
) -> IntelligentRiskLayer:
    """Get or create the module-level IntelligentRiskLayer singleton."""
    global _default_layer
    if _default_layer is None or config is not None:
        _default_layer = IntelligentRiskLayer(config)
    return _default_layer
