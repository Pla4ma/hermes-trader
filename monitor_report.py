import sys, json, datetime, os
sys.path.insert(0, 'src')
from hermes_trader.integrations.alpaca_broker import PaperBrokerAdapter
from hermes_trader.workflow.enhanced_daily_workflow import EnhancedDailyWorkflow

broker = PaperBrokerAdapter()
workflow = EnhancedDailyWorkflow()

# Get account and market data
account = broker.get_account()
risk = broker.get_risk_snapshot()
market = broker.get_market_snapshot('SPY')

# Run position monitor
actions = workflow.monitor_positions()

# Build report
report = {
    'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
    'kill_switch_active': os.path.exists('/opt/hermes-trader/KILL_SWITCH'),
    'equity': account.equity,
    'cash': account.cash,
    'buying_power': account.buying_power,
    'positions': [{'symbol': p.symbol, 'qty': p.qty, 'market_value': p.market_value} for p in account.positions],
    'open_orders_count': len(broker.get_open_orders()),
    'market_open': market.market_open,
    'spy_price': market.last_price,
    'daily_pnl': risk.daily_pnl,
    'weekly_pnl': risk.weekly_pnl,
    'trades_today': risk.trades_today,
    'consecutive_losses': risk.consecutive_losses,
    'positions_checked': len(account.positions),
    'actions_taken': actions
}

# Print summary
print('HERMES TRADER POSITION MONITOR REPORT')
print('=' * 50)
print(f'Timestamp: {report["timestamp"]}')
print(f'Kill Switch: {"ACTIVE" if report["kill_switch_active"] else "INACTIVE"}')
print()
print('PORTFOLIO:')
print(f'  Equity: ${report["equity"]:.2f}')
print(f'  Cash:   ${report["cash"]:.2f}')
print(f'  Buying Power: ${report["buying_power"]:.2f}')
print(f'  Positions: {len(report["positions"])}')
for p in report['positions']:
    print(f'    {p["symbol"]}: {p["qty"]} shares (${p["market_value"]:.2f})')
print(f'  Open Orders: {report["open_orders_count"]}')
print()
print('MARKET:')
print(f'  Market Open: {"YES" if report["market_open"] else "NO"}')
print(f'  SPY Price: ${report["spy_price"]:.2f}')
print()
print('POSITION MONITOR:')
print(f'  Positions Checked: {report["positions_checked"]}')
print(f'  Actions Taken: {len(report["actions_taken"])}')
if report['actions_taken']:
    for i, a in enumerate(report['actions_taken'], 1):
        print(f'    {i}. {a}')
else:
    print('    None')
print()
print('RISK METRICS:')
print(f'  Daily P&L: ${report["daily_pnl"]:.2f}')
print(f'  Weekly P&L: ${report["weekly_pnl"]:.2f}')
print(f'  Trades Today: {report["trades_today"]}')
print(f'  Consecutive Losses: {report["consecutive_losses"]}')
