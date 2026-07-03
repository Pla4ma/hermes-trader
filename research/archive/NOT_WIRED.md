# 0DTE Engines — Archived (Not Wired)

**Date:** 2026-07-03
**Reason:** Spec Section 1 — STOP-SHIP audit

## Files
- `zero_dte.py` (148 lines) — v1 scan/find/execute
- `zero_dte_v2.py` (351 lines) — ORB, Momentum, VWAP, GEX strategies
- `zero_dte_ultimate.py` (399 lines) — 4 strategies, 7 exit rules, risk management

## Why Archived
- `constants.py` sets `ALLOW_0DTE = False`
- `README.md` forbids 0DTE
- **Zero imports** found in workflow.py, enhanced_daily_workflow.py, auto_trader.py, or any other module
- These engines were fully implemented but never connected to the execution path

## Status
- NOT wired into risk_gate.py
- NOT referenced in any execution workflow
- May contain reusable entry-timing logic for future 0DTE implementation

## To Re-enable (requires Jonathan's explicit sign-off)
1. Set `ALLOW_0DTE = True` in constants.py
2. Wire into risk_gate.py approval flow
3. Add to workflow execution path
4. Update README.md
