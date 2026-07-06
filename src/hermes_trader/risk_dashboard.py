"""Risk dashboard — real-time risk metrics for the trading system."""

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


def generate_risk_dashboard() -> dict:
    """Generate comprehensive risk metrics."""
    try:
        account_data = robinhood_mcp_call("get_accounts", {})
        positions_data = robinhood_mcp_call("get_equity_positions", {
            "account_number": ROBINHOOD_ACCOUNT,
        })
        orders_data = robinhood_mcp_call("get_equity_orders", {
            "account_number": ROBINHOOD_ACCOUNT,
        })

        equity = _safe_float(account_data, "equity", "portfolio_value", "account_value")
        cash = _safe_float(account_data, "cash", "cash_balance", "available_cash")
        buying_power = _safe_float(account_data, "buying_power", "instant_buying_power")

        positions = _parse_positions(positions_data)
        orders = _parse_orders_list(orders_data)

        # Position metrics
        total_position_value = sum(abs(p.market_value) for p in positions)
        total_pnl = sum(p.unrealized_pl for p in positions)
        total_pnl_pct = (total_pnl / equity * 100) if equity > 0 else 0

        # Risk metrics
        max_daily_loss = 4.0  # $4 daily loss limit
        daily_loss_used = max(0, -total_pnl)
        daily_loss_remaining = max(0, max_daily_loss - daily_loss_used)

        max_positions = 3
        current_positions = len(positions)
        positions_remaining = max(0, max_positions - current_positions)

        # Concentration risk
        max_single_position = 0
        concentration_symbol = ""
        for p in positions:
            mv = abs(p.market_value)
            if mv > max_single_position:
                max_single_position = mv
                concentration_symbol = p.symbol

        concentration_pct = (max_single_position / equity * 100) if equity > 0 else 0

        # Drawdown
        initial_equity = 50.0
        drawdown = (initial_equity - equity) / initial_equity * 100 if initial_equity > 0 else 0

        # Exit order coverage
        exit_symbols = {o["symbol"] for o in orders}
        positions_without_exits = [p.symbol for p in positions if p.symbol not in exit_symbols]

        dashboard = {
            "timestamp": datetime.utcnow().isoformat(),
            "account": {
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "buying_power": round(buying_power, 2),
                "initial_equity": initial_equity,
                "drawdown_pct": round(drawdown, 2),
            },
            "positions": {
                "count": current_positions,
                "max": max_positions,
                "remaining": positions_remaining,
                "total_value": round(total_position_value, 2),
                "total_pnl": round(total_pnl, 3),
                "total_pnl_pct": round(total_pnl_pct, 3),
            },
            "risk": {
                "daily_loss_used": round(daily_loss_used, 2),
                "daily_loss_remaining": round(daily_loss_remaining, 2),
                "daily_loss_limit": max_daily_loss,
                "concentration_symbol": concentration_symbol,
                "concentration_pct": round(concentration_pct, 1),
                "positions_without_exits": positions_without_exits,
            },
            "positions_detail": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "entry": round(p.cost_basis / p.qty, 2) if p.qty > 0 else 0,
                    "current": round(p.market_value / p.qty, 2) if p.qty > 0 else 0,
                    "pnl": round(p.unrealized_pl, 3),
                    "pnl_pct": round(p.unrealized_plpc * 100, 2),
                    "market_value": round(p.market_value, 2),
                }
                for p in positions
            ],
            "open_exits": [
                {
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "type": o.get("order_type", ""),
                    "limit": o.get("limit_price"),
                    "stop": o.get("stop_price"),
                }
                for o in orders
            ],
        }

        # Risk alerts
        alerts = []
        if daily_loss_used > 2:
            alerts.append(f"⚠️ Daily loss ${daily_loss_used:.2f} approaching $4 limit")
        if drawdown > 10:
            alerts.append(f"🔴 Drawdown {drawdown:.1f}% exceeds 10% threshold")
        if concentration_pct > 60:
            alerts.append(f"⚠️ Concentration: {concentration_symbol} is {concentration_pct:.0f}% of portfolio")
        if positions_without_exits:
            alerts.append(f"⚠️ Positions without exits: {', '.join(positions_without_exits)}")

        dashboard["alerts"] = alerts
        dashboard["risk_level"] = "LOW" if not alerts else ("HIGH" if any("🔴" in a for a in alerts) else "MEDIUM")

        return dashboard

    except Exception as e:
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")
    dashboard = generate_risk_dashboard()
    print(json.dumps(dashboard, indent=2))
