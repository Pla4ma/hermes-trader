"""Portfolio rebalancer — adjusts positions based on optimal parameters."""

import json
import os
from datetime import datetime


def rebalance() -> dict:
    """Check positions and rebalance based on optimal parameters.

    Actions:
    - If a position's stop loss doesn't match optimal params, adjust it
    - If a position has exceeded optimal take profit, signal to sell
    - If cash is available, buy the next best candidate
    """
    try:
        import alpaca_trade_api as tradeapi
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        api = tradeapi.REST(api_key, secret_key, base_url)

        acct = api.get_account()
        positions = api.list_positions()
        orders = api.list_orders(status="open")

        # Load optimal params
        opt_file = "/opt/hermes-trader/data/snapshots/optimal_params.json"
        opt_params = {}
        if os.path.exists(opt_file):
            with open(opt_file) as f:
                opt_params = json.load(f)

        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "cash": float(acct.cash),
            "equity": float(acct.portfolio_value),
            "actions": [],
        }

        exit_symbols = {o.symbol for o in orders}

        for pos in positions:
            sym = pos.symbol
            entry = float(pos.avg_entry_price)
            current = float(pos.current_price)
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
