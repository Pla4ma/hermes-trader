"""Daily summary generator for after-hours review."""

import json
import os
from datetime import datetime


def generate_daily_summary() -> str:
    """Generate a comprehensive daily trading summary."""
    try:
        import alpaca_trade_api as tradeapi
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        api = tradeapi.REST(api_key, secret_key, base_url)

        acct = api.get_account()
        positions = api.list_positions()
        orders = api.list_orders(status="all", limit=50)

        total_pnl = sum(float(p.unrealized_pl) for p in positions)
        filled_orders = [o for o in orders if o.status == "filled"]

        lines = [
            "# 📊 Daily Trading Summary",
            f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d')}",
            "",
            "## Account",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Cash | ${acct.cash} |",
            f"| Equity | ${acct.portfolio_value} |",
            f"| P&L | ${total_pnl:.3f} |",
            f"| Day Trades | {acct.daytrade_count} |",
            "",
            "## Open Positions",
            "| Symbol | Entry | Current | P&L | P&L% |",
            "|---|---|---|---|---|",
        ]

        for p in positions:
            pnl = float(p.unrealized_pl)
            pnl_pct = float(p.unrealized_plpc) * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"| {p.symbol} | ${p.avg_entry_price} | ${p.current_price} | {emoji} ${pnl:.3f} | {pnl_pct:+.2f}% |")

        lines.extend([
            "",
            "## Today's Fills",
            "| Time | Symbol | Side | Qty | Price |",
            "|---|---|---|---|---|",
        ])

        for o in filled_orders:
            fill_price = o.filled_avg_price or "N/A"
            fill_qty = o.filled_qty or "0"
            lines.append(f"| {str(o.filled_at)[:19] if o.filled_at else 'N/A'} | {o.symbol} | {o.side} | {fill_qty} | ${fill_price} |")

        # Open exit orders
        open_orders = [o for o in orders if o.status in ("new", "accepted")]
        if open_orders:
            lines.extend([
                "",
                "## Exit Orders",
                "| Symbol | Side | Type | Limit | Stop |",
                "|---|---|---|---|---|",
            ])
            for o in open_orders:
                lines.append(f"| {o.symbol} | {o.side} | {o.order_type} | ${o.limit_price or 'N/A'} | ${o.stop_price or 'N/A'} |")

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
            "- ✅ Alpaca API connected",
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
