"""Robinhood broker adapter — MCP API via HTTP JSON-RPC.

Uses the Robinhood MCP API at https://agent.robinhood.com/mcp/trading
with OAuth Bearer token auth. Token is loaded from ~/.hermes/mcp-tokens/robinhood.json.

All live trading goes through the Robinhood MCP JSON-RPC endpoint.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import config
from ..models.order_request import OrderRequest
from ..models.position_snapshot import (
    AccountSnapshot,
    MarketSnapshot,
    PositionSnapshot,
    RiskSnapshot,
)

logger = logging.getLogger("hermes_trader.broker.robinhood")

# ── MCP API Config ─────────────────────────────────────────────
ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"
ROBINHOOD_TOKEN_PATH = Path.home() / ".hermes" / "mcp-tokens" / "robinhood.json"
ROBINHOOD_ACCOUNT = "924058324"  # Agentic account number


class BrokerError(Exception):
    """Raised on broker API failures."""


# ── MCP Helper ─────────────────────────────────────────────────


def _load_robinhood_token() -> str:
    """Load the OAuth Bearer token from the token file."""
    if not ROBINHOOD_TOKEN_PATH.exists():
        raise BrokerError(
            f"Robinhood MCP token not found at {ROBINHOOD_TOKEN_PATH}. "
            "Run the Robinhood OAuth flow first."
        )
    with open(ROBINHOOD_TOKEN_PATH) as f:
        data = json.load(f)
    token = data.get("access_token")
    if not token:
        raise BrokerError(
            f"No 'access_token' field in {ROBINHOOD_TOKEN_PATH}."
        )
    return token


def robinhood_mcp_call(tool_name: str, arguments: Optional[dict] = None) -> Any:
    """Make a JSON-RPC 2.0 call to the Robinhood MCP trading API.

    Args:
        tool_name: MCP tool name (e.g. "get_accounts", "place_equity_order").
        arguments: Tool arguments dict. Defaults to empty dict.

    Returns:
        Parsed JSON result from the SSE response.

    Raises:
        BrokerError: On HTTP errors, auth failures, or MCP error responses.
    """
    token = _load_robinhood_token()

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    try:
        resp = requests.post(
            ROBINHOOD_MCP_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        raise BrokerError(f"Robinhood MCP HTTP request failed: {e}") from e

    if resp.status_code == 401:
        raise BrokerError(
            "Robinhood MCP auth failed (401). Token may be expired — "
            "re-run the OAuth flow."
        )
    if resp.status_code == 403:
        raise BrokerError(
            "Robinhood MCP forbidden (403). Check account permissions."
        )
    if resp.status_code >= 400:
        raise BrokerError(
            f"Robinhood MCP HTTP {resp.status_code}: {resp.text[:500]}"
        )

    # Parse SSE response: "event: message\ndata: {json}"
    return _parse_sse_response(resp.text)


def _parse_sse_response(raw: str) -> Any:
    """Parse an SSE-formatted response body.

    SSE format:
        event: message
        data: {"jsonrpc": "2.0", "id": "...", "result": {...}}

    Returns the parsed JSON-RPC result.
    Raises BrokerError on parse failure or RPC-level errors.
    """
    # Find the data line(s) — SSE can have multiple data lines
    result_data = None
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.startswith("data:"):
            json_str = line[len("data:"):].strip()
            if json_str:
                try:
                    result_data = json.loads(json_str)
                except json.JSONDecodeError:
                    continue

    if result_data is None:
        # Fallback: try parsing the entire body as JSON
        # (some responses come as plain JSON, not SSE)
        try:
            result_data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            raise BrokerError(
                f"Could not parse Robinhood MCP response. "
                f"Raw (first 500 chars): {raw[:500]}"
            )

    # Check for JSON-RPC error
    if "error" in result_data:
        err = result_data["error"]
        raise BrokerError(
            f"MCP RPC error {err.get('code', '?')}: "
            f"{err.get('message', str(err))}"
        )

    # Extract the tool result
    result = result_data.get("result", result_data)

    # MCP tool results are wrapped in content array: {"content": [{"type": "text", "text": "..."}]}
    if isinstance(result, dict) and "content" in result:
        content = result["content"]
        if isinstance(content, list) and len(content) > 0:
            text_block = content[0]
            if isinstance(text_block, dict) and "text" in text_block:
                try:
                    return json.loads(text_block["text"])
                except (json.JSONDecodeError, TypeError):
                    return text_block["text"]

    return result


# ── Robinhood Broker Adapter ───────────────────────────────────


class RobinhoodBrokerAdapter:
    """Robinhood broker adapter using MCP API.

    All live trading goes through the Robinhood MCP JSON-RPC endpoint.
    """

    def __init__(self):
        self._journal_path = config.project_root / "data" / "journals" / "robinhood_orders.jsonl"
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._log: list[dict] = []
        self._load_journal()

    # ── Account ───────────────────────────────────────────────

    def get_account(self) -> AccountSnapshot:
        """Fetch live account state from Robinhood."""
        try:
            return self._fetch_account_snapshot()
        except BrokerError as e:
            logger.error(f"Robinhood get_account failed: {e}")
            # Fall back to journal-derived state
            return self._compute_account_state_from_journal()

    def _fetch_account_snapshot(self) -> AccountSnapshot:
        """Fetch real account + positions from Robinhood MCP."""
        # Get account info
        account_data = robinhood_mcp_call("get_accounts", {})
        positions_data = robinhood_mcp_call("get_equity_positions", {
            "account_number": ROBINHOOD_ACCOUNT,
        })

        # Parse account fields (flexible to response shape)
        equity = _safe_float(account_data, "equity", "portfolio_value", "account_value", default=0.0)
        cash = _safe_float(account_data, "cash", "cash_balance", "available_cash", default=0.0)
        buying_power = _safe_float(account_data, "buying_power", "instant_buying_power", default=0.0)

        # Parse positions
        positions = _parse_positions(positions_data)

        return AccountSnapshot(
            equity=equity,
            cash=cash,
            buying_power=buying_power or equity,
            portfolio_value=equity,
            positions=positions,
            open_orders_count=len(self.get_open_orders()),
        )

    def get_position(self, symbol: str) -> Optional[PositionSnapshot]:
        """Return current position for a symbol, if any."""
        try:
            data = robinhood_mcp_call("get_equity_positions", {
                "account_number": ROBINHOOD_ACCOUNT,
            })
            positions = _parse_positions(data)
            for pos in positions:
                if pos.symbol.upper() == symbol.upper():
                    return pos
            return None
        except BrokerError as e:
            logger.warning(f"get_position({symbol}) failed: {e}")
            return None

    def get_positions(self) -> list[PositionSnapshot]:
        """Return all open equity positions."""
        try:
            data = robinhood_mcp_call("get_equity_positions", {
                "account_number": ROBINHOOD_ACCOUNT,
            })
            return _parse_positions(data)
        except BrokerError as e:
            logger.error(f"get_positions failed: {e}")
            return []

    def get_open_orders(self) -> list[dict]:
        """Return current open (unfilled) orders."""
        try:
            data = robinhood_mcp_call("get_equity_orders", {
                "account_number": ROBINHOOD_ACCOUNT,
            })
            orders = _parse_orders_list(data)
            return [o for o in orders if o.get("status") not in ("filled", "canceled", "expired", "rejected")]
        except BrokerError as e:
            logger.warning(f"get_open_orders failed: {e}")
            # Fall back to journal
            return [entry for entry in self._log if entry.get("status") == "open"]

    # ── Market Data ───────────────────────────────────────────

    def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        """Return market data snapshot via Robinhood MCP quotes."""
        try:
            data = robinhood_mcp_call("get_equity_quotes", {
                "account_number": ROBINHOOD_ACCOUNT,
                "symbols": [symbol],
            })
            return _parse_market_snapshot(data, symbol)
        except BrokerError as e:
            logger.warning(f"get_market_snapshot({symbol}) failed: {e}")
            # Fallback to yfinance
            return self._yfinance_snapshot(symbol)

    def _yfinance_snapshot(self, symbol: str) -> MarketSnapshot:
        """Fallback market data from yfinance."""
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = info.get("lastPrice", 0.0) or 0.0
            bid = info.get("bid", 0.0) or 0.0
            ask = info.get("ask", 0.0) or 0.0
            volume = int(info.get("lastVolume", 0) or 0)
            return MarketSnapshot(
                timestamp=datetime.utcnow().isoformat(),
                symbol=symbol,
                last_price=round(price, 2),
                bid=round(bid, 2),
                ask=round(ask, 2),
                volume=volume,
                market_open=self._is_market_open(),
            )
        except Exception as e2:
            logger.warning(f"yfinance fallback also failed for {symbol}: {e2}")
            return MarketSnapshot(
                timestamp=datetime.utcnow().isoformat(),
                symbol=symbol,
                last_price=0.0,
                bid=0.0,
                ask=0.0,
                volume=0,
                market_open=self._is_market_open(),
            )

    # ── Orders ────────────────────────────────────────────────

    def submit_order(self, order: OrderRequest) -> dict:
        """Submit order via Robinhood MCP. Journals locally for audit trail."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "order_id": f"rh_{int(datetime.utcnow().timestamp())}_{order.symbol}",
            "status": "submitted",
            "side": order.side,
            "symbol": order.symbol,
            "qty": order.qty,
            "notional": order.notional,
            "order_type": order.order_type,
            "limit_price": order.limit_price,
            "order_class": order.order_class,
            "take_profit": order.take_profit,
            "stop_loss": order.stop_loss,
            "candidate_id": order.candidate_id,
            "legs": [leg.model_dump() for leg in order.legs] if order.legs else None,
            "required_maintenance_margin": order.required_maintenance_margin,
        }

        if config.is_live_unlocked:
            entry["mode"] = "LIVE"
            try:
                if order.order_class == "mleg" and order.legs:
                    result = self._submit_option_order(order)
                elif self._is_option_symbol(order.symbol):
                    result = self._submit_option_order(order)
                else:
                    result = self._submit_equity_order(order)
                entry["status"] = "filled"
                entry["rh_order_id"] = result.get("id", result.get("order_id", ""))
                entry["rh_status"] = result.get("status", "")
                entry["filled_avg_price"] = result.get("filled_avg_price",
                    result.get("average_fill_price", ""))
                entry["filled_qty"] = result.get("filled_qty",
                    result.get("filled_quantity", "0"))
            except Exception as e:
                entry["status"] = "rh_error"
                entry["error"] = str(e)
                logger.error(f"Robinhood execution failed: {e}")
        else:
            entry["mode"] = "PAPER"

        self._append_journal(entry)
        logger.info(f"Order journaled: {entry['order_id']} ({entry['mode']})")
        return entry

    def _submit_equity_order(self, order: OrderRequest) -> dict:
        """Submit an equity order via Robinhood MCP place_equity_order."""
        args: dict[str, Any] = {
            "account_number": ROBINHOOD_ACCOUNT,
            "symbol": order.symbol.upper(),
            "side": order.side,
            "type": order.order_type or "market",
            "time_in_force": order.time_in_force or "day",
        }

        if order.notional and float(order.notional) > 0:
            args["amount"] = str(round(float(order.notional), 2))
            args["type"] = "market"
        elif order.qty and float(order.qty) > 0:
            args["quantity"] = str(int(order.qty))

        if order.limit_price and float(order.limit_price) > 0:
            args["price"] = str(order.limit_price)
            args["type"] = "limit"

        result = robinhood_mcp_call("place_equity_order", args)
        return result if isinstance(result, dict) else {"result": result}

    def _submit_option_order(self, order: OrderRequest) -> dict:
        """Submit an option order via Robinhood MCP place_option_order.

        For multi-leg (mleg) orders, submits as a single option order with legs.
        For simple option orders, builds the instrument ID from order details.
        """
        args: dict[str, Any] = {
            "account_number": ROBINHOOD_ACCOUNT,
        }

        if order.legs and len(order.legs) > 1:
            # Multi-leg order
            legs = []
            for leg in order.legs:
                legs.append({
                    "symbol": leg.symbol,
                    "side": leg.side,
                    "quantity": str(int(leg.qty)),
                    "position_intent": leg.position_intent,
                })
                if leg.limit_price is not None:
                    legs[-1]["price"] = str(leg.limit_price)
            args["legs"] = legs
            args["type"] = order.order_type or "limit"
            if order.limit_price and float(order.limit_price) > 0:
                args["price"] = str(order.limit_price)
        else:
            # Simple option order
            args["instrument_symbol"] = order.symbol
            args["side"] = order.side
            args["type"] = order.order_type or "market"
            args["quantity"] = str(int(order.qty)) if order.qty else "1"
            if order.limit_price and float(order.limit_price) > 0:
                args["price"] = str(order.limit_price)

        result = robinhood_mcp_call("place_option_order", args)
        return result if isinstance(result, dict) else {"result": result}

    def close_position(self, symbol: str, qty: Optional[float] = None) -> dict:
        """Close a position by submitting a sell order via Robinhood MCP."""
        # First get current position to determine qty
        if qty is None or qty <= 0:
            pos = self.get_position(symbol)
            if pos is None:
                entry = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "action": "close_position",
                    "symbol": symbol,
                    "status": "no_position",
                    "error": f"No open position for {symbol}",
                }
                self._append_journal(entry)
                return entry
            qty = pos.qty

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "close_position",
            "symbol": symbol,
            "qty": qty,
            "status": "submitted",
            "reason": "manual_close",
        }

        if config.is_live_unlocked:
            entry["mode"] = "LIVE"
            try:
                close_order = OrderRequest(
                    candidate_id=f"close_{symbol}_{int(datetime.utcnow().timestamp())}",
                    symbol=symbol,
                    side="sell",
                    order_type="market",
                    qty=qty,
                    time_in_force="day",
                    order_class="simple",
                )
                result = self._submit_equity_order(close_order)
                entry["status"] = "filled"
                entry["rh_order_id"] = result.get("id", result.get("order_id", ""))
                entry["filled_avg_price"] = result.get("filled_avg_price",
                    result.get("average_fill_price", ""))
            except Exception as e:
                entry["status"] = "rh_error"
                entry["error"] = str(e)
                logger.error(f"close_position({symbol}) failed: {e}")
        else:
            entry["mode"] = "PAPER"

        self._append_journal(entry)
        return entry

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order via Robinhood MCP."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "cancel_order",
            "order_id": order_id,
            "status": "submitted",
        }

        if config.is_live_unlocked:
            entry["mode"] = "LIVE"
            try:
                # Try equity cancel first, then option cancel
                try:
                    robinhood_mcp_call("cancel_equity_order", {
                        "account_number": ROBINHOOD_ACCOUNT,
                        "order_id": order_id,
                    })
                    entry["status"] = "cancelled"
                except BrokerError:
                    robinhood_mcp_call("cancel_option_order", {
                        "account_number": ROBINHOOD_ACCOUNT,
                        "order_id": order_id,
                    })
                    entry["status"] = "cancelled"
            except BrokerError as e:
                entry["status"] = "cancel_failed"
                entry["error"] = str(e)
                logger.error(f"cancel_order({order_id}) failed: {e}")
        else:
            entry["mode"] = "PAPER"

        self._append_journal(entry)
        return entry

    def get_order_status(self, order_id: str) -> dict:
        """Check order status via Robinhood MCP."""
        try:
            return robinhood_mcp_call("get_order_status", {
                "account_number": ROBINHOOD_ACCOUNT,
                "order_id": order_id,
            })
        except BrokerError as e:
            logger.warning(f"get_order_status({order_id}) failed: {e}")
            return {"order_id": order_id, "status": "unknown", "error": str(e)}

    def search(self, query: str, asset_class: str = "equity") -> list[dict]:
        """Search for instruments via Robinhood MCP search."""
        try:
            result = robinhood_mcp_call("search", {"query": query})
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("results", result.get("instruments", []))
            return []
        except BrokerError as e:
            logger.warning(f"search('{query}') failed: {e}")
            return []

    # ── Risk State ────────────────────────────────────────────

    def get_risk_snapshot(self) -> RiskSnapshot:
        """Compute risk snapshot from journal history and live positions."""
        # Compute from journal
        trades_today = 0
        trades_this_week = 0
        daily_pnl = 0.0
        weekly_pnl = 0.0
        monthly_pnl = 0.0
        consecutive_losses = 0
        now = datetime.utcnow()

        for entry in self._log:
            if entry.get("status") in ("filled", "executed"):
                try:
                    ts = datetime.fromisoformat(entry["timestamp"])
                except (ValueError, KeyError):
                    continue
                pnl = entry.get("filled_pnl", 0.0) or 0.0

                if ts.date() == now.date():
                    trades_today += 1
                    daily_pnl += pnl
                    if pnl < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0

                from datetime import timedelta

                week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                week_start = week_start - timedelta(days=week_start.weekday())
                if ts >= week_start:
                    trades_this_week += 1
                    weekly_pnl += pnl

                if ts.month == now.month and ts.year == now.year:
                    monthly_pnl += pnl

        return RiskSnapshot(
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            monthly_pnl=monthly_pnl,
            consecutive_losses=consecutive_losses,
            trades_today=trades_today,
            trades_this_week=trades_this_week,
            daily_loss_budget_remaining=max(0.0, config.max_daily_loss_usd - abs(daily_pnl)),
            weekly_loss_budget_remaining=max(0.0, config.max_weekly_loss_usd - abs(weekly_pnl)),
            monthly_loss_budget_remaining=max(0.0, config.max_monthly_loss_usd - abs(monthly_pnl)),
        )

    # ── Internal Helpers ──────────────────────────────────────

    def _compute_account_state_from_journal(self) -> AccountSnapshot:
        """Derive paper account state from journal (fallback)."""
        start_capital = config.max_experiment_capital_usd
        total_pnl = 0.0
        positions: dict[str, PositionSnapshot] = {}

        for entry in self._log:
            if entry.get("status") in ("filled", "simulated_live", "executed"):
                side = entry.get("side", "")
                symbol = entry.get("symbol", "")
                qty = entry.get("qty", 0.0) or 0.0
                price = entry.get("filled_price", 0.0) or 0.0

                if side == "buy":
                    total_pnl -= qty * price
                    if symbol not in positions:
                        positions[symbol] = PositionSnapshot(
                            symbol=symbol,
                            qty=0.0,
                            market_value=0.0,
                            cost_basis=0.0,
                            unrealized_pl=0.0,
                            unrealized_plpc=0.0,
                        )
                    positions[symbol].qty += qty
                    positions[symbol].cost_basis += qty * price
                    positions[symbol].market_value += qty * price
                elif side == "sell":
                    total_pnl += qty * price
                    if symbol in positions:
                        positions[symbol].qty -= qty
                        positions[symbol].market_value -= qty * price

        equity = start_capital + total_pnl
        return AccountSnapshot(
            equity=max(0.0, equity),
            cash=max(0.0, equity),
            buying_power=max(0.0, equity * 2),
            portfolio_value=max(0.0, equity),
            positions=list(positions.values()),
            open_orders_count=len(self.get_open_orders()),
        )

    def _is_market_open(self) -> bool:
        """Return whether US equity markets are open."""
        now = datetime.utcnow()
        if now.weekday() >= 5:
            return False
        hour_utc = now.hour
        return 13 <= hour_utc <= 20

    def _is_option_symbol(self, symbol: str) -> bool:
        """Heuristic: detect if a symbol is an option contract."""
        # Option symbols typically contain digits for expiration/strike
        # e.g. "AAPL250718C00200000" or "AAPL 250718C200"
        import re
        return bool(re.search(r"\d{6}[CP]\d+", symbol.upper()))

    def _load_journal(self) -> None:
        if self._journal_path.exists():
            with open(self._journal_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self._log.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning(f"Skipping malformed journal line: {line[:80]}")

    def _append_journal(self, entry: dict) -> None:
        self._log.append(entry)
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")


# ── Response Parsing Helpers ───────────────────────────────────


def _is_market_open() -> bool:
    """Return whether US equity markets are open (module-level helper)."""
    now = datetime.utcnow()
    if now.weekday() >= 5:
        return False
    hour_utc = now.hour
    return 13 <= hour_utc <= 20


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


def _parse_positions(data: Any) -> list[PositionSnapshot]:
    """Parse equity positions from MCP response into PositionSnapshot list."""
    positions: list[PositionSnapshot] = []

    # Handle different response shapes
    if isinstance(data, dict):
        # Could be {"positions": [...]} or {"results": [...]}
        raw = data.get("positions", data.get("results", data.get("holdings", [])))
    elif isinstance(data, list):
        raw = data
    else:
        return positions

    for pos in raw:
        if not isinstance(pos, dict):
            continue
        symbol = pos.get("symbol", pos.get("ticker", ""))
        if not symbol:
            continue

        qty = _safe_float(pos, "quantity", "qty", "shares", "net_quantity")
        market_value = _safe_float(pos, "market_value", "value", "market_value_today", default=0.0)
        cost_basis = _safe_float(pos, "cost_basis", "average_buy_price", "average_cost", default=0.0)
        # If no market_value from API, compute from qty * price
        if market_value == 0.0 and qty > 0:
            price = _safe_float(pos, "last_price", "current_price", "price", default=0.0)
            market_value = qty * price

        unrealized_pl = _safe_float(pos, "unrealized_pl", "unrealized_gain", "unrealized_pnl", default=0.0)
        unrealized_plpc = _safe_float(pos, "unrealized_plpc", "unrealized_gain_pct",
            "unrealized_percent", default=0.0)

        # Determine side from net_quantity or side field
        net_qty = _safe_float(pos, "net_quantity", default=qty)
        side = "long" if net_qty >= 0 else "short"

        positions.append(PositionSnapshot(
            symbol=symbol,
            qty=abs(qty),
            market_value=abs(market_value),
            cost_basis=abs(cost_basis),
            unrealized_pl=unrealized_pl,
            unrealized_plpc=unrealized_plpc,
            side=side,
            asset_class=pos.get("asset_class", "equity"),
        ))

    return positions


def _parse_orders_list(data: Any) -> list[dict]:
    """Parse equity orders from MCP response into a list of dicts."""
    if isinstance(data, dict):
        raw = data.get("orders", data.get("results", []))
    elif isinstance(data, list):
        raw = data
    else:
        return []

    orders = []
    for order in raw:
        if not isinstance(order, dict):
            continue
        orders.append({
            "order_id": order.get("id", order.get("order_id", "")),
            "symbol": order.get("symbol", order.get("ticker", "")),
            "side": order.get("side", ""),
            "status": order.get("status", ""),
            "qty": order.get("quantity", order.get("qty", 0)),
            "filled_qty": order.get("filled_quantity", order.get("filled_qty", 0)),
            "filled_avg_price": order.get("average_fill_price", order.get("filled_avg_price", "")),
            "order_type": order.get("type", order.get("order_type", "")),
            "submitted_at": order.get("submitted_at", order.get("created_at", "")),
        })

    return orders


def _parse_market_snapshot(data: Any, symbol: str) -> MarketSnapshot:
    """Parse equity quotes from MCP response into MarketSnapshot."""
    # data could be a dict with the quote, a list, or nested
    quote = {}
    if isinstance(data, dict):
        # Could be {"quotes": {"AAPL": {...}}} or direct quote dict
        if "quotes" in data:
            quotes_dict = data["quotes"]
            if isinstance(quotes_dict, dict):
                quote = quotes_dict.get(symbol.upper(), {})
            elif isinstance(quotes_dict, list) and quotes_dict:
                quote = quotes_dict[0]
        elif "results" in data:
            results = data["results"]
            if isinstance(results, list) and results:
                quote = results[0]
            elif isinstance(results, dict):
                quote = results
        else:
            quote = data
    elif isinstance(data, list) and data:
        quote = data[0] if isinstance(data[0], dict) else {}

    last_price = _safe_float(quote, "last_trade_price", "last_price", "mark_price",
        "close", "price", default=0.0)
    bid = _safe_float(quote, "bid_price", "bid", "bid_size", default=0.0)
    ask = _safe_float(quote, "ask_price", "ask", "ask_size", default=0.0)
    volume = int(_safe_float(quote, "volume", "today_volume", default=0.0))

    spread_pct = 0.0
    if last_price > 0 and bid > 0 and ask > 0:
        spread_pct = round(((ask - bid) / last_price) * 100, 4)

    return MarketSnapshot(
        timestamp=datetime.utcnow().isoformat(),
        symbol=symbol.upper(),
        last_price=round(last_price, 2),
        bid=round(bid, 2),
        ask=round(ask, 2),
        spread_pct=spread_pct,
        volume=volume,
        market_open=_is_market_open(),
    )
