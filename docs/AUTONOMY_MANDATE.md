# AUTONOMY_MANDATE.md

## Core Principle

> **"Autonomous inside the box. Never autonomous outside the box."**

The Hermes agent may operate autonomously within the fixed mandate limits defined below.

## What the Agent May Autonomously Do

- Research markets via Vibe-Trading
- Call TradingAgents for committee debate
- Generate trade candidate JSON
- Run deterministic policy engine
- Place **paper** orders (PAPER_AUTONOMOUS mode)
- Place **tiny live** orders (only if globally unlocked by user + TINY_LIVE_AUTONOMOUS mode)
- Close positions
- Cancel orders
- Journal trades
- Schedule tasks
- Send Telegram reports
- Improve research prompts/workflows
- Refine strategy selection based on logs
- Choose NO_TRADE when evidence is weak

## What the Agent May NOT Autonomously Do

- Increase account risk limits
- Increase max trade size
- Enable margin
- Enable naked options
- Enable 0DTE
- Trade outside allowed symbols
- Add new underlyings to allowlist
- Expose secrets
- Bypass policy engine validation
- Edit the live-unlock mechanism
- Remove the kill switch
- Change broker credentials
- Loosen risk rules
- Rewrite this mandate into something riskier
- Install NautilusTrader in phase 1

## Hard Limits (Env-Configurable, Agent Cannot Change)

| Limit | Default Value |
|-------|--------------|
| Max account | $20.00 |
| Max single trade loss | $1.50 (absolute $2.00) |
| Max daily loss | $1.50 |
| Max weekly loss | $3.00 |
| Max monthly loss | $6.00 |
| Max open positions | 1 |
| Max new trades/day | 1 |
| Max new trades/week | 3 |
| Max consecutive losses | 3 |
| Max option premium | $2.00 |

## Mode Hierarchy

1. RESEARCH_ONLY — no orders at all
2. PAPER_AUTONOMOUS — paper orders only (DEFAULT)
3. TINY_LIVE_AUTONOMOUS — live orders inside mandate (requires user unlock)
4. PAUSED — research only, exit-only for open positions

Agent defaults to PAPER_AUTONOMOUS when uncertain.

## Live Trading Unlock Requirements (ALL must be true)

```
ALPACA_PAPER=false
ENABLE_LIVE_TRADING=true
LIVE_AUTONOMY_MODE=TINY_LIVE_AUTONOMOUS
LIVE_CONFIRMATION_PHRASE=I_ACCEPT_THAT_THIS_20_DOLLAR_EXPERIMENT_CAN_LOSE_MONEY
```

Plus: policy engine active, kill switch absent, tests passed, account within mandate.

Agent CANNOT set any of these. Only the user can.