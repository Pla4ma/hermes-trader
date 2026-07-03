#!/usr/bin/env python3
"""
Tests for IV Surface module — SVI fitting, IV computation, signals.
"""
import numpy as np
import pytest

from hermes_trader.iv_surface import (
    IVSurface,
    Signal,
    SVIParams,
    bs_implied_vol,
    bs_price,
    delta_to_strike,
    get_10d_strikes,
    get_25d_strikes,
    svi_fit,
    svi_implied_vol,
    svi_total_variance,
    ssvi_fit,
    ssvi_total_variance,
)


# ── Black-Scholes helpers ─────────────────────────────────────────────

class TestBSPrice:
    def test_call_at_the_money(self):
        """ATM call should be ~0.4 * S * sqrt(T) for small rates."""
        price = bs_price(100, 100, 0.25, 0.20, 0.05, "call")
        assert 3.0 < price < 8.0  # rough ATM call range

    def test_put_put_call_parity(self):
        """C - P = e^{-rT}(F - K)."""
        S, K, T, sigma, r = 100, 95, 0.5, 0.25, 0.05
        call = bs_price(S, K, T, sigma, r, "call")
        put = bs_price(S, K, T, sigma, r, "put")
        F = S * np.exp(r * T)
        parity = np.exp(-r * T) * (F - K)
        assert abs((call - put) - parity) < 1e-6

    def test_zero_time(self):
        """At expiry, value = intrinsic."""
        assert bs_price(110, 100, 0.0, 0.20, 0.05, "call") == pytest.approx(10.0)
        assert bs_price(90, 100, 0.0, 0.20, 0.05, "put") == pytest.approx(10.0)
        assert bs_price(110, 100, 0.0, 0.20, 0.05, "put") == pytest.approx(0.0)


class TestBSImpliedVol:
    def test_roundtrip(self):
        """Pricing then recovering IV should be close to the input."""
        S, K, T, sigma, r = 100, 105, 0.25, 0.25, 0.05
        price = bs_price(S, K, T, sigma, r, "call")
        iv = bs_implied_vol(price, S, K, T, r, "call")
        assert iv is not None
        assert iv == pytest.approx(sigma, abs=1e-6)

    def test_put_roundtrip(self):
        S, K, T, sigma, r = 100, 90, 0.5, 0.30, 0.03
        price = bs_price(S, K, T, sigma, r, "put")
        iv = bs_implied_vol(price, S, K, T, r, "put")
        assert iv is not None
        assert iv == pytest.approx(sigma, abs=1e-6)

    def test_returns_none_for_zero_price(self):
        assert bs_implied_vol(0, 100, 100, 0.25, 0.05) is None

    def test_returns_none_for_zero_tte(self):
        assert bs_implied_vol(5.0, 100, 100, 0.0, 0.05) is None


# ── SVI ───────────────────────────────────────────────────────────────

class TestSVI:
    def test_svi_formula_shape(self):
        """SVI should produce a smile-shaped curve."""
        k = np.linspace(-0.3, 0.3, 50)
        w = svi_total_variance(k, a=0.02, b=0.1, rho=-0.3, m=0.0, sigma=0.2)
        assert len(w) == 50
        # Wings should be higher than ATM (smile)
        assert w[0] > w[25]
        assert w[-1] > w[25]

    def test_svi_fit_recovers_params(self):
        """Fitting SVI to synthetic data should recover near-true params."""
        true_params = (0.02, 0.1, -0.3, 0.0, 0.2)
        k = np.linspace(-0.3, 0.3, 20)
        w_true = svi_total_variance(k, *true_params)
        fitted = svi_fit(k, w_true)
        for fi, ti in zip(fitted, true_params):
            assert fi == pytest.approx(ti, abs=0.05)

    def test_svi_implied_vol_positive(self):
        """SVI IV should always be positive."""
        iv = svi_implied_vol(0.0, 0.25, 0.02, 0.1, -0.3, 0.0, 0.2)
        assert iv > 0

    def test_svi_params_dataclass(self):
        p = SVIParams(0.01, 0.1, -0.5, 0.0, 0.3)
        assert p.to_tuple() == (0.01, 0.1, -0.5, 0.0, 0.3)


# ── SSVI ──────────────────────────────────────────────────────────────

class TestSSVI:
    def test_ssvi_total_variance_nonneg(self):
        """SSVI variance should be non-negative."""
        w = ssvi_total_variance(0.0, 0.25, 0.04, 0.5, -0.3)
        assert w >= 0

    def test_ssvi_fit_converges(self):
        """Fitting SSVI to multi-expiry synthetic data should converge."""
        rho_true, theta_p_true, phi_p_true = -0.3, np.array([0.04, 0.1]), np.array([0.5, 1.0])
        expiries = [0.25, 0.5, 1.0]
        all_k, all_T, all_w = [], [], []
        for T in expiries:
            k = np.linspace(-0.2, 0.2, 15)
            w = np.array([
                ssvi_total_variance(ki, T,
                                    _theta_true(T, theta_p_true),
                                    _phi_true(T, phi_p_true), rho_true)
                for ki in k
            ])
            all_k.extend(k)
            all_T.extend([T] * len(k))
            all_w.extend(w)

        rho_f, theta_f, phi_f = ssvi_fit(np.array(all_k), np.array(all_T), np.array(all_w))
        assert rho_f == pytest.approx(rho_true, abs=0.1)
        for a, b in zip(theta_f, theta_p_true):
            assert a == pytest.approx(b, abs=0.02)


def _theta_true(T, p):
    return p[0] * T + p[1] * T**2

def _phi_true(T, p):
    return p[0] / (1 + p[1] * T)


# ── Delta-strike conversion ──────────────────────────────────────────

class TestDeltaStrike:
    def test_atm_put_delta_near_half(self):
        """25Δ put strike should be below forward."""
        fwd = 100.0
        put_strike = delta_to_strike(fwd, -0.25, 0.25, 0.20, "put")
        assert put_strike < fwd

    def test_atm_call_delta_above_forward(self):
        call_strike = delta_to_strike(100.0, 0.25, 0.25, 0.20, "call")
        assert call_strike > 100.0

    def test_25d_strikes_symmetry(self):
        """Put 25Δ strike < fwd < Call 25Δ strike."""
        p25, c25 = get_25d_strikes(100.0, 0.25, 0.20)
        assert p25 < 100.0 < c25

    def test_10d_strikes_wider(self):
        """10Δ strikes should be further OTM than 25Δ."""
        p25, c25 = get_25d_strikes(100.0, 0.25, 0.20)
        p10, c10 = get_10d_strikes(100.0, 0.25, 0.20)
        assert p10 < p25  # put 10Δ is further OTM (lower strike)
        assert c10 > c25  # call 10Δ is further OTM (higher strike)


# ── IVSurface integration ─────────────────────────────────────────────

def _make_surface() -> IVSurface:
    """Build a test surface with 2 expiry slices of synthetic data."""
    surface = IVSurface(spot=100.0, rate=0.05)
    for T, fwd in [(0.25, 100.0), (0.5, 101.0), (1.0, 102.0)]:
        strikes = np.linspace(80, 120, 15)
        # Synthetic smile: higher IV in the wings
        moneyness = np.log(strikes / fwd)
        ivs = 0.20 + 0.15 * moneyness**2 - 0.05 * moneyness
        ivs = np.clip(ivs, 0.05, 1.0)
        surface.add_expiry(T, strikes, ivs, fwd)
    return surface


class TestIVSurface:
    def test_add_expiry(self):
        s = IVSurface(100.0)
        strikes = np.array([90, 100, 110])
        ivs = np.array([0.25, 0.20, 0.22])
        s.add_expiry(0.25, strikes, ivs, 100.0)
        assert 0.25 in s.slices
        assert len(s.slices[0.25].strikes) == 3

    def test_fit_svi(self):
        s = _make_surface()
        params = s.fit_svi()
        assert len(params) == 3
        for T, p in params.items():
            assert p.b >= 0
            assert -1 < p.rho < 1
            assert p.sigma > 0

    def test_get_iv_after_fit(self):
        s = _make_surface()
        s.fit_svi()
        iv = s.get_iv(100.0, 0.25, method="svi")
        assert 0.05 < iv < 1.0

    def test_atm_iv(self):
        s = _make_surface()
        s.fit_svi()
        iv = s.atm_iv(0.25)
        assert 0.05 < iv < 1.0

    def test_fit_ssvi(self):
        s = _make_surface()
        rho, theta_p, phi_p = s.fit_ssvi()
        assert -1 < rho < 1
        assert all(p >= 0 for p in theta_p)
        assert all(p >= 0 for p in phi_p)

    def test_get_iv_ssvi(self):
        s = _make_surface()
        s.fit_ssvi()
        iv = s.get_iv(100.0, 0.25, method="ssvi")
        assert 0.01 < iv < 2.0

    def test_linear_fallback(self):
        s = IVSurface(100.0, rate=0.05)
        strikes = np.array([90, 100, 110])
        ivs = np.array([0.25, 0.20, 0.22])
        s.add_expiry(0.25, strikes, ivs, 100.0)
        # No SVI fit — should use linear interpolation
        iv = s.get_iv(105.0, 0.25, method="svi")
        assert 0.01 < iv < 1.0

    def test_skew(self):
        s = _make_surface()
        s.fit_svi()
        skew_val = s.skew(0.25)
        # With our synthetic data (negative moneyness coefficient), skew should be nonzero
        assert isinstance(skew_val, float)

    def test_smile_curvature(self):
        s = _make_surface()
        s.fit_svi()
        curv = s.smile_curvature(0.25)
        assert isinstance(curv, float)

    def test_term_structure_slope(self):
        s = _make_surface()
        s.fit_svi()
        slope = s.term_structure_slope(0.25, 0.5)
        assert isinstance(slope, float)

    def test_generate_signals(self):
        s = _make_surface()
        s.fit_svi()
        signals = s.generate_signals(0.25)
        assert "skew_signal" in signals
        assert "term_signal" in signals
        assert "curvature_signal" in signals
        assert signals["skew_signal"] in [e.value for e in Signal]
        assert signals["term_signal"] in [e.value for e in Signal]
        assert signals["curvature_signal"] in [e.value for e in Signal]

    def test_arbitrage_check_clean(self):
        """Synthetic smile data should pass butterfly check."""
        s = IVSurface(100.0)
        strikes = np.array([90, 95, 100, 105, 110])
        ivs = np.array([0.28, 0.24, 0.22, 0.21, 0.23])
        s.add_expiry(0.25, strikes, ivs, 100.0)
        clean, msg = s.arbitrage_check(0.25)
        assert clean is True

    def test_arbitrage_check_violation(self):
        """Construct data with a butterfly violation."""
        s = IVSurface(100.0)
        # Put a dip in the middle that violates convexity
        strikes = np.array([90, 95, 100, 105, 110])
        ivs = np.array([0.20, 0.20, 0.10, 0.20, 0.20])  # sharp dip
        s.add_expiry(0.25, strikes, ivs, 100.0)
        clean, msg = s.arbitrage_check(0.25)
        assert clean is False
        assert "violation" in msg.lower()

    def test_surface_grid(self):
        s = _make_surface()
        s.fit_svi()
        k_grid, t_grid, iv_grid = s.get_surface_grid(n_k=10, n_t=5)
        assert k_grid.shape == (10,)
        assert t_grid.shape == (5,)
        assert iv_grid.shape == (5, 10)
        assert np.all(iv_grid > 0)

    def test_add_option_chain(self):
        """Test raw chain ingestion with IV computation."""
        s = IVSurface(spot=100.0, rate=0.05)
        chain = [
            {"strike": 95, "bid": 8.0, "ask": 8.5, "volume": 100, "open_interest": 200, "option_type": "call"},
            {"strike": 100, "bid": 5.0, "ask": 5.3, "volume": 200, "open_interest": 300, "option_type": "call"},
            {"strike": 105, "bid": 2.5, "ask": 2.8, "volume": 150, "open_interest": 250, "option_type": "call"},
            {"strike": 90, "bid": 0.001, "ask": 0.005, "volume": 5, "open_interest": 10, "option_type": "call"},  # filtered out
        ]
        s.add_option_chain(chain, expiry=0.25, forward=100.0, min_volume=10, min_open_interest=50, min_bid=0.01)
        assert 0.25 in s.slices
        assert len(s.slices[0.25].strikes) == 3  # low-volume one filtered

    def test_signal_enum_values(self):
        """Ensure all signal enum values are strings."""
        for sig in Signal:
            assert isinstance(sig.value, str)
