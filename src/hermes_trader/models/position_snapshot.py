"""Account and position snapshot models."""

from typing import Optional

from pydantic import BaseModel, Field


class PositionSnapshot(BaseModel):
    symbol: str
    qty: float
    market_value: float
    cost_basis: float
    unrealized_pl: float
    unrealized_plpc: float
    side: str = "long"
    asset_class: str = "equity"
    entry_price: Optional[float] = None
    entry_time: Optional[str] = None


class AccountSnapshot(BaseModel):
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    daytrade_count: int = 0
    pattern_day_trader: bool = False
    positions: list[PositionSnapshot] = Field(default_factory=list)
    open_orders_count: int = 0


class MarketSnapshot(BaseModel):
    timestamp: str
    symbol: str
    last_price: float
    bid: float
    ask: float
    spread_pct: float = 0.0
    volume: int = 0
    market_open: bool = False


class RiskSnapshot(BaseModel):
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    monthly_pnl: float = 0.0
    consecutive_losses: int = 0
    trades_today: int = 0
    trades_this_week: int = 0
    daily_loss_budget_remaining: float = 0.0
    weekly_loss_budget_remaining: float = 0.0
    monthly_loss_budget_remaining: float = 0.0