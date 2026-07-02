# Hermes Trading System — v0.5.0 Aggressive Configuration

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HERMES AGENT v0.18.0                     │
│                    SOUL.md: Trading Identity                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Vibe-Trading │  │ TradingAgents│  │   Technical   │     │
│  │   AI (LLM)   │  │  (4 rounds)  │  │   Scanner     │     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         │                 │                  │               │
│         └────────┬────────┴──────────────────┘               │
│                  ▼                                           │
│         ┌────────────────┐                                   │
│         │  Signal Analyzer│  (Confluence Scoring)            │
│         │  100-point max  │                                  │
│         └────────┬───────┘                                   │
│                  ▼                                           │
│         ┌────────────────┐                                   │
│         │  Policy Engine  │  (Risk Gates)                    │
│         │  Kill Switch    │                                  │
│         │  Loss Limits    │                                  │
│         └────────┬───────┘                                   │
│                  ▼                                           │
│         ┌────────────────┐                                   │
│         │  Risk Manager   │  (Position Sizing)               │
│         │  Kelly Criterion│                                   │
│         │  Correlation    │                                  │
│         └────────┬───────┘                                   │
│                  ▼                                           │
│         ┌────────────────┐                                   │
│         │  Alpaca Broker  │  (Execution)                     │
│         │  Paper / Live   │                                  │
│         └────────┬───────┘                                   │
│                  ▼                                           │
│         ┌────────────────┐                                   │
│         │ Position Monitor│  (Trailing Stops)                │
│         │  Every 15 min   │                                  │
│         └────────────────┘                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Configuration Summary

### Account: $50

| Parameter | Value | Notes |
|---|---|---|
| Max Experiment Capital | $50 | Full account |
| Max Single Trade Loss | $2.00 | 4% of account |
| Max Daily Loss | $4.00 | 8% of account |
| Max Weekly Loss | $10.00 | 20% of account |
| Max Monthly Loss | $20.00 | 40% of account |
| Cash Reserve | $5.00 | Emergency buffer |

### Position Sizing

| Parameter | Value | Notes |
|---|---|---|
| Max Open Positions | 3 | Diversification |
| Max Trades Per Day | 3 | Active trading |
| Max Trades Per Week | 10 | Consistent activity |
| Max Position Notional | $25.00 | 50% of account |
| Max Equity Order | $15.00 | 30% of account |

### Aggressive Features

| Feature | Setting | Notes |
|---|---|---|
| Pyramid Scaling | Enabled | Add to winners at 2%/4% |
| Trailing Stop Initial | 1.5% | Tight initial stop |
| Trailing Stop Trail | 0.8% | Lock in profits |
| Trailing Activation | 2.5% | Start trailing after 2.5% profit |
| Profit Taking | 50% at 4% | Take half off at target |
| Backtest Validation | Disabled | Speed over validation |

### Research Stack

| Component | Setting | Notes |
|---|---|---|
| TradingAgents Debate | 4 rounds | Maximum consensus |
| TradingAgents Risk | 4 rounds | Deep risk analysis |
| LLM Provider | CMDDD | Mimo v2.5 Pro |
| SPY Breakout Size | $3,000 | Boost on breakout |
| Backtest Min Sharpe | 1.2 | Quality gate |
| Backtest Min Win Rate | 55% | Quality gate |

### Watchlist

**ETFs:** SPY, QQQ, VOO, DIA, IWM
**Mega Caps:** AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA, META

### Options

| Parameter | Value |
|---|---|
| Long Calls | Enabled |
| Long Put | Enabled |
| Debit Spreads | Enabled (Paper + Live) |
| Max Premium | $5.00 |
| Max Contracts | 2 |
| Min Days to Expiry | 3 |
| Max Days to Expiry | 30 |

### Scoring System

| Dimension | Max Points | What It Measures |
|---|---|---|
| Evidence | 20 | Research quality |
| Committee | 20 | Multi-agent consensus |
| Liquidity | 15 | Volume, spread, OI |
| Risk | 15 | Loss/reward ratio |
| Operational | 10 | Order feasibility |
| Technical | 20 | SMA/EMA/RSI/MACD |
| **Total** | **100** | |

**Thresholds:**
- ≥80: Full position (live eligible)
- ≥65: Paper trading
- <65: No trade

## Cron Schedule (Weekdays)

| Time | Job | Skills |
|---|---|---|
| 9:00 AM | Morning Status | hermes-trader, trading-deep-research, trading-signal-analyzer |
| 10:00 AM | Daily Workflow | hermes-trader, trading-deep-research, trading-signal-analyzer, trading-risk-manager |
| Every 15 min | Position Monitor | hermes-trader, trading-risk-manager, trading-journal-analytics |
| 4:30 PM | After-Hours Review | hermes-trader, trading-journal-analytics |

## Token-Saving Stack

| Tool | Purpose | Savings |
|---|---|---|
| Caveman | Agent output compression | ~75% output tokens |
| sqz | Context dedup + compression | 24.7% avg, 92% dedup |
| Headroom | Proxy + content-aware compress | 60-95% tokens |
| context-mode | 98% context reduction MCP | 98% context |

## Live Trading Unlock

To enable live trading with real money:

```bash
# Edit /opt/hermes-trader/.env and set:
ENABLE_LIVE_TRADING=true
LIVE_AUTONOMY_MODE=TINY_LIVE_AUTONOMOUS
LIVE_CONFIRMATION_PHRASE=I_ACCEPT_THAT_THIS_20_DOLLAR_EXPERIMENT_CAN_LOSE_MONEY
ALPACA_PAPER=false
```

**ALL 4 conditions must be met.**

## Emergency Controls

```bash
# Activate kill switch (stops ALL trading)
hermes-trader kill-on

# Deactivate kill switch
hermes-trader kill-off

# Manual kill switch
touch /opt/hermes-trader/KILL_SWITCH
rm /opt/hermes-trader/KILL_SWITCH
```

## Files

| Path | Purpose |
|---|---|
| `/opt/hermes-trader/.env` | Secrets and config |
| `/opt/hermes-trader/src/hermes_trader/constants.py` | Hard limits |
| `/opt/hermes-trader/src/hermes_trader/config.py` | Config loader |
| `/opt/hermes-trader/data/journals/decisions.jsonl` | Decision log |
| `/opt/hermes-trader/data/journals/paper_orders.jsonl` | Order log |
| `~/.hermes/SOUL.md` | Trading identity |
| `~/.hermes/skills/trading/` | Trading skills |

## Version History

- **v0.5.0** (2026-07-02): Aggressive $50 config, CMDDD Mimo v2.5 Pro, 4 new skills, SOUL.md
- **v0.4.0** (2026-07-02): Enhanced workflow, technical analysis, backtest validation
- **v0.3.0** (2026-07-02): Vendor repos installed, full integration chain
- **v0.2.0** (2026-07-02): Complete trading system implementation
- **v0.1.0** (2026-07-02): Initial setup
