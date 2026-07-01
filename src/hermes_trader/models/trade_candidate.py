"""Pydantic models for the TradeCandidate JSON contract.

Every trade idea must pass through this schema before policy evaluation.
Missing fields → validation error → rejection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class OrderDetails(BaseModel):
    side: Literal["buy", "sell"] = "buy"
    order_type: Literal["market", "limit"] = "limit"
    quantity: float = Field(default=0.0, ge=0)
    notional_usd: float = Field(default=0.0, ge=0)
    limit_price: Optional[float] = Field(default=None, ge=0)
    time_in_force: Literal["day"] = "day"


class OptionDetails(BaseModel):
    expiration_date: Optional[str] = None  # YYYY-MM-DD
    days_to_expiration: int = 0
    strike: float = 0.0
    option_type: Optional[Literal["call", "put"]] = None
    bid: float = 0.0
    ask: float = 0.0
    midpoint: float = 0.0
    open_interest: int = 0
    volume: int = 0
    implied_volatility: float = 0.0
    spread_pct: float = 0.0


class RiskDetails(BaseModel):
    max_loss_usd: float = 0.0
    expected_loss_usd: float = 0.0
    max_profit_usd: float = 0.0
    risk_reward_ratio: float = 0.0
    position_notional_usd: float = 0.0


class SourceInfo(BaseModel):
    vibe_trading_run_id: Optional[str] = None
    tradingagents_run_id: Optional[str] = None
    hermes_workflow_id: Optional[str] = None


class EvidencePack(BaseModel):
    market_data_timestamp: str = ""  # ISO-8601
    vibe_summary: str = ""
    tradingagents_summary: str = ""
    bull_case: str = ""
    bear_case: str = ""
    risk_case: str = ""
    backtest_summary: Optional[str] = None
    transaction_cost_assumption: Optional[str] = None
    slippage_assumption: Optional[str] = None
    known_limitations: list[str] = Field(default_factory=list)


class ExitPlan(BaseModel):
    profit_take_rule: str = ""
    stop_loss_rule: str = ""
    time_exit_rule: str = ""
    expiration_exit_rule: str = ""
    emergency_exit_rule: str = ""


class ConfidenceInfo(BaseModel):
    score_0_to_100: int = Field(ge=0, le=100)
    label: Literal["low", "medium", "high"]
    reason: str = ""


class TradeCandidate(BaseModel):
    """The canonical trade proposal JSON contract.

    Every field is required (no Optional). Missing = rejection.
    """
    candidate_id: str
    created_at: str  # ISO-8601
    mode: Literal["RESEARCH_ONLY", "PAPER_AUTONOMOUS", "TINY_LIVE_AUTONOMOUS"]
    source: SourceInfo = Field(default_factory=lambda: SourceInfo())
    asset_class: Literal["equity", "option"]
    underlying: str
    symbol: str
    strategy: Literal["no_trade", "fractional_etf", "long_call", "long_put", "debit_spread_paper"]
    direction: Literal["bullish", "bearish", "neutral"]
    action: Literal["open", "close", "cancel", "no_trade"]
    order: OrderDetails = Field(default_factory=lambda: OrderDetails())
    option_details: OptionDetails = Field(default_factory=lambda: OptionDetails())
    risk: RiskDetails = Field(default_factory=lambda: RiskDetails())
    evidence: EvidencePack = Field(default_factory=lambda: EvidencePack())
    exit_plan: ExitPlan = Field(default_factory=lambda: ExitPlan())
    confidence: ConfidenceInfo

    @model_validator(mode="after")
    def validate_mode_action_consistency(self) -> TradeCandidate:
        """no_trade actions require no_trade strategy and neutral direction."""
        if self.action == "no_trade":
            if self.strategy != "no_trade":
                raise ValueError("action=no_trade requires strategy=no_trade")
            if self.direction != "neutral":
                raise ValueError("action=no_trade requires direction=neutral")
        return self

    @model_validator(mode="after")
    def validate_option_fields(self) -> TradeCandidate:
        """Option trades require option_details populated."""
        if self.strategy in ("long_call", "long_put", "debit_spread_paper"):
            if self.option_details.option_type is None:
                raise ValueError(f"Option strategy '{self.strategy}' requires option_type (call/put)")
            if self.option_details.strike <= 0:
                raise ValueError(f"Option strategy '{self.strategy}' requires strike > 0")
            if not self.option_details.expiration_date:
                raise ValueError(f"Option strategy '{self.strategy}' requires expiration_date")
        return self

    @model_validator(mode="after")
    def validate_open_action_has_exit(self) -> TradeCandidate:
        """Opening trades require an exit plan."""
        if self.action == "open":
            if not any([
                self.exit_plan.profit_take_rule,
                self.exit_plan.stop_loss_rule,
                self.exit_plan.time_exit_rule,
            ]):
                raise ValueError("action=open requires at least one exit rule (profit_take, stop_loss, or time_exit)")
        return self

    def to_log_dict(self) -> dict:
        """Return a redacted dict safe for logging."""
        return self.model_dump(mode="json", exclude={"created_at"})


class CandidateScore(BaseModel):
    """Scores from the scoring system (Section 35)."""
    evidence_score: int = Field(ge=0, le=25)
    committee_score: int = Field(ge=0, le=25)
    liquidity_score: int = Field(ge=0, le=20)
    risk_score: int = Field(ge=0, le=20)
    operational_score: int = Field(ge=0, le=10)

    @property
    def total(self) -> int:
        return self.evidence_score + self.committee_score + self.liquidity_score + self.risk_score + self.operational_score

    @property
    def tier(self) -> str:
        """paper_only, live_eligible, or rejected."""
        if self.total < 70:
            return "rejected"
        elif self.total < 85:
            return "paper_only"
        return "live_eligible"