"""Risk dashboard — real-time risk metrics for the trading system."""

import json
import os
from datetime import datetime


def generate_risk_dashboard() -> dict:
    """Generate comprehensive risk metrics."""
    try:
        import alpaca_trade_api as tradeapi
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        api = tradeapi.REST(api_key, secret_key, base_url)

        acct = api.get_account()
        positions = api.list_positions()
        orders = api.list_orders(status="open")

        equity = float(acct.portfolio_value)
        cash = float(acct.cash)
        buying_power = float(acct.buying_power)

        # Position metrics
        total_position_value = sum(abs(float(p.market_value)) for p in positions)
        total_pnl = sum(float(p.unrealized_pl) for p in positions)
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
            mv = abs(float(p.market_value))
            if mv > max_single_position:
                max_single_position = mv
                concentration_symbol = p.symbol

        concentration_pct = (max_single_position / equity * 100) if equity > 0 else 0

        # Drawdown
        initial_equity = 50.0
        drawdown = (initial_equity - equity) / initial_equity * 100 if initial_equity > 0 else 0

        # Exit order coverage
        exit_symbols = {o.symbol for o in orders}
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
                    "qty": float(p.qty),
                    "entry": float(p.avg_entry_price),
                    "current": float(p.current_price),
                    "pnl": round(float(p.unrealized_pl), 3),
                    "pnl_pct": round(float(p.unrealized_plpc) * 100, 2),
                    "market_value": round(float(p.market_value), 2),
                }
                for p in positions
            ],
            "open_exits": [
                {
                    "symbol": o.symbol,
                    "side": o.side,
                    "type": o.order_type,
                    "limit": o.limit_price,
                    "stop": o.stop_price,
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
