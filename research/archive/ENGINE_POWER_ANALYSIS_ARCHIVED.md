# ENGINE_POWER_ANALYSIS.md — Archived

**Date:** 2026-07-03
**Reason:** Spec Section 5 — Fabricated metrics

## Why Archived
- Headline stats (84-86% win rate, Sharpe 12-13) not reproducible from codebase
- No backtest path touches real options fills (only yfinance equity bars)
- "Engine power %" is not a tracked metric

## Replacement Metrics (per spec)
- Gate rejection reasons (from risk_gate.py logs)
- Realized paper P&L per strategy
- Backtest sample sizes (once Section 3 lands)

## Original Location
`research/ENGINE_POWER_ANALYSIS.md`
