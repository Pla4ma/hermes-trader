# Hermes Trader — Aggressive System Activation Checklist

## System State: READY FOR PAPER TRADING

### Aggressive Features Enabled
- [x] Pyramid position scaling (add to winners)
- [x] Trailing stop-loss (1.5% initial, 0.8% trail, 2.5% activation)
- [x] Partial profit-taking (50% at 4% profit)
- [x] SPY momentum breakout scanner (20-day lookback, $3k trade size)
- [x] Technical analysis scoring (SMA, EMA, MACD, RSI — 20 points max)
- [x] Enhanced candidate scoring (100-point system with technical dimension)
- [x] Backtest validator (Sharpe > 1.2, Win Rate > 55%)
- [x] TradingAgents 4 debate rounds + 4 risk rounds
- [x] Position monitor cron job (every 15 min, 10AM-4PM weekdays)
- [x] Daily workflow cron job (10AM weekdays)
- [x] Morning status cron job (9AM weekdays)

### Tests
- [x] 30/30 tests passing
- [x] Enhanced workflow test passing
- [x] All module imports successful

### Safety Gates (Paper Mode)
- [x] Kill switch active check
- [x] Daily loss budget ($2/day)
- [x] Max 3 consecutive losses
- [x] Paper-only execution (live disabled)
- [x] Max position size $5,000
- [x] Max experiment capital $20

### To Enable Live Trading (USE EXTREME CAUTION)
1. Set in `.env`:
   - `ENABLE_LIVE_TRADING=true`
   - `LIVE_AUTONOMY_MODE=TINY_LIVE_AUTONOMOUS`
   - `LIVE_CONFIRMATION_PHRASE=I_ACCEPT_THAT_THIS_20_DOLLAR_EXPERIMENT_CAN_LOSE_MONEY`
   - `ALPACA_PAPER=false`
2. Restart cron jobs or run manually to verify
3. First trade will be tiny ($20 max total exposure)

### Cron Jobs Active
```
trading-morning-status    0 9 * * 1-5    (9AM weekdays)
trading-daily-workflow  0 10 * * 1-5   (10AM weekdays)
trading-position-monitor */15 10-16 * * 1-5 (Every 15 min, 10AM-4PM weekdays)
```

### Next Steps
1. Monitor paper trades for 1-2 weeks
2. Review performance logs in `data/journals/`
3. If profitable, consider live unlock (at your own risk)
4. Never trade more than you can afford to lose

---
Generated: 2026-07-02
Commit: 411d8cb
