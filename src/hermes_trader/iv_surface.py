#!/usr/bin/env python3
"""
Implied Volatility Surface Construction & Trading Signals
==========================================================
Institutional-grade IV surface fitting for options trading.

Components:
    1. Black-Scholes implied volatility via Brent's method
    2. SVI (Stochastic Volatility Inspired) parametrization per expiry
    3. SSVI (Surface SVI) global surface fitting
    4. Trading signals: skew, term structure, smile curvature
    5. Delta-to-strike conversion for standard risk reversals

References:
    - Gatheral (2004): SVI parametrization
    - Gatheral & Jacquier (2014): SSVI (arbitrage-free surface)
    - Hagan et al. (2002): SABR model
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.stats import norm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Black-Scholes helpers
# ---------------------------------------------------------------------------

def bs_price(
    spot: float,
    strike: float,
    tte: float,
    sigma: float,
    rate: float = 0.05,
    option_type: str = "call",
) -> float:
    """European Black-Scholes price (forward = spot * exp(r*T))."""
    if tte <= 0:
        return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    F = spot * np.exp(rate * tte)
    d1 = (np.log(F / strike) + 0.5 * sigma**2 * tte) / (sigma * np.sqrt(tte))
    d2 = d1 - sigma * np.sqrt(tte)
    if option_type == "call":
        return np.exp(-rate * tte) * (F * norm.cdf(d1) - strike * norm.cdf(d2))
    else:
        return np.exp(-rate * tte) * (strike * norm.cdf(-d2) - F * norm.cdf(-d1))


def bs_implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    tte: float,
    rate: float = 0.05,
    option_type: str = "call",
) -> Optional[float]:
    """
    Compute Black-Scholes implied volatility via Brent's method.

    Returns None if convergence fails or price is below intrinsic.

    Parameters
    ----------
    market_price : observed option mid-price
    spot : underlying spot price
    strike : option strike
    tte : time-to-expiry in years
    rate : risk-free rate (annualised)
    option_type : 'call' or 'put'
    """
    if tte <= 0 or market_price <= 0:
        return None

    F = spot * np.exp(rate * tte)
    # Intrinsic bounds
    if option_type == "call":
        intrinsic = max(np.exp(-rate * tte) * (F - strike), 0.0)
    else:
        intrinsic = max(np.exp(-rate * tte) * (strike - F), 0.0)

    if market_price <= intrinsic + 1e-10:
        return None  # below intrinsic — no valid IV

    def objective(sigma: float) -> float:
        return bs_price(spot, strike, tte, sigma, rate, option_type) - market_price

    try:
        return brentq(objective, 1e-6, 5.0, xtol=1e-8, maxiter=200)
    except (ValueError, RuntimeError):
        return None


# ---------------------------------------------------------------------------
# 2. SVI parametrization
# ---------------------------------------------------------------------------

def svi_total_variance(
    k: np.ndarray | float,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
) -> np.ndarray | float:
    """
    SVI formula for total implied variance.

    w(k) = a + b * (ρ(k-m) + √((k-m)² + σ²))

    Parameters
    ----------
    k : log-moneyness  ln(K/F)
    a : vertical shift (overall variance level)
    b : wing steepness (rotation)
    rho : skew parameter (-1 < ρ < 1)
    m : horizontal shift
    sigma : smile curvature / width
    """
    x = k - m
    return a + b * (rho * x + np.sqrt(x**2 + sigma**2))


def svi_implied_vol(
    k: float,
    tte: float,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
) -> float:
    """Convert SVI total variance to annualised implied volatility."""
    w = svi_total_variance(k, a, b, rho, m, sigma)
    return np.sqrt(max(w, 0.0) / tte) if tte > 0 else 0.0


def svi_fit(
    log_moneyness: np.ndarray,
    total_variances: np.ndarray,
    initial_guess: Optional[Tuple[float, ...]] = None,
) -> Tuple[float, float, float, float, float]:
    """
    Fit SVI parameters to market total-variance observations.

    Uses constrained L-BFGS-B optimisation.

    Parameters
    ----------
    log_moneyness : ln(K/F) values
    total_variances : σ²_BS * T values
    initial_guess : optional (a, b, rho, m, sigma)

    Returns
    -------
    (a, b, rho, m, sigma)
    """
    log_moneyness = np.asarray(log_moneyness, dtype=float)
    total_variances = np.asarray(total_variances, dtype=float)

    if initial_guess is None:
        w_min = np.min(total_variances)
        k_at_min = log_moneyness[np.argmin(total_variances)]
        initial_guess = [w_min, 0.1, -0.3, k_at_min, 0.3]

    def objective(params):
        a, b, rho, m, sigma = params
        fitted = svi_total_variance(log_moneyness, a, b, rho, m, sigma)
        return float(np.sum((fitted - total_variances) ** 2))

    bounds = [
        (None, None),      # a
        (0, None),         # b ≥ 0
        (-0.999, 0.999),   # ρ
        (None, None),      # m
        (1e-6, None),      # σ > 0
    ]

    result = minimize(
        objective,
        initial_guess,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 500, "ftol": 1e-12},
    )

    if not result.success:
        logger.warning("SVI fit did not fully converge: %s", result.message)

    return tuple(result.x)


# ---------------------------------------------------------------------------
# 3. SSVI (Surface SVI)
# ---------------------------------------------------------------------------

def _theta_parametric(T: float, params: np.ndarray) -> float:
    """θ(T) = p1*T + p2*T² — ATM total variance term structure."""
    return params[0] * T + params[1] * T**2


def _phi_parametric(T: float, params: np.ndarray) -> float:
    """φ(T) = p3 / (1 + p4*T) — smile width parameter."""
    denom = 1.0 + params[1] * T
    return params[0] / max(denom, 1e-8)


def ssvi_total_variance(
    k: float,
    T: float,
    theta_T: float,
    phi_T: float,
    rho: float,
) -> float:
    """
    SSVI total variance (Gatheral & Jacquier 2014).

    w(k, T) = θ(T)/2 * (1 + ρφ(T)k̃ + √((φ(T)k̃ - ρ)² + (1-ρ²)))
    where k̃ = k / θ(T).
    """
    if theta_T <= 0 or T <= 0:
        return 0.0
    k_star = k / theta_T
    return (theta_T / 2.0) * (
        1.0
        + rho * phi_T * k_star
        + np.sqrt((phi_T * k_star - rho) ** 2 + (1.0 - rho**2))
    )


def ssvi_fit(
    log_moneyness: np.ndarray,
    time_to_expiry: np.ndarray,
    total_variances: np.ndarray,
) -> Tuple[float, np.ndarray, np.ndarray]:
    """
    Fit global SSVI surface across all expiries.

    Returns
    -------
    (rho, theta_params, phi_params)
    """
    log_moneyness = np.asarray(log_moneyness, dtype=float)
    time_to_expiry = np.asarray(time_to_expiry, dtype=float)
    total_variances = np.asarray(total_variances, dtype=float)

    def objective(params):
        rho = params[0]
        theta_p = params[1:3]
        phi_p = params[3:5]
        fitted = np.array([
            ssvi_total_variance(
                k, T, _theta_parametric(T, theta_p),
                _phi_parametric(T, phi_p), rho,
            )
            for k, T in zip(log_moneyness, time_to_expiry)
        ])
        return float(np.sum((fitted - total_variances) ** 2))

    initial = np.array([-0.3, 0.04, 0.1, 0.5, 1.0])
    bounds = [
        (-0.99, 0.99),  # rho
        (0, None), (0, None),  # theta params
        (0, None), (0, None),  # phi params
    ]

    result = minimize(
        objective, initial, method="L-BFGS-B", bounds=bounds,
        options={"maxiter": 500, "ftol": 1e-12},
    )
    if not result.success:
        logger.warning("SSVI fit did not fully converge: %s", result.message)

    rho = result.x[0]
    theta_params = result.x[1:3]
    phi_params = result.x[3:5]
    return rho, theta_params, phi_params


# ---------------------------------------------------------------------------
# 4. Delta-to-strike conversion
# ---------------------------------------------------------------------------

def delta_to_strike(
    forward: float,
    delta: float,
    tte: float,
    sigma: float,
    option_type: str = "put",
) -> float:
    """
    Convert option delta to strike price using forward delta.

    Forward delta (no discount):
        Call: Δ = N(d1)       → d1 = Φ⁻¹(Δ)
        Put:  Δ = -N(-d1)     → d1 = -Φ⁻¹(-Δ) = -Φ⁻¹(|Δ|)

    From d1 = [ln(F/K) + 0.5σ²T] / (σ√T):
        ln(K) = ln(F) - d1·σ√T + 0.5σ²T
    """
    if tte <= 0 or sigma <= 0:
        return forward

    if option_type == "put":
        # Put: -N(-d1) = delta → N(-d1) = |delta| → -d1 = Φ⁻¹(|delta|) → d1 = -Φ⁻¹(|delta|)
        d1 = -norm.ppf(-delta)
    else:
        # Call: N(d1) = delta → d1 = Φ⁻¹(delta)
        d1 = norm.ppf(delta)

    ln_K = np.log(forward) - d1 * sigma * np.sqrt(tte) + 0.5 * sigma**2 * tte
    return float(np.exp(ln_K))


def get_25d_strikes(forward: float, tte: float, sigma_atm: float) -> Tuple[float, float]:
    """Return (25Δ put strike, 25Δ call strike)."""
    put_25d = delta_to_strike(forward, -0.25, tte, sigma_atm, "put")
    call_25d = delta_to_strike(forward, 0.25, tte, sigma_atm, "call")
    return put_25d, call_25d


def get_10d_strikes(forward: float, tte: float, sigma_atm: float) -> Tuple[float, float]:
    """Return (10Δ put strike, 10Δ call strike)."""
    put_10d = delta_to_strike(forward, -0.10, tte, sigma_atm, "put")
    call_10d = delta_to_strike(forward, 0.10, tte, sigma_atm, "call")
    return put_10d, call_10d


# ---------------------------------------------------------------------------
# 5. Trading signal types
# ---------------------------------------------------------------------------

class Signal(Enum):
    NEUTRAL = "NEUTRAL"
    BUY_PUTS = "BUY_PUTS"
    SELL_PUTS = "SELL_PUTS"
    BUY_CALLS = "BUY_CALLS"
    SELL_CALLS = "SELL_CALLS"
    CALENDAR_SELL_FRONT = "CALENDAR_SELL_FRONT"
    CALENDAR_SELL_BACK = "CALENDAR_SELL_BACK"
    BUY_STRADDLE = "BUY_STRADDLE"
    SELL_STRADDLE = "SELL_STRADDLE"


def _z_score_signal(value: float, mean: float, std: float, threshold: float = 2.0) -> float:
    """Return z-score. Signal logic lives in callers."""
    if std <= 0:
        return 0.0
    return (value - mean) / std


# ---------------------------------------------------------------------------
# 6. IV Surface builder
# ---------------------------------------------------------------------------

@dataclass
class ExpirySlice:
    """Data for one expiry slice of the surface."""
    expiry: float
    strikes: np.ndarray
    ivs: np.ndarray
    forward: float
    log_moneyness: np.ndarray = field(init=False)
    total_variances: np.ndarray = field(init=False)

    def __post_init__(self):
        self.strikes = np.asarray(self.strikes, dtype=float)
        self.ivs = np.asarray(self.ivs, dtype=float)
        self.log_moneyness = np.log(self.strikes / self.forward)
        self.total_variances = self.ivs**2 * self.expiry


@dataclass
class SVIParams:
    """SVI parameters for one expiry."""
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def to_tuple(self) -> Tuple[float, float, float, float, float]:
        return (self.a, self.b, self.rho, self.m, self.sigma)


class IVSurface:
    """
    Full IV surface construction from options chain data.

    Supports:
        - Per-expiry SVI fitting
        - Global SSVI fitting
        - IV query at arbitrary (strike, expiry) points
        - Trading signal generation (skew, term structure, curvature)
    """

    def __init__(self, spot: float, rate: float = 0.05):
        self.spot = spot
        self.rate = rate
        self.slices: Dict[float, ExpirySlice] = {}
        self.svi_params: Dict[float, SVIParams] = {}
        self.ssvi_result: Optional[Tuple] = None  # (rho, theta_p, phi_p)

    # ---- data ingestion ----

    def add_expiry(
        self,
        expiry: float,
        strikes: np.ndarray,
        ivs: np.ndarray,
        forward: float,
    ) -> None:
        """Add a single expiry slice (market IV observations)."""
        self.slices[expiry] = ExpirySlice(expiry, strikes, ivs, forward)

    def add_option_chain(
        self,
        chain_data: List[dict],
        expiry: float,
        forward: float,
        min_volume: int = 10,
        min_open_interest: int = 50,
        min_bid: float = 0.01,
    ) -> None:
        """
        Ingest raw option chain dicts and compute IVs.

        Each dict should have: strike, bid, ask, volume, open_interest,
        option_type, days_to_expiry (or expiry can be passed separately).
        """
        strikes, ivs = [], []
        for row in chain_data:
            if row.get("volume", 0) < min_volume:
                continue
            if row.get("open_interest", 0) < min_open_interest:
                continue
            bid = row.get("bid", 0)
            if bid < min_bid:
                continue

            mid = (bid + row.get("ask", bid)) / 2.0
            if mid <= 0:
                continue

            iv = bs_implied_vol(
                mid, self.spot, row["strike"], expiry,
                self.rate, row.get("option_type", "call"),
            )
            if iv is not None and 0.01 < iv < 3.0:
                strikes.append(row["strike"])
                ivs.append(iv)

        if strikes:
            self.add_expiry(expiry, np.array(strikes), np.array(ivs), forward)

    # ---- SVI fitting ----

    def fit_svi(self) -> Dict[float, SVIParams]:
        """Fit SVI to each expiry independently."""
        for expiry, sl in self.slices.items():
            a, b, rho, m, sigma = svi_fit(sl.log_moneyness, sl.total_variances)
            self.svi_params[expiry] = SVIParams(a, b, rho, m, sigma)
        return self.svi_params

    def fit_ssvi(self) -> Tuple:
        """Fit global SSVI surface across all expiries."""
        all_k, all_T, all_w = [], [], []
        for expiry, sl in self.slices.items():
            all_k.extend(sl.log_moneyness)
            all_T.extend([expiry] * len(sl.log_moneyness))
            all_w.extend(sl.total_variances)
        self.ssvi_result = ssvi_fit(
            np.array(all_k), np.array(all_T), np.array(all_w),
        )
        return self.ssvi_result

    # ---- IV query ----

    def get_iv(self, strike: float, expiry: float, method: str = "svi") -> float:
        """
        Query implied volatility at an arbitrary (strike, expiry) point.

        Methods: 'svi' (per-expiry), 'ssvi' (global), 'linear' (nearest data).
        """
        if method == "svi" and expiry in self.svi_params:
            p = self.svi_params[expiry]
            fwd = self.slices[expiry].forward
            k = np.log(strike / fwd)
            return svi_implied_vol(k, expiry, p.a, p.b, p.rho, p.m, p.sigma)

        if method == "ssvi" and self.ssvi_result is not None:
            rho, theta_p, phi_p = self.ssvi_result
            fwd = self._forward_for_expiry(expiry)
            k = np.log(strike / fwd)
            theta_T = _theta_parametric(expiry, theta_p)
            phi_T = _phi_parametric(expiry, phi_p)
            w = ssvi_total_variance(k, expiry, theta_T, phi_T, rho)
            return float(np.sqrt(max(w, 0.0) / expiry)) if expiry > 0 else 0.0

        # Linear interpolation fallback
        return self._linear_interpolate_iv(strike, expiry)

    def get_surface_grid(
        self,
        k_range: Tuple[float, float] = (-0.3, 0.3),
        t_range: Optional[Tuple[float, float]] = None,
        n_k: int = 50,
        n_t: int = 50,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return regular (k, T, IV) grid for surface plotting."""
        if t_range is None:
            expiries = sorted(self.slices.keys())
            t_range = (expiries[0], expiries[-1])

        k_grid = np.linspace(k_range[0], k_range[1], n_k)
        t_grid = np.linspace(t_range[0], t_range[1], n_t)
        iv_grid = np.zeros((n_t, n_k))

        for i, T in enumerate(t_grid):
            fwd = self._forward_for_expiry(T)
            for j, k in enumerate(k_grid):
                strike = fwd * np.exp(k)
                iv_grid[i, j] = self.get_iv(strike, T, method="svi")

        return k_grid, t_grid, iv_grid

    # ---- trading signals ----

    def skew(
        self,
        expiry: float,
        put_delta: float = -0.25,
        call_delta: float = 0.25,
    ) -> float:
        """25Δ risk-reversal: IV(call) - IV(put)."""
        fwd = self.slices[expiry].forward
        atm_iv = self.get_iv(fwd, expiry)
        put_strike = delta_to_strike(fwd, put_delta, expiry, atm_iv, "put")
        call_strike = delta_to_strike(fwd, call_delta, expiry, atm_iv, "call")
        return self.get_iv(call_strike, expiry) - self.get_iv(put_strike, expiry)

    def smile_curvature(
        self,
        expiry: float,
        otm_pct: float = 0.10,
    ) -> float:
        """
        Butterfly curvature: ½ IV(K-Δ) + ½ IV(K+Δ) - IV(ATM).
        """
        fwd = self.slices[expiry].forward
        atm_iv = self.get_iv(fwd, expiry)
        iv_put = self.get_iv(fwd * (1 - otm_pct), expiry)
        iv_call = self.get_iv(fwd * (1 + otm_pct), expiry)
        return 0.5 * iv_put + 0.5 * iv_call - atm_iv

    def term_structure_slope(
        self,
        short_expiry: float,
        long_expiry: float,
    ) -> float:
        """IV(long) - IV(short) for ATM options."""
        fwd_short = self.slices[short_expiry].forward
        fwd_long = self.slices[long_expiry].forward
        return self.get_iv(fwd_long, long_expiry) - self.get_iv(fwd_short, short_expiry)

    def generate_signals(
        self,
        expiry: float,
        skew_hist_mean: float = 0.0,
        skew_hist_std: float = 0.02,
        ts_hist_mean: float = 0.0,
        ts_hist_std: float = 0.01,
        curv_hist_mean: float = 0.01,
        curv_hist_std: float = 0.005,
        threshold: float = 2.0,
    ) -> Dict[str, object]:
        """
        Generate comprehensive trading signals from the surface.

        Returns dict with z-scores and Signal enums for skew, term structure,
        and smile curvature.
        """
        cur_skew = self.skew(expiry)
        cur_curv = self.smile_curvature(expiry)

        # Term structure (use this expiry vs next shorter/longer)
        sorted_exp = sorted(self.slices.keys())
        idx = sorted_exp.index(expiry) if expiry in sorted_exp else -1
        ts_slope = 0.0
        if 0 < idx < len(sorted_exp):
            ts_slope = self.term_structure_slope(sorted_exp[idx - 1], expiry)
        elif idx == 0 and len(sorted_exp) > 1:
            ts_slope = self.term_structure_slope(expiry, sorted_exp[-1])

        skew_z = _z_score_signal(cur_skew, skew_hist_mean, skew_hist_std, threshold)
        ts_z = _z_score_signal(ts_slope, ts_hist_mean, ts_hist_std, threshold)
        curv_z = _z_score_signal(cur_curv, curv_hist_mean, curv_hist_std, threshold)

        # Map z-scores to signals
        if skew_z < -threshold:
            skew_sig = Signal.BUY_PUTS
        elif skew_z > threshold:
            skew_sig = Signal.SELL_PUTS
        else:
            skew_sig = Signal.NEUTRAL

        if ts_z < -threshold:
            ts_sig = Signal.CALENDAR_SELL_FRONT
        elif ts_z > threshold:
            ts_sig = Signal.CALENDAR_SELL_BACK
        else:
            ts_sig = Signal.NEUTRAL

        if curv_z < -threshold:
            curv_sig = Signal.BUY_STRADDLE
        elif curv_z > threshold:
            curv_sig = Signal.SELL_STRADDLE
        else:
            curv_sig = Signal.NEUTRAL

        return {
            "skew": cur_skew,
            "skew_z": skew_z,
            "skew_signal": skew_sig.value,
            "term_slope": ts_slope,
            "term_z": ts_z,
            "term_signal": ts_sig.value,
            "curvature": cur_curv,
            "curvature_z": curv_z,
            "curvature_signal": curv_sig.value,
        }

    def atm_iv(self, expiry: float) -> float:
        """Get ATM implied volatility for an expiry."""
        fwd = self.slices[expiry].forward
        return self.get_iv(fwd, expiry)

    def arbitrage_check(self, expiry: float) -> Tuple[bool, str]:
        """
        Check butterfly arbitrage condition for an expiry slice.

        Returns (is_clean, message).
        """
        sl = self.slices[expiry]
        strikes = sl.strikes
        ivs = sl.ivs
        T = expiry

        for i in range(1, len(strikes) - 1):
            w_prev = ivs[i - 1] ** 2 * T
            w_curr = ivs[i] ** 2 * T
            w_next = ivs[i + 1] ** 2 * T
            butterfly = 0.5 * w_prev - w_curr + 0.5 * w_next
            if butterfly < -1e-6:
                return False, f"Butterfly violation at strike {strikes[i]:.2f}"
        return True, "No arbitrage detected"

    # ---- internal helpers ----

    def _forward_for_expiry(self, expiry: float) -> float:
        """Find the forward for the nearest available expiry."""
        nearest = min(self.slices.keys(), key=lambda e: abs(e - expiry))
        return self.slices[nearest].forward

    def _linear_interpolate_iv(self, strike: float, expiry: float) -> float:
        """Fallback: interpolate from nearest expiry's raw data."""
        nearest = min(self.slices.keys(), key=lambda e: abs(e - expiry))
        sl = self.slices[nearest]
        return float(np.interp(np.log(strike / sl.forward), sl.log_moneyness, sl.ivs))
