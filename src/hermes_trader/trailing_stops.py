"""Trailing stop manager — handles dynamic stop losses for live positions.

Uses Robinhood MCP (agent.robinhood.com) for position monitoring and
stop-loss order management.  Token is loaded from
~/.hermes/mcp-tokens/robinhood.json  (must contain {"token": "..."}).
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

MCP_ENDPOINT = "https://agent.robinhood.com/mcp/trading"
ACCOUNT_ID = "924058324"

# ---------------------------------------------------------------------------
# Robinhood MCP helpers
# ---------------------------------------------------------------------------

def _load_mcp_token() -> str:
    """Load the Robinhood MCP bearer token from disk."""
    token_path = Path.home() / ".hermes" / "mcp-tokens" / "robinhood.json"
    data = json.loads(token_path.read_text())
    token = data.get("token") or data.get("bearer_token") or data.get("access_token", "")
    if not token:
        raise RuntimeError(f"No token found in {token_path}")
    return token


def _mcp_call(tool_name: str, arguments: Optional[dict] = None, request_id: int = 1) -> dict:
    """Send a JSON-RPC request to the Robinhood MCP trading endpoint.

    Returns the parsed JSON-RPC response body.  Raises on HTTP errors or
    JSON-RPC error objects.
    """
    token = _load_mcp_token()
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
        "id": request_id,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        MCP_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    if "error" in result:
        raise RuntimeError(f"MCP error ({tool_name}): {result['error']}")
    return result


def _mcp_result_text(tool_name: str, arguments: Optional[dict] = None):
    """Call an MCP tool and unwrap the content, returning the parsed data.

    Robinhood MCP tools return results inside:
        {"result": {"content": [{"type": "text", "text": "<json>"}]}}
    We parse the inner text as JSON and return the data.  If the inner
    text is not JSON, we return the raw string.
    """
    resp = _mcp_call(tool_name, arguments)
    content = resp.get("result", {}).get("content", [])
    if content and content[0].get("type") == "text":
        raw = content[0]["text"]
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return resp.get("result", {})


# ---------------------------------------------------------------------------
# Position & quote fetching
# ---------------------------------------------------------------------------

def _get_positions() -> list:
    """Fetch all open equity positions from Robinhood."""
    try:
        data = _mcp_result_text("get_equity_positions", {"account_id": ACCOUNT_ID})
    except Exception:
        return []
    # Normalise: the tool may return a list directly or wrap in {"results": [...]}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("positions") or []
    return []


def _get_quotes(symbols: list) -> dict:
    """Fetch latest quotes for a list of symbols.  Returns {symbol: price}."""
    if not symbols:
        return {}
    data = _mcp_result_text(
        "get_equity_quotes",
        {"account_id": ACCOUNT_ID, "symbols": symbols},
    )
    prices = {}
    # Normalise various response shapes
    items = data if isinstance(data, list) else data.get("quotes") or data.get("results") or []
    for item in items:
        sym = item.get("symbol", item.get("ticker", ""))
        price = item.get("last_trade_price") or item.get("price") or item.get("mark_price")
        if sym and price is not None:
            prices[sym] = float(price)
    return prices


def _cancel_order(order_id: str) -> None:
    """Cancel an existing open order by its ID (best-effort)."""
    try:
        _mcp_call("cancel_equity_order", {
            "account_id": ACCOUNT_ID,
            "order_id": order_id,
        })
    except Exception as exc:
        # Cancellation is best-effort; log but don't abort
        print(f"[trailing_stops] warn: cancel order {order_id} failed: {exc}")


def _place_stop_order(
    symbol: str,
    qty: float,
    stop_price: float,
    side: str = "sell",
) -> dict:
    """Place a stop-loss equity order via Robinhood MCP.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g. "AAPL").
    qty : float
        Number of shares.
    stop_price : float
        Stop trigger price.
    side : str
        "sell" for stop-loss.

    Returns the parsed order result dict.
    """
    result = _mcp_result_text("place_equity_order", {
        "account_id": ACCOUNT_ID,
        "symbol": symbol,
        "quantity": str(qty),
        "side": side,
        "order_type": "stop",
        "stop_price": str(round(stop_price, 2)),
        "time_in_force": "day",
    })
    return result


# ---------------------------------------------------------------------------
# Main trailing-stop logic
# ---------------------------------------------------------------------------

def update_trailing_stops() -> dict:
    """Check all positions and update stop losses based on profit levels.

    Rules
    -----
    - Initial SL: 1.5% below entry
    - When profit > 2.5%: tighten SL to trail by 0.8%
    - When profit > 4%: signal partial sell (50%)
    - When profit > 8%: signal full sell
    """
    try:
        # 1. Fetch positions and current quotes
        raw_positions = _get_positions()
        if not raw_positions:
            return {
                "timestamp": datetime.utcnow().isoformat(),
                "positions_checked": 0,
                "actions": [],
            }

        # Normalise position data
        symbols = []
        positions = []
        for pos in raw_positions:
            sym = pos.get("symbol") or pos.get("ticker") or ""
            if not sym:
                continue
            entry = float(pos.get("average_buy_price") or pos.get("avg_entry_price") or 0)
            qty = float(pos.get("quantity") or pos.get("qty") or 0)
            if qty <= 0 or entry <= 0:
                continue
            symbols.append(sym)
            positions.append({"symbol": sym, "entry": entry, "qty": qty})

        prices = _get_quotes(symbols)

        actions = []

        for pos in positions:
            sym = pos["symbol"]
            entry = pos["entry"]
            qty = pos["qty"]
            current = prices.get(sym)
            if current is None:
                actions.append({
                    "symbol": sym,
                    "action": "QUOTE_MISSING",
                    "entry": entry,
                })
                continue

            pnl_pct = (current / entry - 1) * 100

            # NOTE: Robinhood does not expose a "list open orders" MCP tool,
            # so we track trailing SL state via file-based state (optional)
            # or re-post unconditionally when profitable.  For simplicity we
            # always evaluate whether a tighter SL is warranted when pnl >= 2.5%.

            if pnl_pct >= 2.5:
                new_sl = round(current * 0.992, 2)
                old_sl = round(entry * 0.985, 2)

                if new_sl > old_sl:
                    try:
                        _place_stop_order(sym, qty, new_sl, side="sell")
                        actions.append({
                            "symbol": sym,
                            "action": "TRAILING_SL",
                            "old_sl": old_sl,
                            "new_sl": new_sl,
                            "pnl_pct": round(pnl_pct, 2),
                        })
                    except Exception as e:
                        actions.append({
                            "symbol": sym,
                            "action": "SL_ERROR",
                            "error": str(e),
                        })

            else:
                # Set initial SL at 1.5% below entry
                sl_price = round(entry * 0.985, 2)
                try:
                    _place_stop_order(sym, qty, sl_price, side="sell")
                    actions.append({
                        "symbol": sym,
                        "action": "INITIAL_SL",
                        "sl_price": sl_price,
                        "pnl_pct": round(pnl_pct, 2),
                    })
                except Exception as e:
                    actions.append({
                        "symbol": sym,
                        "action": "SL_ERROR",
                        "error": str(e),
                    })

            # Profit-taking signals
            if pnl_pct >= 8.0:
                actions.append({
                    "symbol": sym,
                    "action": "TP_SIGNAL",
                    "pnl_pct": round(pnl_pct, 2),
                    "recommendation": "SELL_ALL",
                })
            elif pnl_pct >= 4.0:
                actions.append({
                    "symbol": sym,
                    "action": "TP_SIGNAL",
                    "pnl_pct": round(pnl_pct, 2),
                    "recommendation": "SELL_50%",
                })

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "positions_checked": len(positions),
            "actions": actions,
        }

    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    result = update_trailing_stops()
    print(json.dumps(result, indent=2))
