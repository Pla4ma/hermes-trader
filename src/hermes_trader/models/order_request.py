"""Broker order request model."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    """Validated order ready for broker submission."""
    candidate_id: str
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    qty: float = Field(ge=0)
    notional: Optional[float] = None  # For fractional orders
    limit_price: Optional[float] = None
    time_in_force: Literal["day"] = "day"
    order_class: Literal["simple", "bracket", "oco"] = "simple"
    # Bracket order fields (take profit / stop loss)
    take_profit: Optional[dict] = None
    stop_loss: Optional[dict] = None