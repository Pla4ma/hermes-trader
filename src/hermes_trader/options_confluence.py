#!/usr/bin/env python3
"""
Options Confluence Scanner & Scorer
====================================
The MAIN entry point for finding high‑probability options trades.

Wires together:
  - hol_greeks      → 22 Greeks (1st + higher‑order, Black76/BS/BSM)
  - iv_surface       → SVI/SSVI surface fitting, skew, term structure, curvature
  - greeks_engine    → First‑order Greeks with full documentation
  - gamma_positioning → GEX, put/call walls, max pain, PCR, UOA
  - auto_trader      → Technical scan_and_score (6‑factor equity confluence)
  - market_regime    → Regime detection (BULL/BEAR × HIGH/LOW VOL)

Scoring dimensions (total 100 pts):
  1. Technical signal  — 25 pts  (price action, momentum, trend alignment)
  2. IV edge           — 25 pts  (cheap vs rich IV, surface anomalies)
  3. Greeks quality    — 20 pts  (gamma, theta, vanna‑charm profile)
  4. Liquidity         — 15 pts  (spread, volume, OI)
  5. Regime & flow     — 15 pts  (GEX regime, PCR, put/call walls)

Usage:
    from hermes_trader.options_confluence import OptionsConfluenceScanner

    scanner = OptionsConfluenceScanner()
    results = scanner.scan("SPY")
    # results is a ConfluenceResult with ranked candidates

    # Quick entry point:
    from hermes_trader.options_confluence import scan_options_confluence
    ranked = scan_options_confluence("SPY")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("hermes_trader.options_confluence")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WATCHLIST = ["SPY", "QQQ", "AAPL", "NVDA", "AMZN", "META", "TSLA", "AMD"]
MAX_CONTRACT_COST = 250.0   # hard ceiling per contract (mid × 100)
MIN_VOLUME = 50
MIN_OPEN_INTEREST = 200
MAX_SPREAD_PCT = 8.0
DEFAULT_MAX_DTE = 45
RISK_FREE_RATE = 0.052  # ~5.2 % as of mid-2025


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FullGreeks:
    """All 22 computed Greeks for a single option."""
    # Foundational
    d1: float = 0.0
    d2: float = 0.0
    price: float = 0.0
    # 1st order
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    lambda_: float = 0.0
    epsilon: float = 0.0
    # 2nd order
    vanna: float = 0.0
    charm: float = 0.0
    vomma: float = 0.0
    veta: float = 0.0
    vera: float = 0.0
    # 3rd order
    speed: float = 0.0
    zomma: float = 0.0
    color: float = 0.0
    ultima: float = 0.0

    def to_dict(self) -> dict:
        return {
            "d1": self.d1, "d2": self.d2, "price": self.price,
            "delta": self.delta, "gamma": self.gamma, "theta": self.theta,
            "vega": self.vega, "rho": self.rho, "lambda_": self.lambda_,
            "epsilon": self.epsilon,
            "vanna": self.vanna, "charm": self.charm, "vomma": self.vomma,
            "veta": self.veta, "vera": self.vera,
            "speed": self.speed, "zomma": self.zomma, "color": self.color,
            "ultima": self.ultima,
        }


@dataclass
class ScoreBreakdown:
    """Per‑candidate scoring detail."""
    technical: float = 0.0    # /25
    iv_edge: float = 0.0      # /25
    greeks_quality: float = 0.0  # /20
    liquidity: float = 0.0    # /15
    regime_flow: float = 0.0  # /15
    total: float = 0.0        # /100

    @property
    def tier(self) -> str:
        if self.total >= 70:
            return "A"
        if self.total >= 55:
            return "B"
        if self.total >= 40:
            return "C"
        return "D"


@dataclass
class ConfluenceCandidate:
    """A single scored option candidate with full analytics."""
    # Identification
    symbol: str
    underlying: str
    option_type: str  # "call" or "put"
    strike: float
    expiration: str   # YYYY-MM-DD
    dte: int

    # Market data
    bid: float
    ask: float
    mid: float
    cost_per_contract: float
    volume: int
    open_interest: int
    implied_volatility: float

    # Derived
    moneyness_pct: float       # (K − S) / S  × 100  (positive = OTM call)
    spread_pct: float
    cost_efficiency: float     # delta / mid  (directional bang per $)

    # Full Greeks
    greeks: FullGreeks = field(default_factory=FullGreeks)

    # IV surface context
    iv_surface_iv: float = 0.0   # IV from SVI/SSVI fit
    iv_rank: float = 0.0         # percentile rank vs historical
    iv_percentile: float = 0.0
    skew_zscore: float = 0.0
    term_structure_slope: float = 0.0
    smile_curvature: float = 0.0

    # Scoring
    score: ScoreBreakdown = field(default_factory=ScoreBreakdown)

    # Contextual signals
    technical_direction: str = "neutral"
    regime: str = "UNKNOWN"
    gex_regime: str = "unknown"
    entry_signal: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "underlying": self.underlying,
            "option_type": self.option_type, "strike": self.strike,
            "expiration": self.expiration, "dte": self.dte,
            "bid": self.bid, "ask": self.ask, "mid": self.mid,
            "cost_per_contract": self.cost_per_contract,
            "volume": self.volume, "open_interest": self.open_interest,
            "implied_volatility": self.implied_volatility,
            "moneyness_pct": self.moneyness_pct,
            "spread_pct": self.spread_pct,
            "cost_efficiency": round(self.cost_efficiency, 4),
            "greeks": self.greeks.to_dict(),
            "iv_surface_iv": self.iv_surface_iv,
            "iv_rank": self.iv_rank,
            "iv_percentile": self.iv_percentile,
            "skew_zscore": self.skew_zscore,
            "term_structure_slope": self.term_structure_slope,
            "smile_curvature": self.smile_curvature,
            "score": {
                "technical": round(self.score.technical, 1),
                "iv_edge": round(self.score.iv_edge, 1),
                "greeks_quality": round(self.score.greeks_quality, 1),
                "liquidity": round(self.score.liquidity, 1),
                "regime_flow": round(self.score.regime_flow, 1),
                "total": round(self.score.total, 1),
                "tier": self.score.tier,
            },
            "technical_direction": self.technical_direction,
            "regime": self.regime,
            "gex_regime": self.gex_regime,
            "entry_signal": self.entry_signal,
        }


@dataclass
class ConfluenceResult:
    """Top‑level result from the confluence scanner."""
    underlying: str
    spot: float
    scan_time: str
    candidates_found: int
    candidates: List[ConfluenceCandidate]
    regime_context: dict = field(default_factory=dict)
    surface_context: dict = field(default_factory=dict)
    gamma_context: dict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"═══ Options Confluence Scan: {self.underlying} @ ${self.spot:.2f} ═══",
            f"Time: {self.scan_time}",
            f"Candidates: {self.candidates_found}",
            f"Regime: {self.regime_context.get('regime', 'N/A')}",
        ]
        for i, c in enumerate(self.candidates[:10], 1):
            lines.append(
                f"  {i}. [{c.score.tier}] {c.symbol}  "
                f"score={c.score.total:.1f}/100  "
                f"strike={c.strike}  dte={c.dte}  "
                f"mid=${c.mid:.2f}  delta={c.greeks.delta:.3f}  "
                f"gamma={c.greeks.gamma:.4f}  iv={c.implied_volatility:.3f}  "
                f"signal={c.entry_signal}"
            )
        for n in self.notes[:5]:
            lines.append(f"  ℹ {n}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Greeks computation helper
# ---------------------------------------------------------------------------

def _compute_full_greeks(
    S: float, K: float, tau: float, r: float, sigma: float,
    option_type: str, q: float = 0.0,
) -> FullGreeks:
    """
    Compute all 22 Greeks for a single option using the BSM model.

    First‑order Greeks come from BlackScholesGreeks (greeks_engine),
    higher‑order from BlackScholesMertonModel (hol_greeks).
    """
    flag = "c" if option_type == "call" else "p"
    tau_safe = max(tau, 1 / 365)  # avoid division by zero

    # --- First-order via greeks_engine ---
    from .greeks_engine import BlackScholesGreeks as BSG
    delta = BSG.delta(S, K, r, q, sigma, tau_safe, option_type)
    gamma = BSG.gamma(S, K, r, q, sigma, tau_safe)
    theta = BSG.theta(S, K, r, q, sigma, tau_safe, option_type)
    vega = BSG.vega(S, K, r, q, sigma, tau_safe)
    rho = BSG.rho(S, K, r, q, sigma, tau_safe, option_type)
    price = BSG.price(S, K, r, q, sigma, tau_safe, option_type)

    # Lambda (leverage) — may raise if price ~0
    try:
        lambda_ = BSG.lambda_(S, K, r, q, sigma, tau_safe, option_type)
    except Exception:
        lambda_ = 0.0

    epsilon = BSG.epsilon(S, K, r, q, sigma, tau_safe, option_type)
    d1 = BSG.d1(S, K, r, q, sigma, tau_safe)
    d2 = BSG.d2(S, K, r, q, sigma, tau_safe)

    # --- Higher-order via hol_greeks ---
    from .hol_greeks import compute_hol_greeks
    hol = compute_hol_greeks(S=S, K=K, t=tau_safe, r=r, sigma=sigma, q=q, flag=flag)

    # Vera (from greeks_engine — not in hol_greeks)
    vera = BSG.vera(S, K, r, q, sigma, tau_safe)

    return FullGreeks(
        d1=d1, d2=d2, price=price,
        delta=delta, gamma=gamma, theta=theta, vega=vega,
        rho=rho, lambda_=lambda_, epsilon=epsilon,
        vanna=hol.vanna, charm=hol.charm, vomma=hol.vomma,
        veta=hol.veta, vera=vera,
        speed=hol.speed, zomma=hol.zomma, color=hol.color, ultima=hol.ultima,
    )


# ---------------------------------------------------------------------------
# Technical signal helper (delegates to auto_trader.scan_and_score logic)
# ---------------------------------------------------------------------------

def _get_technical_signal(underlying: str) -> dict:
    """
    Run the same 6‑factor technical analysis that auto_trader.scan_and_score
    uses.  Returns a lightweight dict with direction, score, and key metrics.
    """
    try:
        import yfinance as yf

        data = yf.Ticker(underlying).history(period="3mo")
        if len(data) < 21:
            return {"direction": "neutral", "score": 0, "reason": "insufficient data"}

        close = data["Close"]
        high = data["High"]
        low = data["Low"]
        vol = data["Volume"]
        price = float(close.iloc[-1])

        # Trend
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(min(50, len(close))).mean().iloc[-1])
        trend_bull = price > ma20 and ma20 > ma50
        trend_bear = price < ma20 and ma20 < ma50

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = float((100 - (100 / (1 + gain / loss))).iloc[-1])

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_hist = float(((ema12 - ema26) - (ema12 - ema26).ewm(span=9).mean()).iloc[-1])

        # Momentum
        ret5 = float((close.iloc[-1] / close.iloc[-6] - 1) * 100)
        ret20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100)

        # Volume
        vol_avg = float(vol.rolling(20).mean().iloc[-1])
        vol_ratio = float(vol.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0

        # Position within range
        h20 = float(close.rolling(20).max().iloc[-1])
        l20 = float(close.rolling(20).min().iloc[-1])
        position_in_range = (price - l20) / (h20 - l20) if h20 != l20 else 0.5

        # Composite score  (mirrors auto_trader 6‑factor, scaled to 0‑25)
        score = 0.0
        if trend_bull:
            score += 5
        elif trend_bear:
            score -= 3

        if 40 < rsi < 60:
            score += 3  # healthy
        elif rsi < 30:
            score += 4  # oversold bounce
        elif rsi > 70:
            score -= 2  # overbought risk

        if macd_hist > 0:
            score += 3
        elif macd_hist < 0:
            score -= 1

        if ret5 > 0:
            score += 2
        elif ret5 < -2:
            score -= 2

        if vol_ratio > 1.2:
            score += 1  # volume confirming

        if position_in_range > 0.6:
            score += 2  # in upper range
        elif position_in_range < 0.3:
            score += 1  # potential bounce

        # Clamp to 0-25
        score = max(0, min(25, score))

        direction = "bullish" if score >= 13 else ("bearish" if score <= 7 else "neutral")

        return {
            "direction": direction,
            "score": round(score, 1),
            "price": price,
            "rsi": round(rsi, 1),
            "macd_hist": round(macd_hist, 4),
            "ret5": round(ret5, 2),
            "ret20": round(ret20, 2),
            "vol_ratio": round(vol_ratio, 2),
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
            "position_in_range": round(position_in_range, 3),
        }
    except Exception as e:
        logger.warning("Technical signal failed for %s: %s", underlying, e)
        return {"direction": "neutral", "score": 0, "reason": str(e)}


# ---------------------------------------------------------------------------
# IV surface context helper
# ---------------------------------------------------------------------------

def _build_iv_surface(
    chain_data: List[dict], spot: float, expiries: List[float],
) -> dict:
    """
    Build an IV surface from option chain data and extract trading signals.

    Returns context dict with skew, term structure, curvature per expiry,
    and per‑option IV rank info.
    """
    try:
        from .iv_surface import IVSurface, bs_implied_vol

        surface = IVSurface(spot=spot, rate=RISK_FREE_RATE)

        # Group chain data by expiry and add to surface
        for tte in expiries:
            expiry_chain = [r for r in chain_data if abs(r.get("days_to_expiry", 0) / 365 - tte) < 0.02]
            if len(expiry_chain) >= 5:
                surface.add_option_chain(
                    expiry_chain, tte, forward=spot,
                    min_volume=10, min_open_interest=20, min_bid=0.01,
                )

        if not surface.slices:
            return {"available": False, "reason": "insufficient data for surface"}

        # Fit SVI per expiry
        surface.fit_svi()

        # Fit global SSVI if we have 2+ expiries
        if len(surface.slices) >= 2:
            try:
                surface.fit_ssvi()
            except Exception:
                pass

        # Extract signals from the surface
        sorted_exp = sorted(surface.slices.keys())
        results = {
            "available": True,
            "expiries_fitted": len(surface.slices),
            "expiry_signals": {},
        }

        for exp in sorted_exp:
            try:
                sigs = surface.generate_signals(exp)
                results["expiry_signals"][float(exp)] = sigs
            except Exception:
                continue

        # ATM IVs by expiry
        results["atm_ivs"] = {}
        for exp in sorted_exp:
            try:
                results["atm_ivs"][float(exp)] = round(surface.atm_iv(exp), 4)
            except Exception:
                continue

        return results

    except Exception as e:
        logger.warning("IV surface build failed: %s", e)
        return {"available": False, "reason": str(e)}


# ---------------------------------------------------------------------------
# Confluence scoring engine
# ---------------------------------------------------------------------------

class ConfluenceScorer:
    """
    Multi‑dimensional scoring engine.

    Weights (total 100):
        Technical  25 pts   — Does price action support the option direction?
        IV Edge    25 pts   — Is IV cheap relative to surface / history?
        Greeks     20 pts   — Are the Greeks profile favorable?
        Liquidity  15 pts   — Can we get a clean fill?
        Regime     15 pts   — Does the macro context support this trade?
    """

    def __init__(self, regime_context: dict, surface_context: dict, gamma_context: dict):
        self.regime = regime_context
        self.surface = surface_context
        self.gamma = gamma_context

    def score_candidate(
        self, cand: ConfluenceCandidate, tech: dict,
    ) -> ConfluenceCandidate:
        """Score a single candidate across all 5 dimensions."""
        sb = ScoreBreakdown()

        # ── 1. Technical signal (25 pts) ──────────────────────────────
        sb.technical = self._score_technical(cand, tech)

        # ── 2. IV edge (25 pts) ───────────────────────────────────────
        sb.iv_edge = self._score_iv_edge(cand)

        # ── 3. Greeks quality (20 pts) ────────────────────────────────
        sb.greeks_quality = self._score_greeks(cand)

        # ── 4. Liquidity (15 pts) ─────────────────────────────────────
        sb.liquidity = self._score_liquidity(cand)

        # ── 5. Regime & flow (15 pts) ─────────────────────────────────
        sb.regime_flow = self._score_regime(cand, tech)

        sb.total = sb.technical + sb.iv_edge + sb.greeks_quality + sb.liquidity + sb.regime_flow
        cand.score = sb

        # ── Entry signal synthesis ────────────────────────────────────
        cand.entry_signal = self._synthesize_signal(cand)

        return cand

    # -- Dimension 1: Technical (25 pts) --

    def _score_technical(self, c: ConfluenceCandidate, tech: dict) -> float:
        pts = 0.0
        direction = tech.get("direction", "neutral")

        # Alignment: does option type match technical direction?
        aligned = (
            (c.option_type == "call" and direction == "bullish") or
            (c.option_type == "put" and direction == "bearish")
        )
        if aligned:
            pts += 10
        elif direction == "neutral":
            pts += 4  # neutral is okay for some strategies

        # Technical strength (scale 0-25 → 0-10 pts)
        tech_score = tech.get("score", 0)
        pts += min(10, tech_score * 0.4)

        # Position in range bonus
        pir = tech.get("position_in_range", 0.5)
        if c.option_type == "call" and pir > 0.6:
            pts += 2  # strong uptrend momentum
        elif c.option_type == "put" and pir < 0.4:
            pts += 2  # downtrend or overextended
        elif 0.3 < pir < 0.7:
            pts += 1  # healthy range

        # RSI context
        rsi = tech.get("rsi", 50)
        if c.option_type == "call" and rsi < 40:
            pts += 3  # oversold bounce potential
        elif c.option_type == "put" and rsi > 65:
            pts += 3  # overbought pullback potential

        return min(25, pts)

    # -- Dimension 2: IV Edge (25 pts) --

    def _score_iv_edge(self, c: ConfluenceCandidate) -> float:
        pts = 0.0
        iv = c.implied_volatility
        surface_iv = c.iv_surface_iv

        # IV relative to surface
        if surface_iv > 0:
            iv_ratio = iv / surface_iv
            if 0.85 <= iv_ratio <= 1.0:
                pts += 8  # slightly cheap vs surface — sweet spot
            elif iv_ratio < 0.85:
                pts += 10  # cheap — great entry
            elif iv_ratio <= 1.15:
                pts += 4  # fair value
            else:
                pts += 0  # rich — no edge

        # IV absolute level (moderate IV best for long options)
        if 0.15 < iv < 0.30:
            pts += 5  # ideal range
        elif 0.10 < iv < 0.40:
            pts += 3
        elif iv < 0.10:
            pts += 1  # too quiet — may not move

        # Skew signal
        if abs(c.skew_zscore) > 1.5:
            # Extreme skew — potential mean reversion
            if c.option_type == "put" and c.skew_zscore < -1.5:
                pts += 4  # puts are cheap relative to calls
            elif c.option_type == "call" and c.skew_zscore > 1.5:
                pts += 4  # calls are cheap relative to puts
        elif abs(c.skew_zscore) > 0.8:
            pts += 2

        # Term structure
        ts = c.term_structure_slope
        if ts < -0.01 and c.dte < 21:
            pts += 3  # backwardation — near‑term vol elevated
        elif ts > 0.01 and c.dte > 30:
            pts += 2  # contango — longer‑term relatively cheap

        # Smile curvature bonus (buying straddle/strangle when curvature low)
        if c.smile_curvature < 0.005:
            pts += 2  # flat smile — wings are cheap

        return min(25, pts)

    # -- Dimension 3: Greeks Quality (20 pts) --

    def _score_greeks(self, c: ConfluenceCandidate) -> float:
        pts = 0.0
        g = c.greeks

        # Gamma — high gamma = convexity (good for buyers)
        if g.gamma > 0.05:
            pts += 5
        elif g.gamma > 0.03:
            pts += 4
        elif g.gamma > 0.01:
            pts += 2

        # Theta — low theta decay (buyer wants small theta)
        daily_theta = abs(g.theta)  # already per day
        if daily_theta < 0.02:
            pts += 4
        elif daily_theta < 0.05:
            pts += 3
        elif daily_theta < 0.10:
            pts += 1

        # Gamma/Theta ratio — higher = better risk/reward for buyers
        if daily_theta > 0:
            gt_ratio = g.gamma / daily_theta
            if gt_ratio > 5:
                pts += 3
            elif gt_ratio > 2:
                pts += 2
            elif gt_ratio > 1:
                pts += 1

        # Delta sweet spot
        abs_delta = abs(g.delta)
        if 0.25 <= abs_delta <= 0.40:
            pts += 3  # optimal leverage
        elif 0.15 <= abs_delta <= 0.50:
            pts += 2
        elif abs_delta >= 0.10:
            pts += 1

        # Vanna (dealer hedging flow indicator)
        abs_vanna = abs(g.vanna)
        if abs_vanna > 0.01:
            pts += 2  # meaningful vanna exposure

        # Charm — delta decay (low charm = delta is stable)
        abs_charm = abs(g.charm)
        if abs_charm < 0.005:
            pts += 2  # stable delta

        # Vomma — vol convexity (positive = benefits from vol moves)
        if g.vomma > 0:
            pts += 1

        return min(20, pts)

    # -- Dimension 4: Liquidity (15 pts) --

    def _score_liquidity(self, c: ConfluenceCandidate) -> float:
        pts = 0.0

        # Spread
        if c.spread_pct < 2:
            pts += 5
        elif c.spread_pct < 4:
            pts += 4
        elif c.spread_pct < 6:
            pts += 3
        elif c.spread_pct < 8:
            pts += 1

        # Volume
        if c.volume > 1000:
            pts += 4
        elif c.volume > 500:
            pts += 3
        elif c.volume > 100:
            pts += 2
        elif c.volume > 50:
            pts += 1

        # Open interest
        if c.open_interest > 5000:
            pts += 3
        elif c.open_interest > 1000:
            pts += 2
        elif c.open_interest > 200:
            pts += 1

        # Cost efficiency (delta per dollar)
        if c.cost_efficiency > 0.3:
            pts += 3
        elif c.cost_efficiency > 0.15:
            pts += 2
        elif c.cost_efficiency > 0.08:
            pts += 1

        return min(15, pts)

    # -- Dimension 5: Regime & Flow (15 pts) --

    def _score_regime(self, c: ConfluenceCandidate, tech: dict) -> float:
        pts = 0.0

        # GEX regime alignment
        gex_regime = self.gamma.get("gex", {}).get("regime", "unknown")
        c.gex_regime = gex_regime

        if gex_regime == "positive_gamma":
            # Pinning regime — good for selling premium, okay for ATM calls
            if c.option_type == "call" and abs(c.moneyness_pct) < 3:
                pts += 2  # near ATM, might pin
            else:
                pts += 1
        elif gex_regime == "negative_gamma":
            # Volatile regime — good for buying options (long gamma)
            pts += 4

        # Market regime
        regime_name = self.regime.get("regime", "UNKNOWN")
        c.regime = regime_name
        sizing_mult = self.regime.get("sizing_multiplier", 0.75)

        if "BULL" in regime_name and c.option_type == "call":
            pts += 4
        elif "BEAR" in regime_name and c.option_type == "put":
            pts += 4
        elif "NEUTRAL" in regime_name:
            pts += 2

        # Put/call ratio
        pcr = self.gamma.get("put_call_ratio", {})
        pcr_vol = pcr.get("pcr_volume", 1.0)
        contrarian = pcr.get("contrarian_signal", "NEUTRAL")

        if "BULLISH" in contrarian and c.option_type == "call":
            pts += 2
        elif "BEARISH" in contrarian and c.option_type == "put":
            pts += 2

        # Max pain proximity
        max_pain = self.gamma.get("max_pain", {})
        mp_strike = max_pain.get("max_pain_strike", 0)
        if mp_strike > 0:
            dist_to_pain = abs(c.strike - mp_strike) / c.strike * 100
            if dist_to_pain < 2:
                pts += 2  # strike near max pain
            elif dist_to_pain < 5:
                pts += 1

        # Put/call walls
        walls = self.gamma.get("walls", {})
        nearest_put = walls.get("nearest_put", {})
        nearest_call = walls.get("nearest_call", {})

        if c.option_type == "put" and nearest_put:
            wall_dist = nearest_put.get("distance_pct", 99)
            if wall_dist < 2:
                pts += 2  # near put wall support

        if c.option_type == "call" and nearest_call:
            wall_dist = nearest_call.get("distance_pct", 99)
            if wall_dist < 2:
                pts += 2  # near call wall resistance

        return min(15, pts)

    # -- Signal synthesis --

    def _synthesize_signal(self, c: ConfluenceCandidate) -> str:
        """Create a human‑readable entry signal string."""
        parts = []

        # Direction alignment
        if c.score.technical >= 15:
            parts.append("TECH_ALIGNED")
        elif c.score.technical >= 10:
            parts.append("TECH_OK")

        # IV edge
        if c.iv_surface_iv > 0:
            ratio = c.implied_volatility / c.iv_surface_iv
            if ratio < 0.90:
                parts.append("IV_CHEAP")
            elif ratio > 1.10:
                parts.append("IV_RICH")

        # Greeks
        if c.score.greeks_quality >= 15:
            parts.append("GREEKS_PRIME")
        elif c.score.greeks_quality >= 10:
            parts.append("GREEKS_GOOD")

        # Regime
        gex = self.gamma.get("gex", {}).get("regime", "unknown")
        if gex == "negative_gamma":
            parts.append("SHORT_GAMMA_REGIME")

        # Liquidity
        if c.score.liquidity >= 12:
            parts.append("HIGH_LIQ")

        # Overall conviction
        if c.score.total >= 70:
            parts.insert(0, "🔥 A_TIER")
        elif c.score.total >= 55:
            parts.insert(0, "⭐ B_TIER")

        return " | ".join(parts) if parts else "STANDARD"


# ---------------------------------------------------------------------------
# Main scanner class
# ---------------------------------------------------------------------------

class OptionsConfluenceScanner:
    """
    Institutional‑grade options scanner.

    Pulls chain data from yfinance, computes all 22 Greeks via hol_greeks,
    builds an IV surface, gathers gamma/positioning context, scores every
    candidate across 5 dimensions, and returns ranked results.
    """

    def __init__(self):
        self._spot_cache = {}

    # ---- data fetching ----

    def _get_spot(self, symbol: str) -> float:
        """Get current spot price via yfinance."""
        import yfinance as yf
        return float(yf.Ticker(symbol).fast_info.get("lastPrice", 0))

    def _fetch_chain(
        self, symbol: str, max_dte: int = DEFAULT_MAX_DTE,
    ) -> Tuple[List[dict], float]:
        """
        Fetch option chain from yfinance and normalise into list of dicts.

        Returns (chain_data, spot_price).
        """
        import yfinance as yf
        from datetime import date
        from .greeks_engine import BlackScholesGreeks as BSG

        ticker = yf.Ticker(symbol)
        spot = self._get_spot(symbol)
        today = date.today()
        r = RISK_FREE_RATE

        chain = []
        for exp_str in ticker.options:
            try:
                exp_date = date.fromisoformat(exp_str)
                dte = (exp_date - today).days
            except Exception:
                continue
            if dte > max_dte or dte < 1:
                continue

            opt = ticker.option_chain(exp_str)
            tau = max(dte, 1) / 365.0

            for side_df, is_call in [(opt.calls, True), (opt.puts, False)]:
                for _, row in side_df.iterrows():
                    iv = float(row.get("impliedVolatility", 0))
                    bid = float(row.get("bid", 0))
                    ask = float(row.get("ask", 0))
                    if bid <= 0 or ask <= 0:
                        continue

                    mid = (bid + ask) / 2
                    strike = float(row["strike"])
                    volume = int(row.get("volume", 0) or 0)
                    oi = int(row.get("openInterest", 0) or 0)

                    # Compute Greeks from IV using Black-Scholes
                    opt_type = "call" if is_call else "put"
                    try:
                        delta = abs(float(BSG.delta(spot, strike, r, 0.0, iv, tau, opt_type)))
                        gamma = float(BSG.gamma(spot, strike, r, 0.0, iv, tau))
                        theta = float(BSG.theta(spot, strike, r, 0.0, iv, tau, opt_type))
                        vega = float(BSG.vega(spot, strike, r, 0.0, iv, tau))
                    except Exception:
                        delta = gamma = theta = vega = 0.0

                    chain.append({
                        "symbol": row.get("contractSymbol", ""),
                        "option_type": opt_type,
                        "strike": strike,
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "mid": round(mid, 2),
                        "days_to_expiry": dte,
                        "expiry_date": exp_date.isoformat(),
                        "volume": volume,
                        "open_interest": oi,
                        "implied_volatility": iv,
                        "bsm_delta": delta,
                        "bsm_gamma": gamma,
                        "bsm_theta": theta,
                        "bsm_vega": vega,
                    })

        return chain, spot

    # ---- main scan ----

    def scan(
        self,
        symbol: str = "SPY",
        max_dte: int = DEFAULT_MAX_DTE,
        max_cost: float = MAX_CONTRACT_COST,
        direction: Optional[str] = None,
        top_n: int = 10,
    ) -> ConfluenceResult:
        """
        Full confluence scan for a single underlying.

        Parameters
        ----------
        symbol : str
            Underlying ticker (default "SPY").
        max_dte : int
            Maximum days to expiry.
        max_cost : float
            Maximum mid price per contract.
        direction : str | None
            Force "bullish" / "bearish" / None (auto‑detect from technicals).
        top_n : int
            Number of top candidates to return.

        Returns
        -------
        ConfluenceResult
        """
        logger.info("Starting confluence scan for %s", symbol)

        # ── 1. Fetch chain + spot ────────────────────────────────────
        chain, spot = self._fetch_chain(symbol, max_dte)
        if not chain:
            return ConfluenceResult(
                underlying=symbol, spot=spot,
                scan_time=datetime.utcnow().isoformat(),
                candidates_found=0, candidates=[],
                notes=["No option chain data available"],
            )

        # ── 2. Technical analysis ────────────────────────────────────
        tech = _get_technical_signal(symbol)
        auto_direction = direction or tech.get("direction", "neutral")

        # ── 3. Build IV surface ──────────────────────────────────────
        expiries = sorted(set(r["days_to_expiry"] / 365 for r in chain if r["days_to_expiry"] > 0))
        surface_ctx = _build_iv_surface(chain, spot, expiries)

        # ── 4. Gamma / positioning context ───────────────────────────
        gamma_ctx = {}
        try:
            from .gamma_positioning import GammaPositioning
            gp = GammaPositioning()
            gamma_ctx = gp.full_gamma_analysis(symbol)
        except Exception as e:
            logger.warning("Gamma positioning failed: %s", e)

        # ── 5. Market regime ─────────────────────────────────────────
        regime_ctx = {}
        try:
            from .market_regime import detect_regime
            regime_ctx = detect_regime(symbol)
        except Exception as e:
            logger.warning("Regime detection failed: %s", e)

        # ── 6. Filter chain ──────────────────────────────────────────
        filtered = []
        for r in chain:
            mid = r["mid"]
            cost = mid * 100
            if cost <= 0 or cost > max_cost:
                continue
            if r["volume"] < MIN_VOLUME:
                continue
            # Spread check
            spread = ((r["ask"] - r["bid"]) / mid * 100) if mid > 0 else 999
            if spread > MAX_SPREAD_PCT:
                continue
            # Delta check — need some directional exposure
            if r["bsm_delta"] < 0.05:
                continue
            # Direction filter
            if auto_direction == "bullish" and r["option_type"] != "call":
                continue
            if auto_direction == "bearish" and r["option_type"] != "put":
                continue

            r["spread_pct"] = round(spread, 1)
            filtered.append(r)

        logger.info("Filtered %d → %d candidates", len(chain), len(filtered))

        # ── 7. Compute full Greeks + score ───────────────────────────
        scorer = ConfluenceScorer(regime_ctx, surface_ctx, gamma_ctx)
        candidates: List[ConfluenceCandidate] = []

        for r in filtered:
            try:
                # Compute all 22 Greeks using BSM
                greeks = _compute_full_greeks(
                    S=spot, K=r["strike"],
                    tau=r["days_to_expiry"] / 365,
                    r=RISK_FREE_RATE,
                    sigma=r["implied_volatility"] if r["implied_volatility"] > 0 else 0.20,
                    option_type=r["option_type"],
                    q=0.0,
                )

                # IV surface context for this option
                iv_surface_iv = r["implied_volatility"]  # fallback
                skew_z = 0.0
                ts_slope = 0.0
                curv = 0.0

                if surface_ctx.get("available"):
                    # Find nearest expiry in surface
                    nearest_exp = min(
                        surface_ctx.get("atm_ivs", {}).keys(),
                        key=lambda e: abs(e - r["days_to_expiry"] / 365),
                        default=None,
                    )
                    if nearest_exp is not None:
                        # Get surface IV at this strike
                        try:
                            from .iv_surface import IVSurface, bs_implied_vol as biv
                            # Use SVI if available
                            exp_sigs = surface_ctx.get("expiry_signals", {}).get(nearest_exp, {})
                            skew_z = exp_sigs.get("skew_z", 0.0)
                            ts_slope = exp_sigs.get("term_slope", 0.0)
                            curv = exp_sigs.get("curvature", 0.0)
                        except Exception:
                            pass

                # Build candidate
                cost_eff = greeks.delta / r["mid"] if r["mid"] > 0 else 0
                moneyness = (r["strike"] - spot) / spot * 100
                if r["option_type"] == "put":
                    moneyness = -moneyness  # positive = OTM for puts too

                cand = ConfluenceCandidate(
                    symbol=r["symbol"],
                    underlying=symbol,
                    option_type=r["option_type"],
                    strike=r["strike"],
                    expiration=r["expiry_date"],
                    dte=r["days_to_expiry"],
                    bid=r["bid"],
                    ask=r["ask"],
                    mid=r["mid"],
                    cost_per_contract=round(r["mid"] * 100, 2),
                    volume=r["volume"],
                    open_interest=r["open_interest"],
                    implied_volatility=r["implied_volatility"],
                    moneyness_pct=round(moneyness, 2),
                    spread_pct=r["spread_pct"],
                    cost_efficiency=round(cost_eff, 4),
                    greeks=greeks,
                    iv_surface_iv=iv_surface_iv,
                    iv_rank=0.0,  # TODO: historical IV rank
                    iv_percentile=0.0,
                    skew_zscore=skew_z,
                    term_structure_slope=ts_slope,
                    smile_curvature=curv,
                )

                # Score it
                scorer.score_candidate(cand, tech)
                candidates.append(cand)

            except Exception as e:
                logger.debug("Skip %s: %s", r["symbol"], e)
                continue

        # ── 8. Rank and select top N ─────────────────────────────────
        candidates.sort(key=lambda x: x.score.total, reverse=True)
        top = candidates[:top_n]

        # ── 9. Build result ──────────────────────────────────────────
        notes = []
        if not candidates:
            notes.append("No candidates passed all filters")
        elif len(candidates) < 5:
            notes.append(f"Only {len(candidates)} candidates found — market may be quiet")

        a_tier = [c for c in top if c.score.tier == "A"]
        if a_tier:
            notes.append(f"{len(a_tier)} A‑tier candidate(s) found")

        result = ConfluenceResult(
            underlying=symbol,
            spot=spot,
            scan_time=datetime.utcnow().isoformat(),
            candidates_found=len(candidates),
            candidates=top,
            regime_context=regime_ctx,
            surface_context={
                "available": surface_ctx.get("available", False),
                "expiries_fitted": surface_ctx.get("expiries_fitted", 0),
                "atm_ivs": surface_ctx.get("atm_ivs", {}),
            },
            gamma_context={
                "total_gex": gamma_ctx.get("gex", {}).get("total_gex", 0),
                "gex_regime": gamma_ctx.get("gex", {}).get("regime", "unknown"),
                "max_pain": gamma_ctx.get("max_pain", {}).get("max_pain_strike", 0),
                "put_call_ratio": gamma_ctx.get("put_call_ratio", {}),
                "walls": gamma_ctx.get("walls", {}),
            },
            notes=notes,
        )

        logger.info(
            "Scan complete: %d candidates, %d top, best score %.1f",
            len(candidates), len(top),
            top[0].score.total if top else 0,
        )

        return result

    def scan_multi(
        self, symbols: List[str] = None, **kwargs,
    ) -> List[ConfluenceResult]:
        """Scan multiple underlyings and return all results."""
        if symbols is None:
            symbols = DEFAULT_WATCHLIST

        results = []
        for sym in symbols:
            try:
                r = self.scan(sym, **kwargs)
                results.append(r)
            except Exception as e:
                logger.error("Scan failed for %s: %s", sym, e)
                continue

        # Sort all results by best candidate score across underlyings
        results.sort(
            key=lambda r: r.candidates[0].score.total if r.candidates else 0,
            reverse=True,
        )
        return results


# ---------------------------------------------------------------------------
# Module‑level convenience functions
# ---------------------------------------------------------------------------

def scan_options_confluence(
    symbol: str = "SPY",
    max_dte: int = DEFAULT_MAX_DTE,
    max_cost: float = MAX_CONTRACT_COST,
    direction: Optional[str] = None,
    top_n: int = 10,
) -> ConfluenceResult:
    """
    Quick entry point — scan a single underlying.

    >>> result = scan_options_confluence("SPY")
    >>> print(result.summary())
    """
    scanner = OptionsConfluenceScanner()
    return scanner.scan(symbol, max_dte=max_dte, max_cost=max_cost,
                        direction=direction, top_n=top_n)


def scan_all_confluence(
    symbols: List[str] = None,
    **kwargs,
) -> List[ConfluenceResult]:
    """Scan the full default watchlist."""
    scanner = OptionsConfluenceScanner()
    return scanner.scan_multi(symbols, **kwargs)


def get_best_trade(symbol: str = "SPY") -> dict:
    """
    Single‑shot function for auto_trader integration.

    Returns a dict with the best trade candidate or no_trade.
    """
    result = scan_options_confluence(symbol, top_n=1)

    if not result.candidates:
        return {
            "action": "no_trade",
            "reason": "No qualifying options found",
            "underlying": symbol,
            "scan_time": result.scan_time,
        }

    best = result.candidates[0]
    return {
        "action": "trade",
        "underlying": symbol,
        "candidate": best.to_dict(),
        "regime": result.regime_context.get("regime", "UNKNOWN"),
        "gex_regime": result.gamma_context.get("gex_regime", "unknown"),
        "scan_time": result.scan_time,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")

    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"

    print(f"\n{'═' * 60}")
    print(f"  OPTIONS CONFLUENCE SCANNER — {symbol}")
    print(f"{'═' * 60}\n")

    result = scan_options_confluence(symbol)
    print(result.summary())

    print(f"\n{'─' * 60}")
    print("TOP 3 DETAILED:")
    print(f"{'─' * 60}")

    for i, c in enumerate(result.candidates[:3], 1):
        print(f"\n#{i} {c.symbol}  (Score: {c.score.total:.1f}/100, Tier: {c.score.tier})")
        print(f"   Type: {c.option_type}  Strike: {c.strike}  DTE: {c.dte}")
        print(f"   Bid/Ask: ${c.bid:.2f}/${c.ask:.2f}  Mid: ${c.mid:.2f}  Cost: ${c.cost_per_contract:.2f}")
        print(f"   Delta: {c.greeks.delta:.4f}  Gamma: {c.greeks.gamma:.4f}  Theta: {c.greeks.theta:.4f}  Vega: {c.greeks.vega:.4f}")
        print(f"   Vanna: {c.greeks.vanna:.4f}  Charm: {c.greeks.charm:.4f}  Vomma: {c.greeks.vomma:.4f}")
        print(f"   Speed: {c.greeks.speed:.6f}  Zomma: {c.greeks.zomma:.6f}  Color: {c.greeks.color:.6f}")
        print(f"   IV: {c.implied_volatility:.3f}  Spread: {c.spread_pct:.1f}%  Moneyness: {c.moneyness_pct:.1f}%")
        print(f"   Score breakdown: Tech={c.score.technical:.1f} IV={c.score.iv_edge:.1f} "
              f"Greeks={c.score.greeks_quality:.1f} Liq={c.score.liquidity:.1f} "
              f"Regime={c.score.regime_flow:.1f}")
        print(f"   Signal: {c.entry_signal}")
