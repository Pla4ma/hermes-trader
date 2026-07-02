"""Position monitoring + exit management.

Checks open positions for exit conditions (take profit / stop loss).
When an exit condition triggers, generates a close-order candidate.
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

    def get_open_positions(self) -> list[dict]:
        """Load current open positions from journal."""
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