"""Tests for multi-leg (mleg) order support.

Covers:
- MlegLeg and OrderRequest model validation
- Risk gate margin checks for mleg orders
- Paper broker mleg order journaling
"""

from unittest.mock import patch
from typing import Literal

import pytest

from hermes_trader.config import config
from hermes_trader.models.order_request import OrderRequest, MlegLeg
from hermes_trader.models.trade_candidate import (
    TradeCandidate,
    CandidateScore,
    ConfidenceInfo,
    SourceInfo,
    EvidencePack,
    ExitPlan,
    OrderDetails,
    RiskDetails,
    OptionDetails,
)
from hermes_trader.models.position_snapshot import AccountSnapshot, MarketSnapshot, RiskSnapshot
from hermes_trader.policy.risk_gate import policy_engine
from hermes_trader.integrations.alpaca_broker import PaperBrokerAdapter


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def small_account():
    """$50 account with limited buying power (simulates small retail)."""
    return AccountSnapshot(
        equity=50.0,
        cash=45.0,
        buying_power=100.0,
        portfolio_value=50.0,
        positions=[],
        open_orders_count=0,
    )


@pytest.fixture
def tiny_account():
    """Very small account — buying power barely above margin requirement."""
    return AccountSnapshot(
        equity=50.0,
        cash=48.0,
        buying_power=52.0,
        portfolio_value=50.0,
        positions=[],
        open_orders_count=0,
    )


@pytest.fixture
def empty_risk():
    return RiskSnapshot(
        daily_pnl=0.0,
        weekly_pnl=0.0,
        monthly_pnl=0.0,
        consecutive_losses=0,
        trades_today=0,
        trades_this_week=0,
        daily_loss_budget_remaining=2.0,
        weekly_loss_budget_remaining=5.0,
        monthly_loss_budget_remaining=10.0,
    )


@pytest.fixture
def fresh_market():
    return MarketSnapshot(
        timestamp="2026-07-01T14:30:00Z",
        symbol="SPY",
        last_price=550.0,
        bid=549.95,
        ask=550.05,
        spread_pct=0.018,
        volume=25000000,
        market_open=True,
    )


def _make_mleg_candidate(
    order_class="mleg",
    required_maintenance_margin=None,
    **overrides,
) -> TradeCandidate:
    """Helper to build a valid mleg debit spread candidate."""
    defaults = dict(
        candidate_id="mleg_test_001",
        created_at="2026-07-01T12:00:00Z",
        mode="PAPER_AUTONOMOUS",
        underlying="SPY",
        symbol="SPY",
        asset_class="option",
        strategy="debit_spread_paper",
        order_class=order_class,
        direction="bullish",
        action="open",
        confidence=ConfidenceInfo(score_0_to_100=75, label="medium", reason="Mleg test"),
        source=SourceInfo(),
        evidence=EvidencePack(
            market_data_timestamp="2026-07-01T14:00:00Z",
            vibe_summary="Bullish momentum on SPY with positive macro backdrop.",
            tradingagents_summary="Agents vote 4-1 bullish on SPY short-term outlook.",
            bull_case="Strong momentum, positive macro",
            bear_case="Overbought, potential pullback",
            risk_case="Controlled risk with spread",
        ),
        exit_plan=ExitPlan(
            profit_take_rule="Close at +50% premium",
            stop_loss_rule="Close at -100% premium",
            time_exit_rule="Close by expiration",
        ),
        order=OrderDetails(
            side="buy",
            order_type="limit",
            quantity=1,
            notional_usd=3.00,
            limit_price=1.50,
        ),
        risk=RiskDetails(
            max_loss_usd=1.50,
            expected_loss_usd=0.75,
            max_profit_usd=0.50,
            risk_reward_ratio=0.33,
            position_notional_usd=3.00,
        ),
        option_details=OptionDetails(
            expiration_date="2026-07-18",
            days_to_expiration=17,
            strike=548.0,
            option_type="call",
            bid=1.48,
            ask=1.52,
            midpoint=1.50,
            open_interest=5000,
            volume=1200,
            implied_volatility=0.20,
            spread_pct=2.7,
        ),
        required_maintenance_margin=required_maintenance_margin,
    )
    defaults.update(overrides)
    return TradeCandidate(**defaults)


# ══════════════════════════════════════════════════════════════
# Model Validation — MlegLeg & OrderRequest
# ══════════════════════════════════════════════════════════════

class TestMlegLegModel:
    def test_valid_leg(self):
        leg = MlegLeg(
            symbol="SPY",
            side="buy",
            qty=1,
            position_intent="buy_to_open",
        )
        assert leg.symbol == "SPY"
        assert leg.side == "buy"
        assert leg.position_intent == "buy_to_open"

    def test_leg_with_limit_price(self):
        leg = MlegLeg(
            symbol="SPY",
            side="sell",
            qty=1,
            position_intent="sell_to_open",
            limit_price=2.00,
        )
        assert leg.limit_price == 2.00

    def test_leg_rejects_invalid_position_intent(self):
        with pytest.raises(ValueError):
            MlegLeg(
                symbol="SPY",
                side="buy",
                qty=1,
                position_intent="invalid_intent",
            )

    def test_leg_rejects_negative_qty(self):
        with pytest.raises(ValueError):
            MlegLeg(
                symbol="SPY",
                side="buy",
                qty=-1,
                position_intent="buy_to_open",
            )

    def test_leg_rejects_invalid_side(self):
        with pytest.raises(ValueError):
            MlegLeg(
                symbol="SPY",
                side="hold",
                qty=1,
                position_intent="buy_to_open",
            )


class TestMlegOrderRequest:
    def test_mleg_order_request_with_legs(self):
        legs = [
            MlegLeg(symbol="SPY", side="buy", qty=1, position_intent="buy_to_open"),
            MlegLeg(symbol="SPY", side="sell", qty=1, position_intent="sell_to_open"),
        ]
        order = OrderRequest(
            candidate_id="test_001",
            symbol="SPY",
            side="buy",
            order_type="limit",
            qty=1,
            limit_price=1.50,
            order_class="mleg",
            legs=legs,
            required_maintenance_margin=150.00,
        )
        assert order.order_class == "mleg"
        assert len(order.legs) == 2
        assert order.legs[0].position_intent == "buy_to_open"
        assert order.legs[1].position_intent == "sell_to_open"
        assert order.required_maintenance_margin == 150.00

    def test_mleg_order_defaults_to_no_legs(self):
        order = OrderRequest(
            candidate_id="test_002",
            symbol="SPY",
            side="buy",
            order_type="market",
            qty=1,
        )
        assert order.order_class == "simple"
        assert order.legs == []
        assert order.required_maintenance_margin is None

    def test_mleg_order_accepts_margin_field(self):
        legs = [
            MlegLeg(symbol="SPY", side="buy", qty=1, position_intent="buy_to_open"),
            MlegLeg(symbol="SPY", side="sell", qty=1, position_intent="sell_to_open"),
        ]
        order = OrderRequest(
            candidate_id="test_003",
            symbol="SPY",
            side="buy",
            order_type="limit",
            qty=1,
            limit_price=2.50,
            order_class="mleg",
            legs=legs,
        )
        assert order.required_maintenance_margin is None

    def test_invalid_order_class_rejected(self):
        with pytest.raises(ValueError):
            OrderRequest(
                candidate_id="t",
                symbol="SPY",
                side="buy",
                order_type="limit",
                qty=1,
                order_class="invalid_class",
            )


# ══════════════════════════════════════════════════════════════
# TradeCandidate — Mleg Fields
# ══════════════════════════════════════════════════════════════

class TestTradeCandidateMleg:
    def test_candidate_with_mleg_order_class(self):
        c = _make_mleg_candidate(order_class="mleg", required_maintenance_margin=150.00)
        assert c.order_class == "mleg"
        assert c.required_maintenance_margin == 150.00

    def test_candidate_defaults_to_simple(self):
        c = _make_mleg_candidate(order_class="simple")
        assert c.order_class == "simple"

    def test_candidate_without_margin_field(self):
        c = _make_mleg_candidate()
        assert c.required_maintenance_margin is None


# ══════════════════════════════════════════════════════════════
# Policy Engine — Mleg Margin Checks
# ══════════════════════════════════════════════════════════════

class TestMlegMarginChecks:
    def test_mleg_rejected_when_margin_exceeds_buying_power(
        self, tiny_account, empty_risk, fresh_market
    ):
        """For a $50 account with $52 buying power, a spread needing $150 margin is rejected."""
        # tiny_account.buying_power = 52
        c = _make_mleg_candidate(
            required_maintenance_margin=150.00,
            risk=RiskDetails(
                max_loss_usd=3.00,
                expected_loss_usd=1.50,
                max_profit_usd=2.00,
                risk_reward_ratio=0.67,
                position_notional_usd=5.00,
            ),
        )
        result = policy_engine.evaluate(c, tiny_account, fresh_market, empty_risk)
        assert result.status == "REJECTED"
        assert any("MLEG_MARGIN_INSUFFICIENT" in r for r in result.reasons)

    def test_mleg_approved_when_margin_fits_buying_power(
        self, small_account, empty_risk, fresh_market
    ):
        """For a $50 account with $100 buying power, a spread needing $45 margin is approved."""
        # small_account.buying_power = 100
        c = _make_mleg_candidate(
            required_maintenance_margin=45.00,
            risk=RiskDetails(
                max_loss_usd=1.50,
                expected_loss_usd=0.75,
                max_profit_usd=0.50,
                risk_reward_ratio=0.33,
                position_notional_usd=3.00,
            ),
        )
        score = CandidateScore(
            evidence_score=18, committee_score=18, liquidity_score=12,
            risk_score=12, operational_score=6, technical_score=10,
        )
        result = policy_engine.evaluate(c, small_account, fresh_market, empty_risk, score)
        assert result.status == "APPROVED", f"Expected APPROVED but got {result.status}: {result.reasons}"

    def test_mleg_no_margin_skip_when_none(
        self, tiny_account, empty_risk, fresh_market
    ):
        """When required_maintenance_margin is None, the margin check is skipped."""
        c = _make_mleg_candidate(
            required_maintenance_margin=None,
            risk=RiskDetails(
                max_loss_usd=1.00,
                expected_loss_usd=0.50,
                max_profit_usd=0.50,
                risk_reward_ratio=0.50,
                position_notional_usd=3.00,
            ),
        )
        # Should not fail on MLEG_MARGIN_INSUFFICIENT
        # It may pass or fail on other gates (like BUYING_POWER), but NOT mleg margin
        result = policy_engine.evaluate(c, tiny_account, fresh_market, empty_risk)
        assert not any("MLEG_MARGIN_INSUFFICIENT" in r for r in result.reasons)

    def test_simple_order_skips_mleg_margin_check(
        self, tiny_account, empty_risk, fresh_market
    ):
        """Simple orders (order_class='simple') don't trigger the mleg margin check."""
        c = _make_mleg_candidate(
            order_class="simple",
            required_maintenance_margin=1000.00,  # Huge margin but should be ignored
            risk=RiskDetails(
                max_loss_usd=1.00,
                expected_loss_usd=0.50,
                max_profit_usd=0.50,
                risk_reward_ratio=0.50,
                position_notional_usd=3.00,
            ),
        )
        result = policy_engine.evaluate(c, tiny_account, fresh_market, empty_risk)
        # Should NOT be rejected for mleg margin
        assert not any("MLEG_MARGIN_INSUFFICIENT" in r for r in result.reasons)

    def test_mleg_margin_exact_boundary(
        self, tiny_account, empty_risk, fresh_market
    ):
        """When margin exactly equals buying power, order should be approved (not >)."""
        # tiny_account.buying_power = 52
        c = _make_mleg_candidate(
            required_maintenance_margin=52.00,  # Exactly equal to buying power
            risk=RiskDetails(
                max_loss_usd=1.00,
                expected_loss_usd=0.50,
                max_profit_usd=0.50,
                risk_reward_ratio=0.50,
                position_notional_usd=3.00,
            ),
        )
        result = policy_engine.evaluate(c, tiny_account, fresh_market, empty_risk)
        # Should NOT fail on MLEG_MARGIN (52 is not > 52)
        assert not any("MLEG_MARGIN_INSUFFICIENT" in r for r in result.reasons)

    def test_vertical_spread_margin_formula(
        self, small_account, empty_risk, fresh_market
    ):
        """Verify margin formula: (strike_width * 100) - credit_received.
        For a vertical call spread: buy $548 call, sell $550 call on 1 contract.
        Strike width = $2, so max risk = $2 * 100 = $200 minus credit received.
        With credit of $1.50, margin = ($2 * 100) - $150 = $50.
        """
        # This tests the margin value passed into the candidate, not the
        # calculation itself (that lives in the workflow/strategy layer).
        margin = (2.0 * 100) - 150.0  # = 50.0
        c = _make_mleg_candidate(
            required_maintenance_margin=margin,
            risk=RiskDetails(
                max_loss_usd=50.00,
                expected_loss_usd=25.00,
                max_profit_usd=150.00,
                risk_reward_ratio=3.0,
                position_notional_usd=50.00,
            ),
        )
        score = CandidateScore(
            evidence_score=18, committee_score=18, liquidity_score=12,
            risk_score=12, operational_score=6, technical_score=10,
        )
        result = policy_engine.evaluate(c, small_account, fresh_market, empty_risk, score)
        # $50 margin < $100 buying power → should be approved on margin check
        assert not any("MLEG_MARGIN_INSUFFICIENT" in r for r in result.reasons)


# ══════════════════════════════════════════════════════════════
# Paper Broker — Mleg Order Journaling
# ══════════════════════════════════════════════════════════════

class TestPaperBrokerMleg:
    def test_submit_mleg_order_journals_legs(self):
        broker = PaperBrokerAdapter()
        legs = [
            MlegLeg(symbol="SPY", side="buy", qty=1, position_intent="buy_to_open"),
            MlegLeg(symbol="SPY", side="sell", qty=1, position_intent="sell_to_open"),
        ]
        order = OrderRequest(
            candidate_id="mleg_001",
            symbol="SPY",
            side="buy",
            order_type="limit",
            qty=1,
            limit_price=1.50,
            order_class="mleg",
            legs=legs,
            required_maintenance_margin=45.00,
        )
        result = broker.submit_order(order)
        assert result["order_class"] == "mleg"
        assert result["legs"] is not None
        assert len(result["legs"]) == 2
        assert result["legs"][0]["position_intent"] == "buy_to_open"
        assert result["legs"][1]["position_intent"] == "sell_to_open"
        assert result["required_maintenance_margin"] == 45.00

    def test_submit_simple_order_no_legs(self):
        broker = PaperBrokerAdapter()
        order = OrderRequest(
            candidate_id="simple_001",
            symbol="SPY",
            side="buy",
            order_type="market",
            qty=1,
        )
        result = broker.submit_order(order)
        assert result["order_class"] == "simple"
        assert result["legs"] is None
        assert result["required_maintenance_margin"] is None
