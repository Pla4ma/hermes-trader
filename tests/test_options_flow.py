"""Tests for options_flow module — mocked yfinance, no network calls."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from hermes_trader.options_flow import (
    DEFAULT_VOLUME_MULTIPLIER,
    MIN_SWEEP_SIZE,
    MIN_SWEEP_PREMIUM,
    AGGRESSIVE_FILL_THRESHOLD,
    MIN_VOLUME_FLOOR,
    FlowSentiment,
    PutCallFlow,
    SweepAlert,
    UnusualVolumeAlert,
    OptionsFlowDetector,
    detect_unusual_volume,
    detect_sweeps,
    calculate_put_call_flow,
    get_flow_sentiment,
)


# ── Fixtures ───────────────────────────────────────────────────────

def _make_call_row(
    strike=550, vol=500, oi=1000, bid=1.0, ask=1.5, last=1.3, iv=0.25
):
    return {
        "strike": strike,
        "volume": vol,
        "openInterest": oi,
        "bid": bid,
        "ask": ask,
        "lastPrice": last,
        "impliedVolatility": iv,
    }


def _make_put_row(strike=540, vol=200, oi=800, bid=0.8, ask=1.2, last=1.0, iv=0.30):
    return {**_make_call_row(strike, vol, oi, bid, ask, last, iv)}


def _mock_option_chain(calls, puts):
    chain = MagicMock()
    chain.calls = pd.DataFrame(calls) if calls else pd.DataFrame()
    chain.puts = pd.DataFrame(puts) if puts else pd.DataFrame()
    return chain


def _patch_yfinance(monkeypatch, today_iso=None, chain_factory=None):
    """Patch yfinance.Ticker to return controlled test data."""
    if today_iso is None:
        today_iso = date.today().isoformat()
    if chain_factory is None:
        chain_factory = lambda: _mock_option_chain(
            [_make_call_row()], [_make_put_row()]
        )

    mock_ticker = MagicMock()
    mock_ticker.options = [today_iso]
    mock_ticker.option_chain = MagicMock(return_value=chain_factory())
    mock_ticker.fast_info = {"lastPrice": 550.0}
    mock_ticker.history.return_value = pd.DataFrame(
        {"Volume": [10_000_000] * 20}
    )

    import hermes_trader.options_flow as mod
    orig = mod.OptionsFlowDetector._get_ticker

    def _fake_get_ticker(self, symbol="SPY"):
        return mock_ticker

    monkeypatch.setattr(mod.OptionsFlowDetector, "_get_ticker", _fake_get_ticker)
    return mock_ticker


# ── Tests ──────────────────────────────────────────────────────────


class TestDetectUnusualVolume:
    def test_basic(self, monkeypatch):
        _patch_yfinance(monkeypatch)
        detector = OptionsFlowDetector()
        alerts = detector.detect_unusual_volume("SPY", max_dte=5)
        assert isinstance(alerts, list)
        # Our mock has 1 call with vol=500; avg_vol is 600k * 0.3 ≈ 180k
        # 500 < 180k*3 so no alert expected — that's correct behaviour
        for a in alerts:
            assert isinstance(a, UnusualVolumeAlert)
            assert a.volume_ratio >= DEFAULT_VOLUME_MULTIPLIER

    def test_empty_chain(self, monkeypatch):
        _patch_yfinance(
            monkeypatch,
            chain_factory=lambda: _mock_option_chain([], []),
        )
        alerts = OptionsFlowDetector().detect_unusual_volume("SPY", max_dte=1)
        assert alerts == []

    def test_volume_floor(self, monkeypatch):
        """Contracts below volume floor are excluded."""
        chain = _mock_option_chain(
            [_make_call_row(vol=10)],  # vol=10 < MIN_VOLUME_FLOOR=50
            [],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        alerts = OptionsFlowDetector().detect_unusual_volume("SPY", max_dte=1)
        assert all(a.current_volume >= MIN_VOLUME_FLOOR for a in alerts)

    def test_module_level_function(self, monkeypatch):
        _patch_yfinance(monkeypatch)
        result = detect_unusual_volume("SPY", max_dte=1)
        assert isinstance(result, list)


class TestDetectSweeps:
    def test_basic(self, monkeypatch):
        chain = _mock_option_chain(
            [_make_call_row(vol=150, last=1.48, ask=1.50, bid=0.80)],
            [],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        sweeps = OptionsFlowDetector().detect_sweeps("SPY", max_dte=1)
        assert isinstance(sweeps, list)
        for s in sweeps:
            assert isinstance(s, SweepAlert)
            assert s.fill_ratio >= AGGRESSIVE_FILL_THRESHOLD
            assert s.volume >= MIN_SWEEP_SIZE

    def test_rejects_low_volume(self, monkeypatch):
        chain = _mock_option_chain(
            [_make_call_row(vol=50, last=1.48, ask=1.50, bid=0.80)],  # < 100
            [],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        sweeps = OptionsFlowDetector().detect_sweeps("SPY", max_dte=1)
        # vol=50 < MIN_SWEEP_SIZE=100 → should be excluded
        assert all(s.volume >= MIN_SWEEP_SIZE for s in sweeps)

    def test_rejects_passive_fill(self, monkeypatch):
        """Last price far from ask → not a sweep."""
        chain = _mock_option_chain(
            [_make_call_row(vol=200, last=0.90, ask=1.50, bid=0.80)],
            [],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        sweeps = OptionsFlowDetector().detect_sweeps("SPY", max_dte=1)
        # fill_ratio = (0.90 - 0.80) / (1.50 - 0.80) = 0.14 < 0.85
        assert len(sweeps) == 0


class TestPutCallFlow:
    def test_basic(self, monkeypatch):
        chain = _mock_option_chain(
            [_make_call_row(vol=300, oi=1000)],
            [_make_put_row(vol=100, oi=800)],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        flow = calculate_put_call_flow("SPY", max_dte=1)
        assert isinstance(flow, PutCallFlow)
        assert flow.call_volume == 300
        assert flow.put_volume == 100
        assert flow.net_premium > 0  # more call premium
        assert flow.put_call_volume_ratio < 1.0

    def test_equal_flow(self, monkeypatch):
        chain = _mock_option_chain(
            [_make_call_row(vol=100, last=1.0, bid=1.0, ask=1.0)],
            [_make_put_row(vol=100, last=1.0, bid=1.0, ask=1.0)],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        flow = calculate_put_call_flow("SPY", max_dte=1)
        assert abs(flow.put_call_volume_ratio - 1.0) < 0.01
        assert abs(flow.net_premium) < 1.0


class TestFlowSentiment:
    def test_returns_valid_sentiment(self, monkeypatch):
        chain = _mock_option_chain(
            [_make_call_row(vol=300, oi=1000)],
            [_make_put_row(vol=100, oi=800)],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        result = get_flow_sentiment("SPY", max_dte=1)
        assert isinstance(result, FlowSentiment)
        assert result.signal in ("bullish", "bearish", "neutral")
        assert result.strength in ("strong", "moderate", "weak")
        assert -1.0 <= result.score <= 1.0
        assert "put_call_premium" in result.components
        assert "volume_skew" in result.components
        assert "sweep_direction" in result.components

    def test_bearish_signal(self, monkeypatch):
        """Heavy put volume should yield bearish signal."""
        chain = _mock_option_chain(
            [_make_call_row(vol=10)],
            [_make_put_row(vol=500, last=2.0)],
        )
        _patch_yfinance(monkeypatch, chain_factory=lambda: chain)
        result = OptionsFlowDetector().get_flow_sentiment("SPY", max_dte=1)
        # With puts dominating, signal should be bearish or at least not bullish
        assert result.signal in ("bearish", "neutral")


class TestDataclassStructure:
    def test_alert_fields(self):
        a = UnusualVolumeAlert(
            symbol="SPY", strike=550, expiry="2025-07-08", is_call=True,
            current_volume=1000, avg_volume=200, volume_ratio=5.0,
            open_interest=5000, last_price=2.0, mid_price=2.05,
            premium_estimate=205000,
        )
        assert a.volume_ratio == 5.0

    def test_sweep_score_ordering(self):
        s1 = SweepAlert(
            symbol="SPY", strike=550, expiry="2025-07-08", is_call=True,
            volume=200, open_interest=1000, ask_price=2.0, bid_price=1.0,
            last_price=1.95, estimated_premium=39000, fill_ratio=0.95,
            sweep_score=3.8,
        )
        s2 = SweepAlert(
            symbol="SPY", strike=550, expiry="2025-07-08", is_call=True,
            volume=100, open_interest=500, ask_price=2.0, bid_price=1.0,
            last_price=1.9, estimated_premium=19000, fill_ratio=0.90,
            sweep_score=1.8,
        )
        assert s1.sweep_score > s2.sweep_score
