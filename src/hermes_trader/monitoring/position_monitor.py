"""Position monitoring + exit management.

Checks open positions for exit conditions (take profit / stop loss).
When an exit condition triggers, generates a close-order candidate.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from ..config import config

logger = logging.getLogger("hermes_trader.monitoring.position")


class PositionMonitor:
    """Monitor open positions and trigger exits."""

    def __init__(self, journal_path: str = "/opt/hermes-trader/data/journals"):
        self.journal_path = Path(journal_path)
        self.positions_file = self.journal_path / "positions.json"

    def get_alpaca_positions(self) -> list[dict]:
        """Fetch live positions from Alpaca API."""
        try:
            import alpaca_trade_api as tradeapi
            api_key = os.getenv("ALPACA_API_KEY", "")
            secret_key = os.getenv("ALPACA_SECRET_KEY", "")
            base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
            if not api_key or not secret_key:
                return []
            api = tradeapi.REST(api_key, secret_key, base_url)
            positions = []
            for p in api.list_positions():
                positions.append({
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc),
                    "market_value": float(p.market_value),
                })
            return positions
        except Exception as e:
            logger.warning(f"Alpaca position fetch failed: {e}")
            return []

    def get_open_positions(self) -> list[dict]:
        """Load current open positions from Alpaca or journal."""
        # Prefer live Alpaca positions
        alpaca_pos = self.get_alpaca_positions()
        if alpaca_pos:
            return alpaca_pos
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