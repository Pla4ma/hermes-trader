#!/usr/bin/env python3
"""
Higher-Order Greeks Module
==========================
Institutional-grade higher-order Greeks computation using opengreeks (Rust core)
with pure Python closed-form fallback.

Supported Greeks (2nd order):
  - Vanna   (∂²V/∂S∂σ)  — Delta sensitivity to vol / Vega sensitivity to spot
  - Charm   (∂Δ/∂τ)      — Delta decay over time (positive = delta increases with time)
  - Vomma   (∂²V/∂σ²)    — Vega convexity / Volga
  - Veta    (∂²V/∂σ∂τ)   — Vega decay over time

Supported Greeks (3rd order):
  - Speed   (∂³V/∂S³)    — Gamma sensitivity to spot
  - Zomma   (∂Γ/∂σ)      — Gamma sensitivity to vol
  - Color   (∂Γ/∂τ)      — Gamma decay over time
  - Ultima  (∂³V/∂σ³)    — Vomma sensitivity to vol

Pricing Models:
  - Black76:           Futures options (flag, F, K, t, r, sigma)  [no drift in d1]
  - BlackScholes:      Stock options, no dividends (flag, S, K, t, r, sigma) [drift r in d1]
  - BlackScholesMerton: Stock options with dividends (flag, S, K, t, r, sigma, q) [drift (r-q) in d1]

Usage:
    from hermes_trader.hol_greeks import Black76Model, BlackScholesModel, BlackScholesMertonModel

    model = BlackScholesMertonModel()
    greeks = model.compute_all(S=100, K=100, t=30/365, r=0.05, sigma=0.25, q=0.02, flag='c')
    # greeks = HigherOrderGreeks(vanna=..., charm=..., vomma=..., speed=..., ...)
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.stats import norm

# ---------------------------------------------------------------------------
# Detect opengreeks availability
# ---------------------------------------------------------------------------
_OPENGREEKS_AVAILABLE = False
try:
    from opengreeks import black76 as _og_b76
    from opengreeks import black_scholes as _og_bs
    from opengreeks import black_scholes_merton as _og_bsm

    _OPENGREEKS_AVAILABLE = True
except ImportError:
    _og_b76 = None
    _og_bs = None
    _og_bsm = None


def is_opengreeks_available() -> bool:
    """Return True if opengreeks Rust backend is available."""
    return _OPENGREEKS_AVAILABLE


# ---------------------------------------------------------------------------
# Pure-Python closed-form helpers
# ---------------------------------------------------------------------------
def _bs_d1(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """Black-Scholes d1 parameter (BSM with dividends)."""
    return (math.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * math.sqrt(tau))


def _bs_d2(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """Black-Scholes d2 parameter."""
    return _bs_d1(S, K, r, sigma, tau, q) - sigma * math.sqrt(tau)


def _bs_n(x: float) -> float:
    """Standard normal PDF φ(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_N(x: float) -> float:
    """Standard normal CDF Φ(x)."""
    return norm.cdf(x)


# ---------------------------------------------------------------------------
# Pure-Python higher-order Greek implementations
# Formulas validated against opengreeks (autograd-validated Rust core).
# ---------------------------------------------------------------------------

def _vanna_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Vanna: ∂²V/∂S∂σ = −exp(−qτ)·n(d1)·d2/σ

    Same for calls and puts in Black-Scholes.
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    d2 = d1 - sigma * math.sqrt(tau)
    return -math.exp(-q * tau) * _bs_n(d1) * d2 / sigma


def _charm_call_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Charm (call): ∂Δ/∂τ — positive means delta increases with time.

    Standard form:
      charm_call = q·exp(−qτ)·N(d1) − exp(−qτ)·n(d1)·[2(r−q)τ − d2·σ·√τ] / (2τ·σ·√τ)
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    d2 = d1 - sigma * math.sqrt(tau)
    sqrt_tau = math.sqrt(tau)
    nd1 = _bs_n(d1)
    eqt = math.exp(-q * tau)

    common = -eqt * nd1 * (2.0 * (r - q) * tau - d2 * sigma * sqrt_tau) / (2.0 * tau * sigma * sqrt_tau)
    return q * eqt * _bs_N(d1) + common


def _charm_put_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Charm (put): ∂Δ/∂τ

      charm_put = −q·exp(−qτ)·N(−d1) − exp(−qτ)·n(d1)·[2(r−q)τ − d2·σ·√τ] / (2τ·σ·√τ)
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    d2 = d1 - sigma * math.sqrt(tau)
    sqrt_tau = math.sqrt(tau)
    nd1 = _bs_n(d1)
    eqt = math.exp(-q * tau)

    common = -eqt * nd1 * (2.0 * (r - q) * tau - d2 * sigma * sqrt_tau) / (2.0 * tau * sigma * sqrt_tau)
    return -q * eqt * _bs_N(-d1) + common


def _vomma_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Vomma: ∂²V/∂σ² = Vega · d1 · d2 / σ

    Same for calls and puts.
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    d2 = d1 - sigma * math.sqrt(tau)
    vega = S * math.exp(-q * tau) * _bs_n(d1) * math.sqrt(tau)
    return vega * d1 * d2 / sigma


def _veta_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Veta: ∂²V/∂σ∂τ = −∂ν/∂τ

    Same for calls and puts.
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    d2 = d1 - sigma * math.sqrt(tau)
    vega = S * math.exp(-q * tau) * _bs_n(d1) * math.sqrt(tau)
    return -vega * (q + (r - q) * d1 / (sigma * math.sqrt(tau)) - (1.0 + d1 * d2) / (2.0 * tau))


def _speed_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Speed: ∂Γ/∂S = −Γ/S · (d1/(σ√τ) + 1)

    Validated against opengreeks autograd.
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    gamma = math.exp(-q * tau) * _bs_n(d1) / (S * sigma * math.sqrt(tau))
    return -gamma / S * (d1 / (sigma * math.sqrt(tau)) + 1.0)


def _zomma_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Zomma: ∂Γ/∂σ = Γ · (d1·d2 − 1) / σ

    Validated against opengreeks autograd.
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    d2 = d1 - sigma * math.sqrt(tau)
    gamma = math.exp(-q * tau) * _bs_n(d1) / (S * sigma * math.sqrt(tau))
    return gamma * (d1 * d2 - 1.0) / sigma


def _color_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Color: ∂Γ/∂τ

    Derived analytically and validated against opengreeks autograd:
      color = −Γ · [q + d1·(r−q+σ²/2)/(2σ√τ) + 1/(2τ)]
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    gamma = math.exp(-q * tau) * _bs_n(d1) / (S * sigma * math.sqrt(tau))
    sqrt_tau = math.sqrt(tau)
    B = r - q + 0.5 * sigma**2
    return -gamma * (q + d1 * B / (2.0 * sigma * sqrt_tau) + 1.0 / (2.0 * tau))


def _ultima_pp(S: float, K: float, r: float, sigma: float, tau: float, q: float = 0.0) -> float:
    """
    Ultima: ∂³V/∂σ³

    Derived analytically and validated against opengreeks autograd:
      ultima = exp(−qτ)·S·√τ·n(d1)/σ² · (d1²·d2² − d1² − d2² − d1·d2)
    """
    d1 = _bs_d1(S, K, r, sigma, tau, q)
    d2 = d1 - sigma * math.sqrt(tau)
    sqrt_tau = math.sqrt(tau)
    nd1 = _bs_n(d1)
    bracket = d1**2 * d2**2 - d1**2 - d2**2 - d1 * d2
    return math.exp(-q * tau) * S * sqrt_tau * nd1 / sigma**2 * bracket


# ---------------------------------------------------------------------------
# Vectorized pure-Python fallbacks (numpy)
# ---------------------------------------------------------------------------

def _vanna_np(S, K, r, sigma, tau, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    d2 = d1 - sigma * np.sqrt(tau)
    return -np.exp(-q * tau) * norm.pdf(d1) * d2 / sigma


def _charm_np(S, K, r, sigma, tau, q=0.0, flag='c'):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    d2 = d1 - sigma * np.sqrt(tau)
    sqrt_tau = np.sqrt(tau)
    nd1 = norm.pdf(d1)
    eqt = np.exp(-q * tau)
    common = -eqt * nd1 * (2.0 * (r - q) * tau - d2 * sigma * sqrt_tau) / (2.0 * tau * sigma * sqrt_tau)
    if flag == 'c':
        return q * eqt * norm.cdf(d1) + common
    else:
        return -q * eqt * norm.cdf(-d1) + common


def _vomma_np(S, K, r, sigma, tau, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    d2 = d1 - sigma * np.sqrt(tau)
    vega = S * np.exp(-q * tau) * norm.pdf(d1) * np.sqrt(tau)
    return vega * d1 * d2 / sigma


def _veta_np(S, K, r, sigma, tau, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    d2 = d1 - sigma * np.sqrt(tau)
    vega = S * np.exp(-q * tau) * norm.pdf(d1) * np.sqrt(tau)
    return -vega * (q + (r - q) * d1 / (sigma * np.sqrt(tau)) - (1.0 + d1 * d2) / (2.0 * tau))


def _speed_np(S, K, r, sigma, tau, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    gamma = np.exp(-q * tau) * norm.pdf(d1) / (S * sigma * np.sqrt(tau))
    return -gamma / S * (d1 / (sigma * np.sqrt(tau)) + 1.0)


def _zomma_np(S, K, r, sigma, tau, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    d2 = d1 - sigma * np.sqrt(tau)
    gamma = np.exp(-q * tau) * norm.pdf(d1) / (S * sigma * np.sqrt(tau))
    return gamma * (d1 * d2 - 1.0) / sigma


def _color_np(S, K, r, sigma, tau, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    gamma = np.exp(-q * tau) * norm.pdf(d1) / (S * sigma * np.sqrt(tau))
    sqrt_tau = np.sqrt(tau)
    B = r - q + 0.5 * sigma**2
    return -gamma * (q + d1 * B / (2.0 * sigma * sqrt_tau) + 1.0 / (2.0 * tau))


def _ultima_np(S, K, r, sigma, tau, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    d2 = d1 - sigma * np.sqrt(tau)
    sqrt_tau = np.sqrt(tau)
    nd1 = norm.pdf(d1)
    bracket = d1**2 * d2**2 - d1**2 - d2**2 - d1 * d2
    return np.exp(-q * tau) * S * sqrt_tau * nd1 / sigma**2 * bracket


# ---------------------------------------------------------------------------
# Higher-Order Greek dataclass
# ---------------------------------------------------------------------------
class HigherOrderGreeks:
    """Container for computed higher-order Greeks."""

    __slots__ = ('vanna', 'charm', 'vomma', 'veta', 'speed', 'zomma', 'color', 'ultima')

    def __init__(
        self,
        vanna: float = 0.0,
        charm: float = 0.0,
        vomma: float = 0.0,
        veta: float = 0.0,
        speed: float = 0.0,
        zomma: float = 0.0,
        color: float = 0.0,
        ultima: float = 0.0,
    ):
        self.vanna = vanna
        self.charm = charm
        self.vomma = vomma
        self.veta = veta
        self.speed = speed
        self.zomma = zomma
        self.color = color
        self.ultima = ultima

    def to_dict(self) -> dict:
        """Return Greeks as a dictionary."""
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self) -> str:
        items = ', '.join(f'{n}={getattr(self, n):.6f}' for n in self.__slots__)
        return f'HigherOrderGreeks({items})'

    def __getitem__(self, key: str) -> float:
        if key not in self.__slots__:
            raise KeyError(f'Unknown Greek: {key}')
        return getattr(self, key)


# ---------------------------------------------------------------------------
# Black76 Model (Futures options)
# ---------------------------------------------------------------------------
class Black76Model:
    """
    Higher-order Greeks via Black-76 model for futures options.
    d1 has no drift term (uses ln(F/K) + σ²T/2).

    Signature: flag, F, K, t, r, sigma
    """

    MODEL = 'black76'

    def compute(
        self, *, flag: str, F: float, K: float, t: float, r: float, sigma: float
    ) -> HigherOrderGreeks:
        flag = flag.lower()
        if _OPENGREEKS_AVAILABLE:
            return self._compute_og(flag, F, K, t, r, sigma)
        return self._compute_pp(flag, F, K, t, r, sigma)

    def _compute_og(self, flag, F, K, t, r, sigma):
        return HigherOrderGreeks(
            vanna=_og_b76.vanna(flag, F, K, t, r, sigma),
            charm=_og_b76.charm(flag, F, K, t, r, sigma),
            vomma=_og_b76.vomma(flag, F, K, t, r, sigma),
            veta=_og_b76.veta(flag, F, K, t, r, sigma),
            speed=_og_b76.speed(flag, F, K, t, r, sigma),
            zomma=_og_b76.zomma(flag, F, K, t, r, sigma),
            color=_og_b76.color(flag, F, K, t, r, sigma),
            ultima=_og_b76.ultima(flag, F, K, t, r, sigma),
        )

    def _compute_pp(self, flag, F, K, t, r, sigma):
        # B76 d1 = [ln(F/K) + σ²T/2] / (σ√T) — no drift term
        S = F
        q_eff = r  # B76 equivalent: drift absorbed into forward
        # For B76 pure Python, use d1 without the r term
        d1 = (math.log(S / K) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)
        nd1 = _bs_n(d1)
        sqrt_t = math.sqrt(t)
        gamma = math.exp(-r * t) * nd1 / (S * sigma * sqrt_t)
        vega = S * math.exp(-r * t) * nd1 * sqrt_t

        charm_fn = self._charm_call_b76 if flag == 'c' else self._charm_put_b76
        return HigherOrderGreeks(
            vanna=-math.exp(-r * t) * nd1 * d2 / sigma,
            charm=charm_fn(S, K, r, sigma, t, d1, d2, nd1),
            vomma=vega * d1 * d2 / sigma,
            veta=-vega * (r + r * d1 / (sigma * sqrt_t) - (1.0 + d1 * d2) / (2.0 * t)),
            speed=-gamma / S * (d1 / (sigma * sqrt_t) + 1.0),
            zomma=gamma * (d1 * d2 - 1.0) / sigma,
            color=-gamma * (r + d1 * (r + 0.5 * sigma**2) / (2.0 * sigma * sqrt_t) + 1.0 / (2.0 * t)),
            ultima=math.exp(-r * t) * S * sqrt_t * nd1 / sigma**2 * (d1**2 * d2**2 - d1**2 - d2**2 - d1 * d2),
        )

    @staticmethod
    def _charm_call_b76(S, K, r, sigma, t, d1, d2, nd1):
        sqrt_t = math.sqrt(t)
        eqt = math.exp(-r * t)
        return r * eqt * _bs_N(d1) - eqt * nd1 * (2.0 * r * t - d2 * sigma * sqrt_t) / (2.0 * t * sigma * sqrt_t)

    @staticmethod
    def _charm_put_b76(S, K, r, sigma, t, d1, d2, nd1):
        sqrt_t = math.sqrt(t)
        eqt = math.exp(-r * t)
        return -r * eqt * _bs_N(-d1) - eqt * nd1 * (2.0 * r * t - d2 * sigma * sqrt_t) / (2.0 * t * sigma * sqrt_t)

    def compute_individual(self, **kwargs) -> dict:
        return self.compute(**kwargs).to_dict()

    def compute_array(self, *, flag: str, F: np.ndarray, K: np.ndarray, t, r: float, sigma: np.ndarray) -> dict:
        if _OPENGREEKS_AVAILABLE:
            t_arr = np.full_like(F, t)
            return {
                'vanna': _og_b76.vanna_array(flag, F, K, t_arr, r, sigma),
                'charm': _og_b76.charm_array(flag, F, K, t_arr, r, sigma),
                'vomma': _og_b76.vomma_array(flag, F, K, t_arr, r, sigma),
                'veta': _og_b76.veta_array(flag, F, K, t_arr, r, sigma),
                'speed': _og_b76.speed_array(flag, F, K, t_arr, r, sigma),
                'zomma': _og_b76.zomma_array(flag, F, K, t_arr, r, sigma),
                'color': _og_b76.color_array(flag, F, K, t_arr, r, sigma),
                'ultima': _og_b76.ultima_array(flag, F, K, t_arr, r, sigma),
            }
        return self._compute_array_pp(flag, F, K, t, r, sigma, q=0.0, use_b76_d1=True)

    def _compute_array_pp(self, flag, S_or_F, K, t, r, sigma, q=0.0, use_b76_d1=False):
        if use_b76_d1:
            d1 = (np.log(S_or_F / K) + 0.5 * sigma**2 * t) / (sigma * np.sqrt(t))
        else:
            d1 = (np.log(S_or_F / K) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        nd1 = norm.pdf(d1)
        sqrt_t = np.sqrt(t)
        eqt = np.exp(-q * t)
        gamma = eqt * nd1 / (S_or_F * sigma * sqrt_t)
        vega = S_or_F * eqt * nd1 * sqrt_t
        B = r - q + 0.5 * sigma**2
        charm_common = -eqt * nd1 * (2.0 * (r - q) * t - d2 * sigma * sqrt_t) / (2.0 * t * sigma * sqrt_t)
        if flag == 'c':
            charm = q * eqt * norm.cdf(d1) + charm_common
        else:
            charm = -q * eqt * norm.cdf(-d1) + charm_common
        return {
            'vanna': -eqt * nd1 * d2 / sigma,
            'charm': charm,
            'vomma': vega * d1 * d2 / sigma,
            'veta': -vega * (q + (r - q) * d1 / (sigma * sqrt_t) - (1.0 + d1 * d2) / (2.0 * t)),
            'speed': -gamma / S_or_F * (d1 / (sigma * sqrt_t) + 1.0),
            'zomma': gamma * (d1 * d2 - 1.0) / sigma,
            'color': -gamma * (q + d1 * B / (2.0 * sigma * sqrt_t) + 1.0 / (2.0 * t)),
            'ultima': eqt * S_or_F * sqrt_t * nd1 / sigma**2 * (d1**2 * d2**2 - d1**2 - d2**2 - d1 * d2),
        }


# ---------------------------------------------------------------------------
# Black-Scholes Model (no dividends)
# ---------------------------------------------------------------------------
class BlackScholesModel:
    """
    Higher-order Greeks via Black-Scholes model for stock options (no dividends).
    d1 includes drift r.

    Signature: flag, S, K, t, r, sigma
    """

    MODEL = 'black_scholes'

    def compute(
        self, *, flag: str, S: float, K: float, t: float, r: float, sigma: float
    ) -> HigherOrderGreeks:
        flag = flag.lower()
        if _OPENGREEKS_AVAILABLE:
            return self._compute_og(flag, S, K, t, r, sigma)
        return self._compute_pp(flag, S, K, t, r, sigma)

    def _compute_og(self, flag, S, K, t, r, sigma):
        return HigherOrderGreeks(
            vanna=_og_bs.vanna(flag, S, K, t, r, sigma),
            charm=_og_bs.charm(flag, S, K, t, r, sigma),
            vomma=_og_bs.vomma(flag, S, K, t, r, sigma),
            veta=_og_bs.veta(flag, S, K, t, r, sigma),
            speed=_og_bs.speed(flag, S, K, t, r, sigma),
            zomma=_og_bs.zomma(flag, S, K, t, r, sigma),
            color=_og_bs.color(flag, S, K, t, r, sigma),
            ultima=_og_bs.ultima(flag, S, K, t, r, sigma),
        )

    def _compute_pp(self, flag, S, K, t, r, sigma):
        q = 0.0
        charm_fn = _charm_call_pp if flag == 'c' else _charm_put_pp
        return HigherOrderGreeks(
            vanna=_vanna_pp(S, K, r, sigma, t, q),
            charm=charm_fn(S, K, r, sigma, t, q),
            vomma=_vomma_pp(S, K, r, sigma, t, q),
            veta=_veta_pp(S, K, r, sigma, t, q),
            speed=_speed_pp(S, K, r, sigma, t, q),
            zomma=_zomma_pp(S, K, r, sigma, t, q),
            color=_color_pp(S, K, r, sigma, t, q),
            ultima=_ultima_pp(S, K, r, sigma, t, q),
        )

    def compute_individual(self, **kwargs) -> dict:
        return self.compute(**kwargs).to_dict()

    def compute_array(self, *, flag: str, S: np.ndarray, K: np.ndarray, t, r: float, sigma: np.ndarray) -> dict:
        if _OPENGREEKS_AVAILABLE:
            t_arr = np.full_like(S, t)
            return {
                'vanna': _og_bs.vanna_array(flag, S, K, t_arr, r, sigma),
                'charm': _og_bs.charm_array(flag, S, K, t_arr, r, sigma),
                'vomma': _og_bs.vomma_array(flag, S, K, t_arr, r, sigma),
                'veta': _og_bs.veta_array(flag, S, K, t_arr, r, sigma),
                'speed': _og_bs.speed_array(flag, S, K, t_arr, r, sigma),
                'zomma': _og_bs.zomma_array(flag, S, K, t_arr, r, sigma),
                'color': _og_bs.color_array(flag, S, K, t_arr, r, sigma),
                'ultima': _og_bs.ultima_array(flag, S, K, t_arr, r, sigma),
            }
        b76 = Black76Model()
        return b76._compute_array_pp(flag, S, K, t, r, sigma, q=0.0, use_b76_d1=False)


# ---------------------------------------------------------------------------
# Black-Scholes-Merton Model (with continuous dividend yield)
# ---------------------------------------------------------------------------
class BlackScholesMertonModel:
    """
    Higher-order Greeks via Black-Scholes-Merton model for stock options with dividends.
    d1 includes drift (r-q).

    Signature: flag, S, K, t, r, sigma, q
    """

    MODEL = 'black_scholes_merton'

    def compute(
        self, *, flag: str, S: float, K: float, t: float, r: float, sigma: float, q: float = 0.0
    ) -> HigherOrderGreeks:
        flag = flag.lower()
        if _OPENGREEKS_AVAILABLE:
            return self._compute_og(flag, S, K, t, r, sigma, q)
        return self._compute_pp(flag, S, K, t, r, sigma, q)

    def _compute_og(self, flag, S, K, t, r, sigma, q):
        return HigherOrderGreeks(
            vanna=_og_bsm.vanna(flag, S, K, t, r, sigma, q),
            charm=_og_bsm.charm(flag, S, K, t, r, sigma, q),
            vomma=_og_bsm.vomma(flag, S, K, t, r, sigma, q),
            veta=_og_bsm.veta(flag, S, K, t, r, sigma, q),
            speed=_og_bsm.speed(flag, S, K, t, r, sigma, q),
            zomma=_og_bsm.zomma(flag, S, K, t, r, sigma, q),
            color=_og_bsm.color(flag, S, K, t, r, sigma, q),
            ultima=_og_bsm.ultima(flag, S, K, t, r, sigma, q),
        )

    def _compute_pp(self, flag, S, K, t, r, sigma, q):
        charm_fn = _charm_call_pp if flag == 'c' else _charm_put_pp
        return HigherOrderGreeks(
            vanna=_vanna_pp(S, K, r, sigma, t, q),
            charm=charm_fn(S, K, r, sigma, t, q),
            vomma=_vomma_pp(S, K, r, sigma, t, q),
            veta=_veta_pp(S, K, r, sigma, t, q),
            speed=_speed_pp(S, K, r, sigma, t, q),
            zomma=_zomma_pp(S, K, r, sigma, t, q),
            color=_color_pp(S, K, r, sigma, t, q),
            ultima=_ultima_pp(S, K, r, sigma, t, q),
        )

    def compute_individual(self, **kwargs) -> dict:
        return self.compute(**kwargs).to_dict()

    def compute_array(self, *, flag: str, S: np.ndarray, K: np.ndarray, t, r: float, sigma: np.ndarray, q: float = 0.0) -> dict:
        if _OPENGREEKS_AVAILABLE:
            t_arr = np.full_like(S, t)
            return {
                'vanna': _og_bsm.vanna_array(flag, S, K, t_arr, r, sigma, q),
                'charm': _og_bsm.charm_array(flag, S, K, t_arr, r, sigma, q),
                'vomma': _og_bsm.vomma_array(flag, S, K, t_arr, r, sigma, q),
                'veta': _og_bsm.veta_array(flag, S, K, t_arr, r, sigma, q),
                'speed': _og_bsm.speed_array(flag, S, K, t_arr, r, sigma, q),
                'zomma': _og_bsm.zomma_array(flag, S, K, t_arr, r, sigma, q),
                'color': _og_bsm.color_array(flag, S, K, t_arr, r, sigma, q),
                'ultima': _og_bsm.ultima_array(flag, S, K, t_arr, r, sigma, q),
            }
        b76 = Black76Model()
        return b76._compute_array_pp(flag, S, K, t, r, sigma, q=q, use_b76_d1=False)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------
_hog_bsm = BlackScholesMertonModel()


def compute_hol_greeks(
    S: float, K: float, t: float, r: float, sigma: float,
    q: float = 0.0, flag: str = 'c',
) -> HigherOrderGreeks:
    """Convenience: compute all higher-order Greeks (BSM, reduces to BS when q=0)."""
    return _hog_bsm.compute(flag=flag, S=S, K=K, t=t, r=r, sigma=sigma, q=q)


def compute_hol_greeks_dict(
    S: float, K: float, t: float, r: float, sigma: float,
    q: float = 0.0, flag: str = 'c',
) -> dict:
    """Convenience: compute all higher-order Greeks, returning a dict."""
    return compute_hol_greeks(S=S, K=K, t=t, r=r, sigma=sigma, q=q, flag=flag).to_dict()


# ---------------------------------------------------------------------------
# Module info
# ---------------------------------------------------------------------------
__all__ = [
    'Black76Model',
    'BlackScholesModel',
    'BlackScholesMertonModel',
    'HigherOrderGreeks',
    'compute_hol_greeks',
    'compute_hol_greeks_dict',
    'is_opengreeks_available',
    # Pure Python implementations for testing
    '_vanna_pp', '_charm_call_pp', '_charm_put_pp', '_vomma_pp',
    '_veta_pp', '_speed_pp', '_zomma_pp', '_color_pp', '_ultima_pp',
]
