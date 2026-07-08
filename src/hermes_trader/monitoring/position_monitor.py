"""Position monitoring + exit management.

Checks open positions for exit conditions using smart exit rules.
When an exit condition triggers, generates a close-order candidate.

Uses the Robinhood MCP broker adapter (agent.robinhood.com/mcp/trading)
for live position data instead of legacy broker.

Smart exits replace basic TP/SL with 7 exit rules from smart_exits.py:
  1. Time decay — 0DTE force-close by 3:30 PM ET
  2. Profit targets — sell into strength
  3. VWAP-based exits
  4. Momentum fade — cut losers fast
  5. Quick scalp — don't be greedy
  6. Stop loss — hard cut
  7. RSI overbought/oversold reversal
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from ..config import config

logger = logging.getLogger("hermes_trader.monitoring.position")

ET = ZoneInfo("America/New_York")


class PositionMonitor:
    """Monitor open positions and trigger exits using smart exit rules."""

    def __init__(self, journal_path: str = "/opt/hermes-trader/data/journals"):
        self.journal_path = Path(journal_path)
        self.positions_file = self.journal_path / "positions.json"

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Exit checking — uses smart_exits.calculate_smart_exit()
    # ------------------------------------------------------------------

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
        """Check one position using smart exit rules."""
        symbol = pos.get("symbol", "")
        entry_price = float(pos.get("entry_price", 0))
        current_price = float(pos.get("current_price", entry_price))
        qty = float(pos.get("qty", 0))
        position_id = pos.get("position_id", "")

        # ── 0DTE time-based exit: force close by 3:30 PM ET ────────────
        now = datetime.now(ET)
        minutes_to_close = (16 * 60) - (now.hour * 60 + now.minute)

        if minutes_to_close <= 30 and current_price > 0:
            # Less than 30 min to close — force liquidation
            rounded_qty = _round_contracts(qty)
            if rounded_qty > 0:
                logger.warning(
                    f"0DTE force-close {symbol}: {minutes_to_close} min to close"
                )
                return {
                    "action": "close",
                    "underlying": symbol,
                    "position_id": position_id,
                    "quantity": rounded_qty,
                    "exit_reason": f"0dte_time_exit:{minutes_to_close}min_to_close",
                }

        # ── Entry time — parse from position data or fall back to now ───
        entry_time = _parse_entry_time(pos)

        # ── Call smart exit engine ─────────────────────────────────────
        try:
            from ..smart_exits import calculate_smart_exit

            smart = calculate_smart_exit(
                entry_price=entry_price,
                current_price=current_price,
                spot=current_price,       # use option price as proxy
                strike=float(pos.get("strike", entry_price)),
                option_type=pos.get("option_type", pos.get("type", "call")),
                entry_time=entry_time,
                vwap=pos.get("vwap"),
                rsi=pos.get("rsi"),
                iv=pos.get("iv"),
            )

            action = smart.get("action", "hold")
            urgency = smart.get("urgency", "low")

            if action == "sell_all":
                rounded_qty = _round_contracts(qty)
                if rounded_qty > 0:
                    logger.info(
                        f"Smart exit SELL ALL {symbol}: {smart.get('reason', '')} "
                        f"[urgency={urgency}]"
                    )
                    return {
                        "action": "close",
                        "underlying": symbol,
                        "position_id": position_id,
                        "quantity": rounded_qty,
                        "exit_reason": f"smart_exit:{smart.get('reason', '')}",
                        "urgency": urgency,
                    }

            if action == "sell_half":
                sell_qty = _round_contracts(qty * 0.5)
                if sell_qty > 0:
                    logger.info(
                        f"Smart exit SELL HALF {symbol}: {smart.get('reason', '')} "
                        f"[urgency={urgency}]"
                    )
                    return {
                        "action": "close",
                        "underlying": symbol,
                        "position_id": position_id,
                        "quantity": sell_qty,
                        "exit_reason": f"smart_exit:{smart.get('reason', '')}",
                        "urgency": urgency,
                    }

            if action == "tighten_stop":
                # Not a direct close — update the stop price in place
                logger.info(
                    f"Smart exit TIGHTEN STOP {symbol}: {smart.get('reason', '')}"
                )
                # Return a hint but don't trigger a close order yet
                return None

            # action == "hold" — no exit needed
            return None

        except ImportError:
            logger.debug("smart_exits module not available, falling back to basic TP/SL")
            return self._basic_tp_sl(pos)

    # ------------------------------------------------------------------
    # Fallback: basic take-profit / stop-loss
    # ------------------------------------------------------------------

    def _basic_tp_sl(self, pos: dict) -> Optional[dict]:
        """Fallback exit check with basic TP/SL when smart_exits unavailable."""
        symbol = pos.get("symbol", "")
        entry_price = float(pos.get("entry_price", 0))
        current_price = float(pos.get("current_price", entry_price))
        take_profit = float(pos.get("take_profit", 0))
        stop_loss = float(pos.get("stop_loss", 0))
        position_id = pos.get("position_id", "")
        qty = float(pos.get("qty", 0))

        # Check take profit
        if take_profit and current_price >= take_profit:
            logger.info(f"Take profit triggered for {symbol} at {current_price}")
            rounded_qty = _round_contracts(qty)
            return {
                "action": "close",
                "underlying": symbol,
                "position_id": position_id,
                "quantity": rounded_qty,
                "exit_reason": f"profit_take:{current_price}",
            }

        # Check stop loss
        if stop_loss and current_price <= stop_loss:
            logger.info(f"Stop loss triggered for {symbol} at {current_price}")
            rounded_qty = _round_contracts(qty)
            return {
                "action": "close",
                "underlying": symbol,
                "position_id": position_id,
                "quantity": rounded_qty,
                "exit_reason": f"stop_loss:{current_price}",
            }

        return None


# ======================================================================
# Helpers
# ======================================================================

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


def _round_contracts(qty: float) -> int:
    """Round fractional quantity to nearest integer.

    Options contracts must be whole numbers.  Rounds to nearest integer
    (0.5 rounds up) to avoid placing unfillable fractional orders.
    """
    return max(0, round(qty))


def _parse_entry_time(pos: dict) -> Optional[datetime]:
    """Parse entry_time from position data, falling back to now ET.

    Accepts ISO-8601 strings with or without timezone.
    """
    raw = pos.get("entry_time") or pos.get("filled_at") or pos.get("created_at")
    if not raw:
        return datetime.now(ET)
    try:
        raw_str = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw_str)
        # If naive, assume ET
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt
    except (ValueError, TypeError):
        return datetime.now(ET)
