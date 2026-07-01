# ARCHITECTURE.md — Hermes Autonomous Trading System

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Telegram                              │
│              (User interface, reports, alerts)               │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                     Hermes Agent                             │
│         Main orchestrator, scheduler, workflow manager       │
└───┬───────────────────────┬──────────────────┬──────────────┘
    │                       │                  │
┌───▼──────────┐  ┌─────────▼──────┐  ┌───────▼──────────────┐
│ Vibe-Trading │  │ TradingAgents  │  │ Scheduling/Cron       │
│ Research &   │  │ Debate &       │  │ Daily workflow        │
│ Backtesting  │  │ Committee      │  │ triggers              │
└───┬──────────┘  └─────────┬──────┘  └──────────────────────┘
    │                       │
    │  Research output      │  Committee decision
    │                       │
┌───▼───────────────────────▼────────────────────────────────┐
│              TradeCandidate JSON                            │
│    Structured trade proposal from research + debate         │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│         Deterministic Policy Engine                         │
│     Code-based validation, not LLM-based                   │
│     Checks: risk, liquidity, mode, limits, kill switch      │
│     Output: APPROVED / REJECTED / PAUSED / NO_TRADE        │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│              Alpaca Broker Adapter                          │
│     Paper trading first, live only if globally unlocked     │
│     Order placement, fill monitoring, exit management       │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│         Monitoring & Journaling                             │
│     Telegram reports, audit logs, trade journals, P&L       │
└────────────────────────────────────────────────────────────┘
```

## Data Flow

1. **Hermes** triggers daily workflow cycle
2. **Vibe-Trading** researches market, generates candidates, runs backtests
3. **TradingAgents** committee debates each candidate
4. Candidate(s) converted to **TradeCandidate JSON**
5. **Policy Engine** deterministically validates against mandate
6. If APPROVED → **Alpaca** adapter places order (paper or live)
7. **Monitor** tracks fills, exits, P&L
8. **Telegram** reports results

## Layer Responsibilities

### Hermes Agent (Orchestrator)
- Reads mode from env/config
- Manages daily schedule
- Coordinates Vibe-Trading and TradingAgents calls
- Converts research output to structured JSON
- Runs policy engine
- Triggers execution
- Generates Telegram reports
- Manages Hermes skill for system operation

### Vibe-Trading (Research)
- Market regime analysis
- SPY/QQQ/VOO scanning
- Options analysis
- Backtesting when possible
- Factor analysis
- Strategy candidate generation
- Evidence pack creation

### TradingAgents (Committee)
- Independent bull/bear/analyst debate
- Risk manager critique
- Portfolio manager final recommendation
- Confidence score
- No-trade comparison

### Policy Engine (Deterministic Code)
- NOT an LLM prompt — pure Python validation
- Validates every order against mandate
- Checks: risk limits, liquidity, expiration, mode, kill switch, symbol allowlist, duplicates
- Only place where trade is approved/rejected

### Alpaca Adapter (Broker)
- Paper trading primary
- Live trading only if global unlock configured
- Account/position/order queries
- Market data and quotes
- Order placement through controlled wrapper

### Monitoring & Journaling
- Structured audit logs
- Trade journals (JSONL)
- Decision journals
- No-trade journals
- P&L tracking
- Telegram reporting per cycle

## Safety Boundaries

The system has three concentric safety layers:

1. **Mode gates** — RESEARCH_ONLY / PAPER / LIVE modes prevent escalation
2. **Live unlock gate** — Multiple env vars + confirmation phrase required
3. **Policy engine** — Final deterministic gate before any order

The agent CANNOT change mode, unlock live trading, or bypass the policy engine.