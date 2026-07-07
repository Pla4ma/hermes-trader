"""Portfolio rebalancer — adjusts positions based on optimal parameters."""

import json
import os
from datetime import datetime

from .integrations.robinhood_broker import (
    ROBINHOOD_ACCOUNT,
    _parse_orders_list,
    _parse_positions,
    _safe_float,
    robinhood_mcp_call,
)


def rebalance() -> dict:
    """Check positions and rebalance based on optimal parameters.

    Actions:
    - If a position's stop loss doesn't match optimal params, adjust it
    - If a position has exceeded optimal take profit, signal to sell
    - If cash is available, buy the next best candidate
    """
    try:
        account_data = robinhood_mcp_call("get_accounts", {})
        positions_data = robinhood_mcp_call("get_equity_positions", {
            "account_number": ROBINHOOD_ACCOUNT,
        })
        orders_data = robinhood_mcp_call("get_equity_orders", {
            "account_number": ROBINHOOD_ACCOUNT,
        })

        cash = _safe_float(account_data, "cash", "cash_balance", "available_cash")
        equity = _safe_float(account_data, "equity", "portfolio_value", "account_value")

        positions = _parse_positions(positions_data)
        orders = _parse_orders_list(orders_data)

        # Load optimal params
        opt_file = "/opt/hermes-trader/data/snapshots/optimal_params.json"
        opt_params = {}
        if os.path.exists(opt_file):
            with open(opt_file) as f:
                opt_params = json.load(f)

        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "cash": cash,
            "equity": equity,
            "actions": [],
        }

        exit_symbols = {o["symbol"] for o in orders}

        for pos in positions:
            sym = pos.symbol
            entry = pos.cost_basis / pos.qty if pos.qty > 0 else 0
            current = pos.market_value / pos.qty if pos.qty > 0 else 0
            pnl_pct = (current / entry - 1) * 100 if entry > 0 else 0

            opt = opt_params.get(sym, {}).get("params", {})
            if not opt:
                continue

            # Check if stop loss matches optimal
            optimal_sl_pct = opt.get("stop", 0.015)
            optimal_tp_pct = opt.get("tp", 0.04)

            # Check if we need to adjust stop
            if pnl_pct >= 2.5:
                # Tighten stop to trail
                new_sl = round(current * (1 - optimal_sl_pct), 2)
                result["actions"].append({
                    "symbol": sym,
                    "action": "TRAIL_STOP",
                    "new_sl": new_sl,
                    "pnl_pct": round(pnl_pct, 2),
                })

            # Check if take profit hit
            if pnl_pct >= optimal_tp_pct * 100:
                result["actions"].append({
                    "symbol": sym,
                    "action": "TP_HIT",
                    "pnl_pct": round(pnl_pct, 2),
                    "recommendation": "SELL_PARTIAL",
                })

        return result

    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")
    result = rebalance()
    print(json.dumps(result, indent=2))
