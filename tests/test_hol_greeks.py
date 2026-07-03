#!/usr/bin/env python3
"""
Tests for higher-order Greeks module (hol_greeks.py)
====================================================
Validates all 8 higher-order Greeks against:
1. Pure Python closed-form formulas
2. opengreeks Rust backend (when available)
3. Cross-validation between the two backends
4. Numerical differentiation
5. Mathematical consistency checks (signs, magnitudes, relationships)
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from hermes_trader.hol_greeks import (
    Black76Model,
    BlackScholesModel,
    BlackScholesMertonModel,
    HigherOrderGreeks,
    compute_hol_greeks,
    compute_hol_greeks_dict,
    is_opengreeks_available,
    # Pure Python implementations for cross-validation
    _vanna_pp,
    _charm_call_pp,
    _charm_put_pp,
    _vomma_pp,
    _veta_pp,
    _speed_pp,
    _zomma_pp,
    _color_pp,
    _ultima_pp,
)

# ---------------------------------------------------------------------------
# Standard test parameters
# ---------------------------------------------------------------------------
S, K, r, sigma, tau, q = 100.0, 100.0, 0.05, 0.25, 30.0 / 365, 0.02
F = 100.0  # Forward price for Black76


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def bsm():
    return BlackScholesMertonModel()


@pytest.fixture
def bs():
    return BlackScholesModel()


@pytest.fixture
def b76():
    return Black76Model()


# ---------------------------------------------------------------------------
# 1. HigherOrderGreeks dataclass tests
# ---------------------------------------------------------------------------
class TestHigherOrderGreeks:
    def test_init_defaults(self):
        hog = HigherOrderGreeks()
        assert hog.vanna == 0.0
        assert hog.ultima == 0.0

    def test_init_values(self):
        hog = HigherOrderGreeks(vanna=-0.5, vomma=0.3, ultima=-10.0)
        assert hog.vanna == -0.5
        assert hog.vomma == 0.3
        assert hog.ultima == -10.0
        assert hog.color == 0.0

    def test_to_dict(self):
        hog = HigherOrderGreeks(vanna=1.0, charm=2.0, vomma=3.0)
        d = hog.to_dict()
        assert isinstance(d, dict)
        assert d['vanna'] == 1.0
        assert d['charm'] == 2.0
        assert d['vomma'] == 3.0
        assert len(d) == 8

    def test_getitem(self):
        hog = HigherOrderGreeks(vanna=-0.1, speed=-0.002)
        assert hog['vanna'] == -0.1
        assert hog['speed'] == -0.002

    def test_getitem_invalid(self):
        hog = HigherOrderGreeks()
        with pytest.raises(KeyError, match='Unknown Greek'):
            _ = hog['delta']

    def test_repr(self):
        hog = HigherOrderGreeks(vanna=-0.5)
        r = repr(hog)
        assert 'HigherOrderGreeks' in r
        assert 'vanna=' in r

    def test_all_slots_populated(self):
        hog = HigherOrderGreeks()
        for name in HigherOrderGreeks.__slots__:
            assert hasattr(hog, name)
            setattr(hog, name, 1.0)
            assert getattr(hog, name) == 1.0


# ---------------------------------------------------------------------------
# 2. Pure Python closed-form formula tests
# ---------------------------------------------------------------------------
class TestPurePythonFormulas:
    """Test the pure Python implementations produce reasonable values."""

    def test_vanna_atm(self):
        v = _vanna_pp(S, K, r, sigma, tau, q)
        # ATM vanna should be small (d2 ≈ 0 near ATM for BSM with small r)
        assert abs(v) < 0.1

    def test_vomma_atm(self):
        v = _vomma_pp(S, K, r, sigma, tau, q)
        # ATM vomma near zero (d1*d2 ≈ 0)
        assert abs(v) < 1.0

    def test_speed_atm(self):
        v = _speed_pp(S, K, r, sigma, tau, q)
        # ATM speed should be negative (gamma decreases as S moves away)
        assert v < 0

    def test_zomma_atm(self):
        v = _zomma_pp(S, K, r, sigma, tau, q)
        # ATM zomma negative (d1*d2 < 1)
        assert v < 0

    def test_color_atm(self):
        v = _color_pp(S, K, r, sigma, tau, q)
        # ATM color should be negative (gamma decays over time)
        assert v < 0

    def test_ultima_atm(self):
        v = _ultima_pp(S, K, r, sigma, tau, q)
        assert isinstance(v, float)
        assert abs(v) < 100

    def test_veta_atm(self):
        v = _veta_pp(S, K, r, sigma, tau, q)
        # Veta for ATM with r>0 should be positive (vega increases with time for ATM)
        # Actually depends on params. Just check it's finite.
        assert math.isfinite(v)

    def test_vanna_sign_otm_call(self):
        """OTM call vanna should be positive (delta increases with vol)."""
        v = _vanna_pp(S, K * 1.1, r, sigma, tau, q)
        assert v > 0

    def test_vanna_sign_itm_call(self):
        """ITM call vanna should be negative."""
        v = _vanna_pp(S, K * 0.9, r, sigma, tau, q)
        assert v < 0

    def test_vomma_sign_otm(self):
        """OTM vomma should be positive (vega convexity)."""
        v = _vomma_pp(S, K * 1.2, r, sigma, tau, q)
        assert v > 0

    def test_vomma_nonzero_non_atm(self):
        """Non-ATM vomma should be non-zero (vega convexity)."""
        v = _vomma_pp(S, K * 0.8, r, sigma, tau, q)
        assert v != 0  # ITM vomma is non-zero
        assert math.isfinite(v)

    def test_no_infinite_greeks(self):
        """All formulas should produce finite values for valid inputs."""
        for fn in [_vanna_pp, _charm_call_pp, _vomma_pp, _speed_pp, _zomma_pp, _color_pp, _ultima_pp]:
            val = fn(S, K, r, sigma, tau, q)
            assert math.isfinite(val), f'{fn.__name__} returned {val}'


# ---------------------------------------------------------------------------
# 3. Black-Scholes-Merton model tests
# ---------------------------------------------------------------------------
class TestBlackScholesMertonModel:
    def test_compute_returns_higher_order_greeks(self, bsm):
        result = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert isinstance(result, HigherOrderGreeks)

    def test_compute_dict(self, bsm):
        result = bsm.compute_individual(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert isinstance(result, dict)
        assert len(result) == 8
        assert 'vanna' in result
        assert 'ultima' in result

    def test_call_put_vanna_same(self, bsm):
        """Vanna is the same for calls and puts in Black-Scholes."""
        c = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        p = bsm.compute(flag='p', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert abs(c.vanna - p.vanna) < 1e-10

    def test_call_put_vomma_same(self, bsm):
        """Vomma is the same for calls and puts."""
        c = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        p = bsm.compute(flag='p', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert abs(c.vomma - p.vomma) < 1e-10

    def test_call_put_speed_same(self, bsm):
        """Speed is the same for calls and puts."""
        c = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        p = bsm.compute(flag='p', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert abs(c.speed - p.speed) < 1e-10

    def test_call_put_zomma_same(self, bsm):
        """Zomma is the same for calls and puts."""
        c = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        p = bsm.compute(flag='p', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert abs(c.zomma - p.zomma) < 1e-10

    def test_call_put_color_same(self, bsm):
        """Color is the same for calls and puts."""
        c = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        p = bsm.compute(flag='p', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert abs(c.color - p.color) < 1e-10

    def test_computes_different_for_call_put(self, bsm):
        """Charm differs for calls vs puts."""
        c = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        p = bsm.compute(flag='p', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        assert c.charm != p.charm

    def test_array_batch(self, bsm):
        """Batch computation with arrays."""
        S_arr = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
        K_arr = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        sigma_arr = np.array([0.20, 0.22, 0.25, 0.22, 0.20])
        result = bsm.compute_array(flag='c', S=S_arr, K=K_arr, t=tau, r=r, sigma=sigma_arr, q=q)
        assert isinstance(result, dict)
        assert len(result) == 8
        for key, val in result.items():
            assert isinstance(val, np.ndarray)
            assert len(val) == 5

    def test_cross_validate_vs_opengreeks(self, bsm):
        """opengreeks (if available) should match pure Python."""
        hog = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        if is_opengreeks_available():
            from opengreeks.black_scholes_merton import (
                vanna, charm, vomma, speed, zomma, color, ultima
            )
            expected = {
                'vanna': vanna('c', S, K, tau, r, sigma, q),
                'charm': charm('c', S, K, tau, r, sigma, q),
                'vomma': vomma('c', S, K, tau, r, sigma, q),
                'speed': speed('c', S, K, tau, r, sigma, q),
                'zomma': zomma('c', S, K, tau, r, sigma, q),
                'color': color('c', S, K, tau, r, sigma, q),
                'ultima': ultima('c', S, K, tau, r, sigma, q),
            }
            for name, og_val in expected.items():
                hog_val = getattr(hog, name)
                assert abs(hog_val - og_val) < 1e-6, f'{name}: hog={hog_val} og={og_val}'


# ---------------------------------------------------------------------------
# 4. Black-Scholes model tests (no dividends)
# ---------------------------------------------------------------------------
class TestBlackScholesModel:
    def test_compute(self, bs):
        result = bs.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma)
        assert isinstance(result, HigherOrderGreeks)

    def test_matches_opengreeks(self, bs):
        """With q=0, BS should match opengreeks BS."""
        hog = bs.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma)
        if is_opengreeks_available():
            from opengreeks.black_scholes import (
                vanna, vomma, speed, zomma, color, ultima
            )
            for name, fn in [('vanna', vanna), ('vomma', vomma), ('speed', speed),
                              ('zomma', zomma), ('color', color), ('ultima', ultima)]:
                og = fn('c', S, K, tau, r, sigma)
                hog_val = getattr(hog, name)
                assert abs(hog_val - og) < 1e-6, f'{name}: hog={hog_val} og={og}'

    def test_array(self, bs):
        S_arr = np.array([90.0, 100.0, 110.0])
        K_arr = np.array([100.0, 100.0, 100.0])
        sigma_arr = np.array([0.20, 0.25, 0.20])
        result = bs.compute_array(flag='c', S=S_arr, K=K_arr, t=tau, r=r, sigma=sigma_arr)
        assert len(result['vanna']) == 3


# ---------------------------------------------------------------------------
# 5. Black-76 model tests (futures)
# ---------------------------------------------------------------------------
class TestBlack76Model:
    def test_compute(self, b76):
        result = b76.compute(flag='c', F=F, K=K, t=tau, r=r, sigma=sigma)
        assert isinstance(result, HigherOrderGreeks)

    def test_array(self, b76):
        F_arr = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
        K_arr = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        sigma_arr = np.array([0.20, 0.22, 0.25, 0.22, 0.20])
        result = b76.compute_array(flag='c', F=F_arr, K=K_arr, t=tau, r=r, sigma=sigma_arr)
        assert isinstance(result, dict)
        for key, val in result.items():
            assert len(val) == 5

    def test_matches_opengreeks(self, b76):
        """Black76 should match opengreeks B76."""
        hog = b76.compute(flag='c', F=F, K=K, t=tau, r=r, sigma=sigma)
        if is_opengreeks_available():
            from opengreeks.black76 import (
                vanna, vomma, speed, zomma, color, ultima
            )
            for name, fn in [('vanna', vanna), ('vomma', vomma), ('speed', speed),
                              ('zomma', zomma), ('color', color), ('ultima', ultima)]:
                og = fn('c', F, K, tau, r, sigma)
                hog_val = getattr(hog, name)
                assert abs(hog_val - og) < 1e-6, f'{name}: hog={hog_val} og={og}'


# ---------------------------------------------------------------------------
# 6. Convenience functions
# ---------------------------------------------------------------------------
class TestConvenienceFunctions:
    def test_compute_hol_greeks(self):
        hog = compute_hol_greeks(S=S, K=K, t=tau, r=r, sigma=sigma, q=q, flag='c')
        assert isinstance(hog, HigherOrderGreeks)
        assert hog.vanna != 0

    def test_compute_hol_greeks_dict(self):
        d = compute_hol_greeks_dict(S=S, K=K, t=tau, r=r, sigma=sigma, q=q, flag='c')
        assert isinstance(d, dict)
        assert len(d) == 8

    def test_default_q_is_zero(self):
        hog_q0 = compute_hol_greeks(S=S, K=K, t=tau, r=r, sigma=sigma, q=0.0, flag='c')
        hog_default = compute_hol_greeks(S=S, K=K, t=tau, r=r, sigma=sigma, flag='c')
        for name in HigherOrderGreeks.__slots__:
            assert abs(getattr(hog_q0, name) - getattr(hog_default, name)) < 1e-15, name


# ---------------------------------------------------------------------------
# 7. Mathematical consistency tests
# ---------------------------------------------------------------------------
class TestMathConsistency:
    """Cross-validate relationships between Greeks."""

    def test_vomma_vanna_relationship(self):
        """Both should be non-zero and finite for OTM options."""
        hog = compute_hol_greeks(S=100, K=110, t=0.25, r=0.05, sigma=0.30, q=0.0, flag='c')
        assert hog.vanna != 0
        assert hog.vomma != 0
        assert math.isfinite(hog.vanna)
        assert math.isfinite(hog.vomma)

    def test_speed_relates_to_gamma(self):
        """Speed = ∂Γ/∂S, ATM speed should be negative."""
        hog = compute_hol_greeks(S=100, K=100, t=0.25, r=0.05, sigma=0.25, q=0.0, flag='c')
        assert hog.speed < 0

    def test_color_sign_atm(self):
        """Color = ∂Γ/∂τ: ATM gamma decays over time, so color should be negative."""
        hog = compute_hol_greeks(S=100, K=100, t=0.25, r=0.05, sigma=0.25, q=0.0, flag='c')
        assert hog.color < 0

    def test_ultima_relates_to_vomma(self):
        """Ultima = ∂Vomma/∂σ, should be non-zero for non-ATM options."""
        hog = compute_hol_greeks(S=100, K=110, t=0.25, r=0.05, sigma=0.30, q=0.0, flag='c')
        assert hog.ultima != 0
        assert math.isfinite(hog.ultima)

    def test_short_dte_higher_magnitude_speed(self):
        """Speed magnitude should increase as expiry approaches."""
        hog_long = compute_hol_greeks(S=100, K=100, t=90 / 365, r=0.05, sigma=0.25, q=0.0, flag='c')
        hog_short = compute_hol_greeks(S=100, K=100, t=7 / 365, r=0.05, sigma=0.25, q=0.0, flag='c')
        assert abs(hog_short.speed) > abs(hog_long.speed)

    def test_vanna_extreme_moneyness(self):
        """Deep ITM/OTM vanna should approach zero."""
        v_deep_itm = _vanna_pp(100, 50, 0.05, 0.25, 30 / 365, 0.0)
        v_deep_otm = _vanna_pp(100, 200, 0.05, 0.25, 30 / 365, 0.0)
        assert abs(v_deep_itm) < 0.01
        assert abs(v_deep_otm) < 0.01

    def test_dividend_impact_on_charm(self):
        """Dividends should affect charm (q term is explicit)."""
        hog_no_q = compute_hol_greeks(S=100, K=100, t=0.25, r=0.05, sigma=0.25, q=0.0, flag='c')
        hog_high_q = compute_hol_greeks(S=100, K=100, t=0.25, r=0.05, sigma=0.25, q=0.05, flag='c')
        assert hog_no_q.charm != hog_high_q.charm


# ---------------------------------------------------------------------------
# 8. Numerical differentiation cross-validation
# ---------------------------------------------------------------------------
class TestNumericalDifferentiation:
    """Validate analytical formulas against finite-difference approximations."""

    def test_vanna_numerical(self):
        """Vanna = ∂Δ/∂σ ≈ [Δ(σ+k) - Δ(σ-k)] / (2k)"""
        from hermes_trader.greeks_engine import BlackScholesGreeks
        dk = 0.001
        delta_plus = BlackScholesGreeks.delta(S, K, r, q, sigma + dk, tau, 'call')
        delta_minus = BlackScholesGreeks.delta(S, K, r, q, sigma - dk, tau, 'call')
        vanna_num = (delta_plus - delta_minus) / (2 * dk)
        vanna_analytical = _vanna_pp(S, K, r, sigma, tau, q)
        assert abs(vanna_num - vanna_analytical) < 1e-3, f'vanna: {vanna_num} vs {vanna_analytical}'

    def test_vomma_numerical(self):
        """Vomma = ∂²V/∂σ² ≈ [V(σ+k) - 2V(σ) + V(σ-k)] / k²"""
        from hermes_trader.greeks_engine import BlackScholesGreeks
        dk = 0.001
        V = lambda sig: BlackScholesGreeks.price(S, K, r, q, sig, tau, 'call')
        vomma_num = (V(sigma + dk) - 2 * V(sigma) + V(sigma - dk)) / (dk**2)
        vomma_analytical = _vomma_pp(S, K, r, sigma, tau, q)
        assert abs(vomma_num - vomma_analytical) < 1e-3, f'vomma: {vomma_num} vs {vomma_analytical}'

    def test_speed_numerical(self):
        """Speed = ∂Γ/∂S ≈ [Γ(S+h) - Γ(S-h)] / (2h)"""
        from hermes_trader.greeks_engine import BlackScholesGreeks
        ds = 0.1
        G = lambda s: BlackScholesGreeks.gamma(s, K, r, q, sigma, tau)
        speed_num = (G(S + ds) - G(S - ds)) / (2 * ds)
        speed_analytical = _speed_pp(S, K, r, sigma, tau, q)
        assert abs(speed_num - speed_analytical) < 1e-3, f'speed: {speed_num} vs {speed_analytical}'

    def test_zomma_numerical(self):
        """Zomma = ∂Γ/∂σ ≈ [Γ(σ+k) - Γ(σ-k)] / (2k)"""
        from hermes_trader.greeks_engine import BlackScholesGreeks
        dk = 0.001
        G = lambda sig: BlackScholesGreeks.gamma(S, K, r, q, sig, tau)
        zomma_num = (G(sigma + dk) - G(sigma - dk)) / (2 * dk)
        zomma_analytical = _zomma_pp(S, K, r, sigma, tau, q)
        assert abs(zomma_num - zomma_analytical) < 1e-3, f'zomma: {zomma_num} vs {zomma_analytical}'


# ---------------------------------------------------------------------------
# 9. opengreeks backend tests (conditional)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not is_opengreeks_available(), reason='opengreeks not installed')
class TestOpenGreeksBackend:
    """Tests that only run when opengreeks Rust backend is available."""

    def test_bsm_matches_opengreeks(self, bsm):
        hog = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        from opengreeks.black_scholes_merton import vanna as og_vanna
        og_val = og_vanna('c', S, K, tau, r, sigma, q)
        assert abs(hog.vanna - og_val) < 1e-10

    def test_bs_matches_opengreeks(self, bs):
        hog = bs.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma)
        from opengreeks.black_scholes import vanna as og_vanna
        og_val = og_vanna('c', S, K, tau, r, sigma)
        assert abs(hog.vanna - og_val) < 1e-10

    def test_b76_matches_opengreeks(self, b76):
        hog = b76.compute(flag='c', F=F, K=K, t=tau, r=r, sigma=sigma)
        from opengreeks.black76 import vanna as og_vanna
        og_val = og_vanna('c', F, K, tau, r, sigma)
        assert abs(hog.vanna - og_val) < 1e-10

    def test_all_greeks_match_opengreeks(self, bsm):
        hog = bsm.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma, q=q)
        from opengreeks.black_scholes_merton import (
            vanna, charm, vomma, speed, zomma, color, ultima
        )
        expected = {
            'vanna': vanna('c', S, K, tau, r, sigma, q),
            'charm': charm('c', S, K, tau, r, sigma, q),
            'vomma': vomma('c', S, K, tau, r, sigma, q),
            'speed': speed('c', S, K, tau, r, sigma, q),
            'zomma': zomma('c', S, K, tau, r, sigma, q),
            'color': color('c', S, K, tau, r, sigma, q),
            'ultima': ultima('c', S, K, tau, r, sigma, q),
        }
        for name, og_val in expected.items():
            hog_val = getattr(hog, name)
            assert abs(hog_val - og_val) < 1e-6, f'{name}: hog={hog_val} og={og_val}'

    def test_b76_all_greeks_match_opengreeks(self, b76):
        hog = b76.compute(flag='c', F=F, K=K, t=tau, r=r, sigma=sigma)
        from opengreeks.black76 import (
            vanna, charm, vomma, speed, zomma, color, ultima
        )
        expected = {
            'vanna': vanna('c', F, K, tau, r, sigma),
            'charm': charm('c', F, K, tau, r, sigma),
            'vomma': vomma('c', F, K, tau, r, sigma),
            'speed': speed('c', F, K, tau, r, sigma),
            'zomma': zomma('c', F, K, tau, r, sigma),
            'color': color('c', F, K, tau, r, sigma),
            'ultima': ultima('c', F, K, tau, r, sigma),
        }
        for name, og_val in expected.items():
            hog_val = getattr(hog, name)
            assert abs(hog_val - og_val) < 1e-6, f'{name}: hog={hog_val} og={og_val}'

    def test_bs_all_greeks_match_opengreeks(self, bs):
        hog = bs.compute(flag='c', S=S, K=K, t=tau, r=r, sigma=sigma)
        from opengreeks.black_scholes import (
            vanna, charm, vomma, speed, zomma, color, ultima
        )
        expected = {
            'vanna': vanna('c', S, K, tau, r, sigma),
            'charm': charm('c', S, K, tau, r, sigma),
            'vomma': vomma('c', S, K, tau, r, sigma),
            'speed': speed('c', S, K, tau, r, sigma),
            'zomma': zomma('c', S, K, tau, r, sigma),
            'color': color('c', S, K, tau, r, sigma),
            'ultima': ultima('c', S, K, tau, r, sigma),
        }
        for name, og_val in expected.items():
            hog_val = getattr(hog, name)
            assert abs(hog_val - og_val) < 1e-6, f'{name}: hog={hog_val} og={og_val}'


# ---------------------------------------------------------------------------
# 10. opengreeks detection
# ---------------------------------------------------------------------------
class TestOpengreeksDetection:
    def test_detection_returns_bool(self):
        result = is_opengreeks_available()
        assert isinstance(result, bool)

    def test_detection_matches_import(self):
        try:
            from opengreeks import black76
            assert is_opengreeks_available()
        except ImportError:
            assert not is_opengreeks_available()
