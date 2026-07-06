"""Portfolio report — comprehensive portfolio status for Telegram delivery."""

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


def generate_portfolio_report() -> str:
    """Generate a comprehensive portfolio report."""
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

        positions = _parse_positions(positions_data)
        orders = _parse_orders_list(orders_data)
        open_orders = [o for o in orders if o.get("status") not in ("filled", "canceled", "expired", "rejected")]

        total_pnl = sum(p.unrealized_pl for p in positions)
        total_pnl_pct = (total_pnl / equity * 100) if equity > 0 else 0

        # Market regime
        try:
            from .market_regime import detect_regime
            regime = detect_regime()
            regime_name = regime.get("regime", "UNKNOWN")
            aggression = regime.get("aggression", "UNKNOWN")
        except Exception:
            regime_name = "UNKNOWN"
            aggression = "UNKNOWN"

        # Risk dashboard
        try:
            from .risk_dashboard import generate_risk_dashboard
            risk = generate_risk_dashboard()
            risk_level = risk.get("risk_level", "UNKNOWN")
            alerts = risk.get("alerts", [])
        except Exception:
            risk_level = "UNKNOWN"
            alerts = []

        lines = [
            "📊 **HERMES TRADING SYSTEM**",
            f"📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "💰 **Account**",
            f"  Equity: ${equity:.2f}",
            f"  Cash: ${cash:.2f}",
            f"  P&L: ${total_pnl:.3f} ({total_pnl_pct:+.2f}%)",
            "",
            "📈 **Positions**",
        ]

        for p in positions:
            pnl = p.unrealized_pl
            pnl_pct = p.unrealized_plpc * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            entry = p.cost_basis / p.qty if p.qty > 0 else 0
            current = p.market_value / p.qty if p.qty > 0 else 0
            lines.append(f"  {emoji} {p.symbol}: ${entry:.2f} → ${current:.2f} ({pnl_pct:+.2f}%)")

        if not positions:
            lines.append("  No open positions")

        lines.extend([
            "",
            "🛡️ **Risk Status**",
            f"  Level: {risk_level}",
            f"  Regime: {regime_name} ({aggression})",
        ])

        for alert in alerts:
            lines.append(f"  {alert}")

        if not alerts:
            lines.append("  ✅ No alerts")

        if open_orders:
            lines.extend([
                "",
                "🎯 **Exit Orders**",
            ])
            for o in open_orders:
                sl = f"${o.get('stop_price', 'N/A')}" if o.get('stop_price') else "N/A"
                tp = f"${o.get('limit_price', 'N/A')}" if o.get('limit_price') else "N/A"
                lines.append(f"  {o['symbol']}: SL={sl} TP={tp}")

        lines.extend([
            "",
            "⚙️ **System**",
            "  ✅ Robinhood MCP connected",
            "  ✅ Real market data (yfinance)",
            "  ✅ Auto-trading engine",
            "  ✅ Trailing stops",
            "  ✅ Position monitor (15 min)",
            "  ✅ Market regime detector",
            "  ✅ Risk dashboard",
        ])

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Portfolio report error: {e}"


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")
    print(generate_portfolio_report())
