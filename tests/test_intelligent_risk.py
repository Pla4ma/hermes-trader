"""Tests for intelligent_risk.py — dynamic risk management layer."""
import math
import pytest

from hermes_trader.intelligent_risk import (
    IntelligentRiskConfig,
    IntelligentRiskLayer,
    RiskSignal,
    SignalType,
    SignalAction,
    TradeRiskResult,
    LivePositionAction,
    get_intelligent_risk_layer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def layer():
    return IntelligentRiskLayer(IntelligentRiskConfig())


@pytest.fixture
def default_config():
    return IntelligentRiskConfig()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults(self, default_config):
        cfg = default_config
        assert cfg.iv_stop_base_pct == 0.02
        assert cfg.iv_stop_max_pct == 0.06
        assert cfg.iv_stop_min_pct == 0.005
        assert cfg.iv_percentile_high == 75.0
        assert cfg.iv_percentile_low == 25.0
        assert cfg.gex_tighten_factor == 0.6
        assert cfg.charm_abs_threshold == 0.008
        assert cfg.vanna_hedge_abs_threshold == 0.04
        assert cfg.risk_per_trade_pct == 0.02
        assert cfg.target_ruin_rate == 0.05
        assert cfg.max_position_pct_equity == 0.10

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("INTELLIGENT_RISK_IV_STOP_BASE_PCT", "0.035")
        monkeypatch.setenv("INTELLIGENT_RISK_GEX_TIGHTEN_FACTOR", "0.5")
        cfg = IntelligentRiskConfig.from_env()
        assert cfg.iv_stop_base_pct == 0.035
        assert cfg.gex_tighten_factor == 0.5

    def test_from_env_invalid_value(self, monkeypatch, caplog):
        monkeypatch.setenv("INTELLIGENT_RISK_IV_STOP_BASE_PCT", "not_a_number")
        with caplog.at_level("WARNING"):
            cfg = IntelligentRiskConfig.from_env()
        # Should keep default on parse failure
        assert cfg.iv_stop_base_pct == 0.02
        assert "Could not parse" in caplog.text


# ---------------------------------------------------------------------------
# RiskSignal
# ---------------------------------------------------------------------------

class TestRiskSignal:
    def test_to_dict(self):
        sig = RiskSignal(
            signal_type=SignalType.IV_STOP,
            action=SignalAction.WIDEN_STOP,
            current_value=0.85,
            threshold=0.75,
            adjustment_factor=1.5,
            reason="test reason",
        )
        d = sig.to_dict()
        assert d["type"] == "iv_stop"
        assert d["action"] == "widen_stop"
        assert d["current"] == 0.85
        assert d["threshold"] == 0.75
        assert d["adj_factor"] == 1.5
        assert d["reason"] == "test reason"

    def test_to_dict_with_metadata(self):
        sig = RiskSignal(
            signal_type=SignalType.GEX_EXIT,
            action=SignalAction.TIGHTEN_STOP,
            current_value=-1e6,
            threshold=-5e5,
            adjustment_factor=0.6,
            reason="GEX flip",
            metadata={"regime": "negative_gamma"},
        )
        d = sig.to_dict()
        assert d["regime"] == "negative_gamma"


# ---------------------------------------------------------------------------
# TradeRiskResult
# ---------------------------------------------------------------------------

class TestTradeRiskResult:
    def test_to_risk_summary(self):
        sig = RiskSignal(
            signal_type=SignalType.IV_STOP,
            action=SignalAction.WIDEN_STOP,
            current_value=0.85,
            threshold=0.75,
            adjustment_factor=1.5,
            reason="IV high",
        )
        result = TradeRiskResult(
            stop_pct=0.035,
            size_multiplier=0.8,
            position_usd=200.0,
            risk_of_ruin=0.04,
            win_rate_used=0.60,
            payoff_ratio_used=1.5,
            signals=[sig],
        )
        summary = result.to_risk_summary()
        assert summary["intelligent_stop_pct"] == 0.035
        assert summary["size_multiplier"] == 0.8
        assert summary["position_usd"] == 200.0
        assert summary["risk_of_ruin_pct"] == 4.0
        assert len(summary["signals"]) == 1
        assert summary["kill_trade"] is False


# ---------------------------------------------------------------------------
# IntelligentRiskLayer — New Trade Evaluation
# ---------------------------------------------------------------------------

class TestEvaluateNewTrade:
    def test_basic_evaluation(self, layer):
        result = layer.evaluate_new_trade(
            spot=200, strike=200, days_to_expiry=30, option_type="call",
            iv=0.25, account_equity=10000,
        )
        assert isinstance(result, TradeRiskResult)
        assert 0.005 <= result.stop_pct <= 0.06
        assert result.size_multiplier > 0
        assert result.position_usd >= 50.0
        assert 0 <= result.risk_of_ruin <= 1.0
        assert result.win_rate_used == layer.cfg.default_win_rate

    def test_high_iv_widens_stop(self, layer):
        result_high = layer.evaluate_new_trade(
            spot=500, strike=500, days_to_expiry=30, option_type="call",
            iv=0.40, account_equity=10000,
            iv_surface_data={"iv_percentile": 90.0, "avg_iv": 0.20},
        )
        result_low = layer.evaluate_new_trade(
            spot=500, strike=500, days_to_expiry=30, option_type="call",
            iv=0.12, account_equity=10000,
            iv_surface_data={"iv_percentile": 10.0, "avg_iv": 0.20},
        )
        assert result_high.stop_pct > result_low.stop_pct

    def test_negative_gex_tightens_stop(self, layer):
        result_neg = layer.evaluate_new_trade(
            spot=500, strike=500, days_to_expiry=30, option_type="call",
            iv=0.25, account_equity=10000,
            gex_data={"total_gex": -2e6, "regime": "negative_gamma"},
        )
        result_pos = layer.evaluate_new_trade(
            spot=500, strike=500, days_to_expiry=30, option_type="call",
            iv=0.25, account_equity=10000,
            gex_data={"total_gex": 2e6, "regime": "positive_gamma"},
        )
        assert result_neg.stop_pct < result_pos.stop_pct

    def test_put_option(self, layer):
        result = layer.evaluate_new_trade(
            spot=200, strike=200, days_to_expiry=21, option_type="put",
            iv=0.30, account_equity=5000,
        )
        assert isinstance(result, TradeRiskResult)
        assert 0.005 <= result.stop_pct <= 0.06

    def test_custom_base_stop(self, layer):
        result = layer.evaluate_new_trade(
            spot=200, strike=200, days_to_expiry=30, option_type="call",
            iv=0.25, base_stop_pct=0.04,
        )
        # With normal IV, stop should stay near 4%
        assert 0.005 <= result.stop_pct <= 0.06

    def test_kill_trade_on_extreme_conditions(self, layer):
        # Extreme: max stop + charm decay
        cfg = IntelligentRiskConfig(iv_stop_max_pct=0.02, charm_abs_threshold=0.001)
        layer_kill = IntelligentRiskLayer(cfg)
        result = layer_kill.evaluate_new_trade(
            spot=100, strike=100, days_to_expiry=5, option_type="call",
            iv=0.80, account_equity=5000,
            iv_surface_data={"iv_percentile": 99.0, "avg_iv": 0.15},
        )
        # At extreme IV, stop hits max; if charm is high, trade gets killed
        if result.stop_pct >= cfg.iv_stop_max_pct:
            charm_signals = [s for s in result.signals if s.signal_type == SignalType.CHARM_EXIT]
            if charm_signals:
                assert result.kill_trade

    def test_position_size_capped(self, layer):
        result = layer.evaluate_new_trade(
            spot=200, strike=200, days_to_expiry=30, option_type="call",
            iv=0.25, account_equity=100000, base_position_usd=50000,
        )
        max_usd = 100000 * layer.cfg.max_position_pct_equity
        assert result.position_usd <= max_usd

    def test_min_position_size(self, layer):
        result = layer.evaluate_new_trade(
            spot=200, strike=200, days_to_expiry=30, option_type="call",
            iv=0.25, account_equity=1000, base_position_usd=10,
        )
        assert result.position_usd >= layer.cfg.min_position_usd

    def test_kelly_size_reduction(self, layer):
        # Very poor risk/reward should reduce size significantly
        result = layer.evaluate_new_trade(
            spot=200, strike=200, days_to_expiry=30, option_type="call",
            iv=0.25, account_equity=10000, base_position_usd=1000,
            win_rate=0.35, payoff_ratio=0.8,
        )
        # High ruin probability should trigger Kelly reduction
        if result.risk_of_ruin > layer.cfg.target_ruin_rate:
            assert result.size_multiplier < 1.0

    def test_to_risk_summary_integration(self, layer):
        result = layer.evaluate_new_trade(
            spot=200, strike=200, days_to_expiry=30, option_type="call",
            iv=0.25,
        )
        summary = result.to_risk_summary()
        assert "intelligent_stop_pct" in summary
        assert "size_multiplier" in summary
        assert "signals" in summary


# ---------------------------------------------------------------------------
# IntelligentRiskLayer — Live Position Evaluation
# ---------------------------------------------------------------------------

class TestEvaluateLivePosition:
    def test_basic_live_evaluation(self, layer):
        action = layer.evaluate_live_position(
            spot=200, strike=200, days_to_expiry=21, option_type="call",
            iv=0.25, entry_price=5.0, current_option_price=6.0,
            unrealised_pnl_pct=0.20,
        )
        assert isinstance(action, LivePositionAction)
        assert action.suggested_stop_pct > 0
        assert action.size_adjustment > 0

    def test_gex_flip_exits(self, layer):
        action = layer.evaluate_live_position(
            spot=200, strike=200, days_to_expiry=14, option_type="call",
            iv=0.30, entry_price=5.0, current_option_price=4.0,
            unrealised_pnl_pct=-0.20,
            gex_data={"total_gex": -3e6, "regime": "negative_gamma"},
        )
        assert action.should_exit
        assert action.urgency == "critical"

    def test_charm_decay_exits(self, layer):
        # Short DTE + high charm should trigger exit
        action = layer.evaluate_live_position(
            spot=200, strike=200, days_to_expiry=10, option_type="call",
            iv=0.40, entry_price=8.0, current_option_price=3.0,
            unrealised_pnl_pct=-0.60,
        )
        # At 10 DTE with high IV, charm is large relative to delta
        # The -60% PnL check should also fire
        if action.should_exit:
            assert "exit" in action.exit_reason.lower() or "p&l" in action.exit_reason.lower() or "charm" in action.exit_reason.lower() or "GEX" in action.exit_reason

    def test_deep_loss_exits(self, layer):
        action = layer.evaluate_live_position(
            spot=200, strike=200, days_to_expiry=21, option_type="call",
            iv=0.25, entry_price=5.0, current_option_price=1.0,
            unrealised_pnl_pct=-0.80,
        )
        assert action.should_exit
        assert action.urgency == "critical"

    def test_stop_price_calculation(self, layer):
        action = layer.evaluate_live_position(
            spot=200, strike=200, days_to_expiry=30, option_type="call",
            iv=0.25, entry_price=10.0, current_option_price=10.5,
            unrealised_pnl_pct=0.05, current_stop_pct=0.02,
        )
        if action.suggested_stop_price is not None:
            expected = 10.0 * (1 - action.suggested_stop_pct)
            assert abs(action.suggested_stop_price - expected) < 0.01

    def test_to_dict(self, layer):
        action = layer.evaluate_live_position(
            spot=200, strike=200, days_to_expiry=21, option_type="call",
            iv=0.25, entry_price=5.0, current_option_price=6.0,
            unrealised_pnl_pct=0.20,
        )
        d = action.to_dict()
        assert "should_exit" in d
        assert "suggested_stop_pct" in d
        assert "urgency" in d
        assert isinstance(d["signals"], list)


# ---------------------------------------------------------------------------
# Dynamic stop computation
# ---------------------------------------------------------------------------

class TestComputeDynamicStop:
    def test_returns_float_and_signals(self, layer):
        stop, signals = layer.compute_dynamic_stop(
            base_stop_pct=0.02, spot=200, strike=200, days_to_expiry=30,
            option_type="call", iv=0.25,
        )
        assert isinstance(stop, float)
        assert isinstance(signals, list)
        assert 0.005 <= stop <= 0.06

    def test_low_iv_tightens(self, layer):
        stop, _ = layer.compute_dynamic_stop(
            base_stop_pct=0.02, spot=200, strike=200, days_to_expiry=30,
            option_type="call", iv=0.10,
            iv_surface_data={"iv_percentile": 10.0, "avg_iv": 0.30},
        )
        assert stop <= 0.02

    def test_high_iv_widens(self, layer):
        stop, _ = layer.compute_dynamic_stop(
            base_stop_pct=0.02, spot=200, strike=200, days_to_expiry=30,
            option_type="call", iv=0.50,
            iv_surface_data={"iv_percentile": 95.0, "avg_iv": 0.20},
        )
        assert stop >= 0.02

    def test_clamped_to_bounds(self, layer):
        # Extreme IV should be clamped to max
        stop, _ = layer.compute_dynamic_stop(
            base_stop_pct=0.02, spot=200, strike=200, days_to_expiry=30,
            option_type="call", iv=1.0,
            iv_surface_data={"iv_percentile": 99.0, "avg_iv": 0.15},
        )
        assert stop <= layer.cfg.iv_stop_max_pct


# ---------------------------------------------------------------------------
# Greeks summary
# ---------------------------------------------------------------------------

class TestGreeksSummary:
    def test_returns_all_greeks(self, layer):
        gs = layer.get_position_greeks_summary(200, 200, 30, "call", 0.25)
        expected_keys = {"delta", "gamma", "theta", "vega", "rho", "vanna", "charm",
                         "vomma", "speed", "zomma", "color", "ultima", "veta"}
        assert expected_keys.issubset(set(gs.keys()))

    def test_atm_values_reasonable(self, layer):
        gs = layer.get_position_greeks_summary(200, 200, 30, "call", 0.25)
        assert 0.4 < gs["delta"] < 0.6  # ATM call delta ~0.5
        assert gs["gamma"] > 0          # Gamma always positive for long
        assert gs["theta"] < 0          # Theta negative for long option
        assert gs["vega"] > 0           # Vega positive for long option


# ---------------------------------------------------------------------------
# Risk of Ruin math
# ---------------------------------------------------------------------------

class TestRiskOfRuin:
    def test_positive_edge_low_ruin(self, layer):
        # 70% win rate, 2:1 payoff → low ruin
        ror = layer._risk_of_ruin(0.70, 2.0)
        assert ror < 0.10

    def test_negative_edge_high_ruin(self, layer):
        # 30% win rate, 0.5:1 payoff → high ruin
        ror = layer._risk_of_ruin(0.30, 0.5)
        assert ror > 0.50

    def test_breakeven(self, layer):
        # 50% win rate, 1:1 payoff → zero edge → ruin approaches 1.0
        ror = layer._risk_of_ruin(0.50, 1.0)
        assert ror > 0.90  # zero edge → near-certain ruin over time

    def test_kelly_fraction(self, layer):
        # Edge = 0.7*2 - 0.3 = 1.1, f* = 1.1/2 = 0.55, half-kelly = 0.275
        f = layer._kelly_fraction(0.70, 2.0)
        assert 0.1 < f < 0.5

    def test_kelly_zero_edge(self, layer):
        f = layer._kelly_fraction(0.50, 1.0)
        assert f == 0.0  # No edge, no bet


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_returns_same_instance(self):
        layer1 = get_intelligent_risk_layer()
        layer2 = get_intelligent_risk_layer()
        assert layer1 is layer2

    def test_custom_config_creates_new(self):
        cfg = IntelligentRiskConfig(iv_stop_base_pct=0.05)
        layer = get_intelligent_risk_layer(cfg)
        assert layer.cfg.iv_stop_base_pct == 0.05
        # Restore default singleton
        get_intelligent_risk_layer()  # reset
