# Hermes Autonomous Trading System

Phase-1 autonomous $20 experimental trading system.

**Stack:** Hermes Agent → Vibe-Trading → TradingAgents → Alpaca (paper/live) → Telegram

**Mission:** Build a fully autonomous but mandate-bounded trading research and execution system. Not a profit engine — an autonomy and safety experiment.

## Quick Reference

| Item | Value |
|------|-------|
| Max account | $20.00 |
| Max single trade loss | $1.50 (absolute $2.00) |
| Max open positions | 1 |
| Max new trades/day | 1 |
| Max new trades/week | 3 |
| Default mode | PAPER_AUTONOMOUS |
| Live mode | Locked (requires explicit user unlock) |
| Allowed underlyings | SPY, QQQ, VOO |
| Forbidden | Naked options, 0DTE, margin, crypto, futures, forex |

## Directory Structure

```
/opt/hermes-trader/
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── KILL_SWITCH.example
├── KILL_SWITCH           # Create to emergency-stop all trading
├── RUNNING.lock          # Prevents concurrent trading cycles
├── docs/                 # Architecture, runbook, integration docs
├── src/hermes_trader/    # Main Python package
│   ├── config.py         # Configuration loader
│   ├── constants.py      # Mandate constants
│   ├── models/           # TradeCandidate JSON, decisions, snapshots
│   ├── policy/           # Deterministic risk/autonomy policy engine
│   ├── execution/        # Broker adapter, order manager
│   ├── integrations/     # Vibe-Trading, TradingAgents, Alpaca clients
│   ├── research/         # Market research, thesis builder
│   ├── strategy/         # Strategy implementations
│   ├── monitoring/       # Telegram reporter, journal, audit
│   └── scheduling/       # Daily workflow, cron setup
├── tests/                # Test suite
├── logs/                 # Structured logs
├── data/                 # Research, backtests, journals
└── scripts/              # Shell scripts for cycles
```

## Modes

| Mode | Research | Paper Orders | Live Orders |
|------|----------|-------------|-------------|
| RESEARCH_ONLY | Yes | No | No |
| PAPER_AUTONOMOUS | Yes | Yes | No |
| TINY_LIVE_AUTONOMOUS | Yes | Yes | Yes (inside mandate) |
| PAUSED | Yes | No | No (exit only) |

Default: **PAPER_AUTONOMOUS**

## Core Principle

> "Autonomous inside the box. Never autonomous outside the box."

The system operates autonomously within fixed mandate limits. It cannot change risk limits, add symbols, enable margin, or unlock live mode on its own.

## See Also

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/RUNBOOK.md](docs/RUNBOOK.md)
- [docs/SECURITY.md](docs/SECURITY.md)