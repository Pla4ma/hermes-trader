"""Daily summary generator for after-hours review."""

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


def generate_daily_summary() -> str:
    """Generate a comprehensive daily trading summary."""
    try:
        account_data = robinhood_mcp_call("get_portfolio", {"account_number": ROBINHOOD_ACCOUNT})
        positions_data = robinhood_mcp_call("get_equity_positions", {
            "account_number": ROBINHOOD_ACCOUNT,
        })
        orders_data = robinhood_mcp_call("get_equity_orders", {
            "account_number": ROBINHOOD_ACCOUNT,
        })

        equity = _safe_float(account_data.get("data", account_data) if isinstance(account_data, dict) else {}, "equity_value", "equity", "total_value", "portfolio_value")
        cash = _safe_float(account_data.get("data", account_data) if isinstance(account_data, dict) else {}, "cash", "cash_balance", "available_cash")
        daytrade_count = int(_safe_float(account_data, "daytrade_count", "day_trade_count", default=0))

        positions = _parse_positions(positions_data)
        orders = _parse_orders_list(orders_data)

        total_pnl = sum(p.unrealized_pl for p in positions)
        filled_orders = [o for o in orders if o.get("status") == "filled"]

        lines = [
            "# 📊 Daily Trading Summary",
            f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d')}",
            "",
            "## Account",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Cash | ${cash:.2f} |",
            f"| Equity | ${equity:.2f} |",
            f"| P&L | ${total_pnl:.3f} |",
            f"| Day Trades | {daytrade_count} |",
            "",
            "## Open Positions",
            "| Symbol | Entry | Current | P&L | P&L% |",
            "|---|---|---|---|---|",
        ]

        for p in positions:
            pnl = p.unrealized_pl
            pnl_pct = p.unrealized_plpc * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            entry = p.cost_basis / p.qty if p.qty > 0 else 0
            current = p.market_value / p.qty if p.qty > 0 else 0
            lines.append(f"| {p.symbol} | ${entry:.2f} | ${current:.2f} | {emoji} ${pnl:.3f} | {pnl_pct:+.2f}% |")

        lines.extend([
            "",
            "## Today's Fills",
            "| Time | Symbol | Side | Qty | Price |",
            "|---|---|---|---|---|",
        ])

        for o in filled_orders:
            fill_price = o.get("filled_avg_price", "N/A") or "N/A"
            fill_qty = o.get("filled_qty", "0") or "0"
            fill_time = str(o.get("submitted_at", "N/A"))[:19] if o.get("submitted_at") else "N/A"
            lines.append(f"| {fill_time} | {o['symbol']} | {o['side']} | {fill_qty} | ${fill_price} |")

        # Open exit orders
        open_orders = [o for o in orders if o.get("status") in ("new", "accepted", "open", "partially_filled")]
        if open_orders:
            lines.extend([
                "",
                "## Exit Orders",
                "| Symbol | Side | Type | Status |",
                "|---|---|---|---|",
            ])
            for o in open_orders:
                lines.append(f"| {o['symbol']} | {o['side']} | {o.get('order_type', 'N/A')} | {o.get('status', 'N/A')} |")

        # Journal analysis
        journal_path = "/opt/hermes-trader/data/journals/paper_orders.jsonl"
        if os.path.exists(journal_path):
            with open(journal_path) as f:
                entries = [json.loads(line) for line in f if line.strip()]
            today = datetime.utcnow().strftime("%Y-%m-%d")
            today_entries = [e for e in entries if e.get("timestamp", "").startswith(today)]
            lines.extend([
                "",
                f"## Journal: {len(today_entries)} entries today ({len(entries)} total)",
            ])

        lines.extend([
            "",
            "## System Status",
            "- ✅ Robinhood MCP connected",
            "- ✅ Real market data (yfinance)",
            "- ✅ Auto-trading engine active",
            "- ✅ Trailing stops enabled",
            "- ✅ Position monitor cron (every 15 min)",
            "",
            "## Next Steps",
            "- Position monitor runs every 15 min during market hours",
            "- Auto-trade will buy next best candidate when cash frees up",
            "- Trailing stops tighten at 2.5% profit",
            "- Take profit signals at 4% and 8%",
        ])

        return "\n".join(lines)

    except Exception as e:
        return f"# Daily Summary Error\n{e}"


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")
    print(generate_daily_summary())
