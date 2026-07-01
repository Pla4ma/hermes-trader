# PHASE_2_NAUTILUS_PLAN.md

## What is NautilusTrader?

[NautilusTrader](https://github.com/nautechsystems/nautilus_trader) is a high-performance, event-driven algorithmic trading platform in Python. It provides:

- Event-driven architecture for real-time trading
- Backtesting engine with venue simulation
- Live trading engine with venue adapters
- Risk management framework
- Order book and market data handling
- Multi-venue support

## Why NautilusTrader is Deferred (Phase 1)

1. **Account is only $20** — The infrastructure overhead of a full event-driven engine far exceeds what a tiny experimental account needs.

2. **Goal is workflow proof, not production trading** — Phase 1 must prove that the autonomous Hermes → Vibe-Trading → TradingAgents → Alpaca loop works reliably before adding heavy infrastructure.

3. **Complexity risk** — NautilusTrader introduces threading, event loops, venue adapters, and serialization complexity. Adding it before the basic research/committee/policy/execution loop is stable would multiply failure modes.

4. **Vibe-Trading + TradingAgents are the approved brain layers** — Phase 1 uses these as the research and decision layers. NautilusTrader would replace the execution layer, not the brain.

5. **Alpaca paper/tiny-live is sufficient** — The current Alpaca adapter (MCP or alpaca-py) handles paper and tiny live orders for a $20 account with a single position.

## What Evidence is Required Before Adding NautilusTrader

- [ ] At least 20 autonomous paper cycles completed
- [ ] At least 10 actual paper trade decisions logged (or meaningful no-trade decisions)
- [ ] Strategy candidates show stable behavior
- [ ] Vibe-Trading/TradingAgents workflow produces structured outputs reliably
- [ ] Broker adapter and risk gate work without critical errors
- [ ] Trade journal exists with structured entries
- [ ] Paper/live fill gap is measurable
- [ ] User wants production-grade simulation/live parity
- [ ] No critical safety failures in Phase 1

## How NautilusTrader Would Fit (Phase 2)

```
Current Phase 1:
  Hermes → Vibe-Trading → TradingAgents → Policy Engine → Alpaca Adapter

Phase 2 with NautilusTrader:
  Hermes → Vibe-Trading → TradingAgents → Policy Engine
                                              ↓
                                     NautilusTrader Execution
                                     ├── Backtest Engine (venue simulation)
                                     ├── Live Trading Engine
                                     ├── Risk Management (supplements policy engine)
                                     └── Alpaca Venue Adapter
```

## Problems NautilusTrader Would Solve

- **Better backtesting realism** — venue-style simulation with order book depth
- **Production-grade execution** — event-driven, no polling
- **Live/paper parity** — same code paths for both modes
- **Multi-venue readiness** — if we ever add IBKR, etc.
- **Risk framework** — built-in position/risk management alongside our deterministic engine
- **Data pipeline** — structured market data ingestion

## Problems NautilusTrader Would NOT Solve

- Strategy quality (that's Vibe-Trading's job)
- Decision quality (that's TradingAgents' job)
- $20 account viability (that's a capital constraint, not a software constraint)
- LLM hallucination risk (that's a Hermes/Vibe-Trading/TradingAgents concern)
- The fundamental question of whether autonomous LLM-driven trading can be useful

## Trigger Conditions Checklist

```
[ ] 20+ autonomous paper cycles
[ ] 10+ paper trade decisions or meaningful no-trade decisions
[ ] Stable strategy candidates
[ ] Reliable Vibe-Trading integration
[ ] Reliable TradingAgents integration
[ ] Stable policy engine
[ ] Stable broker adapter
[ ] Stable Telegram reporting
[ ] No critical safety failures
[ ] Evidence that better simulation/live parity is needed
[ ] User explicitly requests Phase 2
```

**Until ALL triggers are met: Do not install, configure, or reference NautilusTrader for execution purposes.**