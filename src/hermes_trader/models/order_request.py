"""Broker order request model."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MlegLeg(BaseModel):
    """A single leg in a multi-leg (mleg) order.

    Each leg specifies its own symbol, side, qty, and position intent
    (buy_to_open, sell_to_close, etc.) for Robinhood's mleg API.
    """
    symbol: str
    side: Literal["buy", "sell"]
    qty: float = Field(ge=0)
    position_intent: Literal[
        "buy_to_open",
        "buy_to_close",
        "sell_to_open",
        "sell_to_close",
    ]
    limit_price: Optional[float] = None


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
    order_class: Literal["simple", "bracket", "oco", "mleg"] = "simple"
    # Bracket order fields (take profit / stop loss)
    take_profit: Optional[dict] = None
    stop_loss: Optional[dict] = None
    # Multi-leg order fields
    legs: list[MlegLeg] = Field(default_factory=list)
    required_maintenance_margin: Optional[float] = None
