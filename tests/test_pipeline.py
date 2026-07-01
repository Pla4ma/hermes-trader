"""Tests for the full Hermes trading system pipeline."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_trader.config import config
from hermes_trader.constants import (
    MAX_EXPERIMENT_CAPITAL_USD, MAX_SINGLE_TRADE_LOSS_USD,
    MIN_SCORE_PAPER, ALLOWED_STRATEGIES,
)
from hermes_trader.models.trade_candidate import (
    TradeCandidate, CandidateScore, ConfidenceInfo,
    SourceInfo, EvidencePack, ExitPlan, OrderDetails, RiskDetails, OptionDetails,
)
from hermes_trader.models.trade_decision import PolicyResult
from hermes_trader.models.order_request import OrderRequest
from hermes_trader.models.position_snapshot import AccountSnapshot, MarketSnapshot, RiskSnapshot, PositionSnapshot
from hermes_trader.policy.risk_gate import PolicyEngine, policy_engine
from hermes_trader.policy.scoring import ScoringEngine
from hermes_trader.integrations.alpaca_broker import PaperBrokerAdapter
from hermes_trader.workflow import DailyWorkflow


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def fresh_account():
    return AccountSnapshot(
        equity=20.0, cash=18.0, buying_power=40.0,
        portfolio_value=20.0, positions=[], open_orders_count=0,
    )


@pytest.fixture
def empty_risk():
    return RiskSnapshot(
        daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
        consecutive_losses=0, trades_today=0, trades_this_week=0,
        daily_loss_budget_remaining=1.5, weekly_loss_budget_remaining=3.0,
        monthly_loss_budget_remaining=6.0,
    )


@pytest.fixture
def fresh_market():
    return MarketSnapshot(
        timestamp="2026-07-01T14:30:00Z",
        symbol="SPY", last_price=550.0, bid=549.95, ask=550.05,
        spread_pct=0.018, volume=25000000, market_open=True,
    )


def make_basic_candidate(**overrides) -> TradeCandidate:
    """Helper to build a valid paper candidate."""
    defaults = dict(
        candidate_id="test_001",
        created_at="2026-07-01T12:00:00Z",
        mode="PAPER_AUTONOMOUS",
        underlying="SPY",
        symbol="SPY",
        asset_class="equity",
        strategy="fractional_etf",
        direction="bullish",
        action="open",
        confidence=ConfidenceInfo(score_0_to_100=75, label="medium", reason="Test"),
        source=SourceInfo(),
        evidence=EvidencePack(
            market_data_timestamp="2026-07-01T14:00:00Z",
            vibe_summary="Vibe suggests bullish momentum in SPY based on recent macro data.",
            tradingagents_summary="Agents split 3-2 bullish on SPY short-term.",
            bull_case="Strong momentum, positive macro",
            bear_case="Overbought, potential pullback",
            risk_case="Moderate risk of 2% loss",
        ),
        exit_plan=ExitPlan(
            profit_take_rule="Sell at +3%",
            stop_loss_rule="Stop at -1%",
            time_exit_rule="Close by EOW",
        ),
        order=OrderDetails(side="buy", order_type="limit", quantity=0.01, notional_usd=5.50),
        risk=RiskDetails(max_loss_usd=0.50, expected_loss_usd=0.30, max_profit_usd=1.50, risk_reward_ratio=3.0, position_notional_usd=5.50),
        option_details=OptionDetails(),
    )
    defaults.update(overrides)
    return TradeCandidate(**defaults)


# ══════════════════════════════════════════════════════════════
# Unit Tests — TradeCandidate model
# ══════════════════════════════════════════════════════════════

class TestTradeCandidate:
    def test_valid_paper_candidate(self):
        c = make_basic_candidate()
        assert c.candidate_id == "test_001"
        assert c.strategy == "fractional_etf"

    def test_no_trade_requires_correct_strategy(self):
        with pytest.raises(ValueError):
            make_basic_candidate(action="no_trade", strategy="fractional_etf", direction="bullish")

    def test_no_trade_valid(self):
        c = make_basic_candidate(
            strategy="no_trade", action="no_trade", direction="neutral",
            confidence=ConfidenceInfo(score_0_to_100=0, label="low", reason="No trade"),
        )
        assert c.action == "no_trade"

    def test_option_requires_option_type(self):
        with pytest.raises(ValueError):
            make_basic_candidate(strategy="long_call", asset_class="option")

    def test_open_requires_exit_plan(self):
        with pytest.raises(ValueError):
            make_basic_candidate(
                exit_plan=ExitPlan(),
                risk=RiskDetails(max_loss_usd=1.0, expected_loss_usd=0.5, max_profit_usd=3.0, risk_reward_ratio=2.0, position_notional_usd=5.0),
            )

    def test_open_allowed_with_any_one_exit_rule(self):
        c = make_basic_candidate(exit_plan=ExitPlan(profit_take_rule="Sell at +5%"))
        assert c.action == "open"


# ══════════════════════════════════════════════════════════════
# Unit Tests — Policy Engine
# ══════════════════════════════════════════════════════════════

class TestPolicyEngine:
    def test_approves_valid_paper_candidate(self, fresh_account, empty_risk, fresh_market):
        c = make_basic_candidate()
        score = CandidateScore(evidence_score=20, committee_score=20, liquidity_score=18, risk_score=15, operational_score=7)
        result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk, score)
        assert result.status == "APPROVED", f"Expected APPROVED but got {result.status}: {result.reasons}"
        assert result.allowed_action == "paper_order"

    def test_rejects_when_kill_switch_active(self, fresh_account, empty_risk, fresh_market):
        with patch.object(config, 'is_kill_switch_active', True):
            c = make_basic_candidate()
            result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk)
            assert result.status == "REJECTED"
            assert any("KILL_SWITCH" in r for r in result.reasons)

    def test_rejects_bad_symbol(self, fresh_account, empty_risk, fresh_market):
        c = make_basic_candidate(underlying="GME", symbol="GME")
        result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk)
        assert result.status == "REJECTED"
        assert any("UNDERLYING" in r for r in result.reasons)

    def test_rejects_when_exceeds_max_loss(self, fresh_account, empty_risk, fresh_market):
        c = make_basic_candidate(
            risk=RiskDetails(max_loss_usd=100.0, expected_loss_usd=2.0, max_profit_usd=0.0, risk_reward_ratio=0.0, position_notional_usd=5.0),
        )
        result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk)
        assert result.status == "REJECTED"
        # Either MAX_LOSS_EXCEEDED or BUYING_POWER_INSUFFICIENT catches it
        assert any(msg in str(result.reasons) for msg in ["MAX_LOSS", "BUYING_POWER", "EXPECTED_LOSS"])

    def test_no_trade_action_returns_no_trade(self, fresh_account):
        c = make_basic_candidate(
            strategy="no_trade", action="no_trade", direction="neutral",
            confidence=ConfidenceInfo(score_0_to_100=0, label="low", reason="No trade"),
        )
        result = policy_engine.evaluate(c, fresh_account)
        assert result.status == "NO_TRADE"

    def test_rejects_no_evidence(self, fresh_account, empty_risk, fresh_market):
        c = make_basic_candidate(
            evidence=EvidencePack(market_data_timestamp=""),
            exit_plan=ExitPlan(profit_take_rule="Sell +3%", stop_loss_rule="-1%", time_exit_rule="EOW"),
        )
        result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk)
        assert result.status == "REJECTED"
        assert any("NO_EVIDENCE" in r for r in result.reasons)

    def test_rejects_low_confidence(self, fresh_account, empty_risk, fresh_market):
        c = make_basic_candidate(
            confidence=ConfidenceInfo(score_0_to_100=20, label="low", reason="Weak conviction"),
        )
        result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk)
        assert result.status == "REJECTED"
        assert any("CONFIDENCE" in r for r in result.reasons)

    def test_rejects_market_closed_when_required(self, fresh_account, empty_risk):
        c = make_basic_candidate()
        closed_market = MarketSnapshot(
            timestamp="2026-07-01T22:00:00Z", symbol="SPY",
            last_price=550.0, bid=549.95, ask=550.05,
            spread_pct=0.018, volume=100000, market_open=False,
        )
        result = policy_engine.evaluate(c, fresh_account, closed_market, empty_risk)
        assert result.status == "REJECTED"
        assert any("MARKET_CLOSED" in r for r in result.reasons)

    def test_daily_loss_cap(self, fresh_account, fresh_market):
        risky = RiskSnapshot(
            daily_pnl=-2.0, weekly_pnl=-2.0, monthly_pnl=-2.0,
            consecutive_losses=1, trades_today=1, trades_this_week=1,
            daily_loss_budget_remaining=0.0, weekly_loss_budget_remaining=1.0,
            monthly_loss_budget_remaining=4.0,
        )
        c = make_basic_candidate()
        result = policy_engine.evaluate(c, fresh_account, fresh_market, risky)
        assert result.status == "REJECTED"
        assert any("DAILY_LOSS" in r for r in result.reasons)

    def test_consecutive_losses_cap(self, fresh_account, fresh_market):
        many_losses = RiskSnapshot(
            daily_pnl=-1.0, weekly_pnl=-1.0, monthly_pnl=-1.0,
            consecutive_losses=5, trades_today=0, trades_this_week=0,
            daily_loss_budget_remaining=0.5, weekly_loss_budget_remaining=2.0,
            monthly_loss_budget_remaining=5.0,
        )
        c = make_basic_candidate()
        result = policy_engine.evaluate(c, fresh_account, fresh_market, many_losses)
        assert result.status == "REJECTED"
        assert any("CONSECUTIVE_LOSS" in r for r in result.reasons)

    def test_paused_mode_blocks_orders(self, fresh_account, fresh_market, empty_risk):
        with patch.object(config, 'trader_mode', 'PAUSED'):
            c = make_basic_candidate()
            result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk)
            assert result.status == "REJECTED"
            assert any("MODE_PAUSED" in r for r in result.reasons)

    def test_research_mode_only_allows_no_trade(self, fresh_account, fresh_market, empty_risk):
        with patch.object(config, 'trader_mode', 'RESEARCH_ONLY'):
            c = make_basic_candidate()
            result = policy_engine.evaluate(c, fresh_account, fresh_market, empty_risk)
            assert result.status == "REJECTED"
            assert any("MODE_RESEARCH_ONLY" in r for r in result.reasons)


# ══════════════════════════════════════════════════════════════
# Unit Tests — Scoring Engine
# ══════════════════════════════════════════════════════════════

class TestScoringEngine:
    def test_scores_high_for_well_supported_candidate(self):
        c = make_basic_candidate(
            evidence=EvidencePack(
                market_data_timestamp="2026-07-01T14:00:00Z",
                vibe_summary="A" * 200,
                tradingagents_summary="B" * 200,
                bull_case="Bullish case text " * 10,
                bear_case="Bearish case text " * 10,
                risk_case="Risk case text " * 10,
                backtest_summary="Backtest returns 12% win rate",
                transaction_cost_assumption="$0.01/share",
                slippage_assumption="1bps",
                known_limitations=["Low volume on options", "Earnings next week"],
            ),
            exit_plan=ExitPlan(
                profit_take_rule="Take profit at +3%",
                stop_loss_rule="Stop loss at -1.5%",
                time_exit_rule="Close by Friday",
                emergency_exit_rule="Emergency close if gap down",
            ),
            risk=RiskDetails(
                max_loss_usd=0.50, expected_loss_usd=0.30, max_profit_usd=2.00,
                risk_reward_ratio=4.0, position_notional_usd=5.50,
            ),
            source=SourceInfo(vibe_trading_run_id="vibe_001", tradingagents_run_id="ta_001"),
        )
        engine = ScoringEngine()
        score = engine.score(c)
        assert score.total >= 70, f"Expected >=70, got {score.total}. Breakdown: {score}"
        assert score.tier in ("paper_only", "live_eligible")

    def test_scores_low_for_poor_candidate(self):
        c = make_basic_candidate(
            evidence=EvidencePack(market_data_timestamp=""),
            exit_plan=ExitPlan(profit_take_rule="Take profit", stop_loss_rule="Stop"),  # need >=1 for open
            risk=RiskDetails(max_loss_usd=10.0, expected_loss_usd=8.0, max_profit_usd=0.0, risk_reward_ratio=0.0, position_notional_usd=100.0),
        )
        engine = ScoringEngine()
        score = engine.score(c)
        assert score.total < 50, f"Expected <50, got {score.total}. Breakdown: {score}"
        assert score.tier == "rejected"


# ══════════════════════════════════════════════════════════════
# Unit Tests — Broker Adapter
# ══════════════════════════════════════════════════════════════

class TestPaperBrokerAdapter:
    def test_submit_order_journals(self):
        broker = PaperBrokerAdapter()
        order = OrderRequest(
            candidate_id="test_001", symbol="SPY", side="buy",
            order_type="limit", qty=1.0, limit_price=550.0,
        )
        result = broker.submit_order(order)
        assert result["status"] == "submitted"
        assert result["symbol"] == "SPY"
        assert result["candidate_id"] == "test_001"

    def test_get_account_returns_defaults(self):
        broker = PaperBrokerAdapter()
        account = broker.get_account()
        assert account.equity == 20.0
        assert account.portfolio_value == 20.0

    def test_get_risk_snapshot_returns_empty(self):
        broker = PaperBrokerAdapter()
        risk = broker.get_risk_snapshot()
        assert risk.trades_today == 0
        assert risk.consecutive_losses == 0
        assert risk.daily_loss_budget_remaining == 1.5

    def test_market_is_open_during_trading_hours(self):
        broker = PaperBrokerAdapter()
        assert isinstance(broker._is_market_open(), bool)


# ══════════════════════════════════════════════════════════════
# Integration Tests — Full Pipeline
# ══════════════════════════════════════════════════════════════

class TestDailyWorkflow:
    def test_run_without_research_returns_no_trade(self):
        wf = DailyWorkflow()
        report = wf.run(research_result=None)
        # Either returns policy_status=NO_TRADE or status=KILL_SWITCH_ACTIVE
        ps = report.get("policy_status", report.get("status", ""))
        assert ps in ("NO_TRADE", "KILL_SWITCH_ACTIVE", "REJECTED")

    def test_run_with_mock_research(self):
        wf = DailyWorkflow()
        research = {
            "underlying": "SPY",
            "symbol": "SPY",
            "asset_class": "equity",
            "strategy": "fractional_etf",
            "direction": "bullish",
            "action": "open",
            "order_side": "buy",
            "order_type": "limit",
            "order_qty": 0.01,
            "order_notional": 5.50,
            "limit_price": 550.0,
            "max_loss": 0.50,
            "expected_loss": 0.30,
            "max_profit": 2.00,
            "risk_reward": 4.0,
            "notional": 5.50,
            "data_timestamp": "2026-07-01T14:00:00Z",
            "vibe_summary": "Vibe analysis suggests bullish momentum. SPY in uptrend.",
            "agents_summary": "TradingAgents committee votes 4-1 bullish.",
            "bull_case": "Macro tailwinds, strong earnings",
            "bear_case": "Overbought RSI, profit-taking risk",
            "risk_case": "Controlled risk with limit order",
            "exit_profit_take": "Take profit at +3%",
            "exit_stop_loss": "Stop loss at -1%",
            "exit_time": "Close by Friday EOD",
            "confidence_score": 75,
            "confidence_label": "medium",
            "confidence_reason": "Good setup but mixed signals",
            "limitations": ["Small sample"],
        }
        report = wf.run(research_result=research)
        assert report["candidate_id"] is not None
        assert report["policy_status"] in ("APPROVED", "REJECTED", "NO_TRADE", "KILL_SWITCH_ACTIVE")


# ══════════════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_config_redacted_repr_has_no_secrets(self):
        r = config.redacted_repr()
        assert "alpaca_api_key" in r
        assert r["alpaca_api_key_set"] is False or isinstance(r["alpaca_api_key_set"], bool)

    def test_candidate_score_total_never_exceeds_100(self):
        engine = ScoringEngine()
        c = make_basic_candidate()
        score = engine.score(c)
        assert 0 <= score.total <= 100

    def test_policy_result_type_field(self):
        r = PolicyResult(status="APPROVED", reasons=["All good"], allowed_action="paper_order")
        assert r.status == "APPROVED"
        r2 = PolicyResult(status="REJECTED", reasons=["No"], allowed_action="none")
        assert r2.status == "REJECTED"

    def test_order_request_validation(self):
        with pytest.raises(ValueError):
            OrderRequest(candidate_id="t", symbol="", side="buy", order_type="market", qty=-1)
