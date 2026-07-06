"""Position monitoring + exit management.

Checks open positions for exit conditions (take profit / stop loss).
When an exit condition triggers, generates a close-order candidate.

Uses the Robinhood MCP broker adapter (agent.robinhood.com/mcp/trading)
for live position data instead of legacy broker.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from ..config import config

logger = logging.getLogger("hermes_trader.monitoring.position")


class PositionMonitor:
    """Monitor open positions and trigger exits."""

    def __init__(self, journal_path: str = "/opt/hermes-trader/data/journals"):
        self.journal_path = Path(journal_path)
        self.positions_file = self.journal_path / "positions.json"

    def get_robinhood_positions(self) -> list[dict]:
        """Fetch live positions from Robinhood MCP API.

        Uses the RobinhoodBrokerAdapter's MCP client to call
        ``get_equity_positions`` on the Robinhood MCP trading endpoint.
        """
        try:
            from ..integrations.robinhood_broker import robinhood_mcp_call, ROBINHOOD_ACCOUNT

            data = robinhood_mcp_call("get_equity_positions", {
                "account_number": ROBINHOOD_ACCOUNT,
            })

            positions = []
            # Handle different response shapes
            if isinstance(data, dict):
                raw = data.get("positions", data.get("results", data.get("holdings", [])))
            elif isinstance(data, list):
                raw = data
            else:
                return []

            for p in raw:
                if not isinstance(p, dict):
                    continue
                symbol = p.get("symbol", p.get("ticker", ""))
                if not symbol:
                    continue

                qty = _safe_float(p, "quantity", "qty", "shares", "net_quantity", default=0.0)
                if qty == 0:
                    continue

                entry_price = _safe_float(p, "average_buy_price", "average_cost", "cost_basis", default=0.0)
                current_price = _safe_float(p, "last_price", "current_price", "price", default=0.0)
                market_value = _safe_float(p, "market_value", "value", "market_value_today", default=0.0)
                if market_value == 0.0 and qty > 0 and current_price > 0:
                    market_value = qty * current_price

                unrealized_pl = _safe_float(p, "unrealized_pl", "unrealized_gain", "unrealized_pnl", default=0.0)
                unrealized_plpc = _safe_float(p, "unrealized_plpc", "unrealized_gain_pct", "unrealized_percent", default=0.0)

                positions.append({
                    "symbol": symbol,
                    "qty": float(qty),
                    "entry_price": float(entry_price),
                    "current_price": float(current_price),
                    "unrealized_pl": float(unrealized_pl),
                    "unrealized_plpc": float(unrealized_plpc),
                    "market_value": float(market_value),
                })
            return positions
        except Exception as e:
            logger.warning(f"Robinhood position fetch failed: {e}")
            return []

    def get_open_positions(self) -> list[dict]:
        """Load current open positions from Robinhood or journal."""
        # Prefer live Robinhood positions
        rh_pos = self.get_robinhood_positions()
        if rh_pos:
            return rh_pos
        # Fallback to local journal
        if not self.positions_file.exists():
            return []
        try:
            with open(self.positions_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def check_exits(self) -> list[dict]:
        """Check each position for exit conditions.

        Returns list of close-order specs ready for TradeCandidate builder.
        """
        positions = self.get_open_positions()
        exits = []

        for pos in positions:
            try:
                exit_spec = self._check_single_position(pos)
                if exit_spec:
                    exits.append(exit_spec)
            except Exception as e:
                logger.error(f"Error checking position {pos.get('symbol')}: {e}")

        return exits

    def _check_single_position(self, pos: dict) -> Optional[dict]:
        """Check one position for exit triggers."""
        symbol = pos.get("symbol", "")
        entry_price = float(pos.get("entry_price", 0))
        current_price = float(pos.get("current_price", entry_price))
        take_profit = float(pos.get("take_profit", 0))
        stop_loss = float(pos.get("stop_loss", 0))
        position_id = pos.get("position_id", "")

        # Check take profit
        if take_profit and current_price >= take_profit:
            logger.info(f"Take profit triggered for {symbol} at {current_price}")
            return {
                "action": "close",
                "underlying": symbol,
                "position_id": position_id,
                "exit_reason": f"profit_take:{current_price}",
            }

        # Check stop loss
        if stop_loss and current_price <= stop_loss:
            logger.info(f"Stop loss triggered for {symbol} at {current_price}")
            return {
                "action": "close",
                "underlying": symbol,
                "position_id": position_id,
                "exit_reason": f"stop_loss:{current_price}",
            }

        return None


def _safe_float(data: dict, *keys: str, default: float = 0.0) -> float:
    """Extract the first matching float from dict keys."""
    if not isinstance(data, dict):
        return default
    for key in keys:
        val = data.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return default
