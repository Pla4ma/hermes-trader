"""Policy engine output models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PolicyResult(BaseModel):
    """Output of the deterministic policy engine."""
    status: Literal["APPROVED", "REJECTED", "PAUSED", "NO_TRADE"]
    reasons: list[str] = Field(default_factory=list)
    allowed_action: Literal["none", "paper_order", "live_order", "close_order", "cancel_order"]
    risk_summary: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def is_approved(self) -> bool:
        return self.status == "APPROVED"

    @property
    def can_execute(self) -> bool:
        return self.allowed_action != "none"


class DecisionLog(BaseModel):
    """Logged decision entry for decision_journal.jsonl."""
    timestamp: str
    candidate_id: str
    policy_status: str
    policy_reasons: list[str]
    mode: str
    strategy: str
    underlying: str
    score_total: int
    score_tier: str
    kill_switch_active: bool
    notes: str = ""