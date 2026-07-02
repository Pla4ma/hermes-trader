"""Trailing stop manager — handles dynamic stop losses for live positions."""

import json
import os
from datetime import datetime


def update_trailing_stops() -> dict:
    """Check all positions and update stop losses based on profit levels.

    Rules:
    - Initial SL: 1.5% below entry
    - When profit > 2.5%: tighten SL to trail by 0.8%
    - When profit > 4%: signal partial sell (50%)
    - When profit > 8%: signal full sell
    """
    try:
        import alpaca_trade_api as tradeapi
        from alpaca.trading.enums import OrderSide, TimeInForce

        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        api = tradeapi.REST(api_key, secret_key, base_url)

        positions = api.list_positions()
        open_orders = api.list_orders(status="open")
        actions = []

        for pos in positions:
            entry = float(pos.avg_entry_price)
            current = float(pos.current_price)
            qty = float(pos.qty)
            pnl_pct = (current / entry - 1) * 100 if entry > 0 else 0

            # Find existing stop order
            existing_sl = None
            for o in open_orders:
                if o.symbol == pos.symbol and o.order_type == "stop":
                    existing_sl = o
                    break

            if pnl_pct >= 2.5:
                # Tighten stop to trail by 0.8%
                new_sl = round(current * 0.992, 2)
                old_sl = float(existing_sl.stop_price) if existing_sl else round(entry * 0.985, 2)

                if new_sl > old_sl:
                    # Cancel old stop and set tighter one
                    if existing_sl:
                        api.cancel_order(existing_sl.id)
                        import time
                        time.sleep(0.5)

                    try:
                        sl = api.submit_order(
                            symbol=pos.symbol, qty=str(qty),
                            side=OrderSide.SELL, type="stop",
                            stop_price=str(new_sl),
                            time_in_force=TimeInForce.DAY,
                        )
                        actions.append({
                            "symbol": pos.symbol,
                            "action": "TRAILING_SL",
                            "old_sl": old_sl,
                            "new_sl": new_sl,
                            "pnl_pct": round(pnl_pct, 2),
                        })
                    except Exception as e:
                        actions.append({
                            "symbol": pos.symbol,
                            "action": "SL_ERROR",
                            "error": str(e),
                        })

            elif not existing_sl:
                # No stop order — set initial SL at 1.5%
                sl_price = round(entry * 0.985, 2)
                try:
                    sl = api.submit_order(
                        symbol=pos.symbol, qty=str(qty),
                        side=OrderSide.SELL, type="stop",
                        stop_price=str(sl_price),
                        time_in_force=TimeInForce.DAY,
                    )
                    actions.append({
                        "symbol": pos.symbol,
                        "action": "INITIAL_SL",
                        "sl_price": sl_price,
                        "pnl_pct": round(pnl_pct, 2),
                    })
                except Exception as e:
                    actions.append({
                        "symbol": pos.symbol,
                        "action": "SL_ERROR",
                        "error": str(e),
                    })

            # Profit-taking signals
            if pnl_pct >= 4.0:
                actions.append({
                    "symbol": pos.symbol,
                    "action": "TP_SIGNAL",
                    "pnl_pct": round(pnl_pct, 2),
                    "recommendation": "SELL_50%",
                })
            elif pnl_pct >= 8.0:
                actions.append({
                    "symbol": pos.symbol,
                    "action": "TP_SIGNAL",
                    "pnl_pct": round(pnl_pct, 2),
                    "recommendation": "SELL_ALL",
                })

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "positions_checked": len(positions),
            "actions": actions,
        }

    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")
    result = update_trailing_stops()
    print(json.dumps(result, indent=2))
