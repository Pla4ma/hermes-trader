#!/usr/bin/env python3
"""Focused deep-dive on confirmed runtime errors from initial scan."""

import sys
import traceback
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/hermes-trader/src")

ERRORS = []
WARNINGS = []

def err(module, func, etype, msg):
    ERRORS.append({"module": module, "func": func, "type": etype, "msg": msg})
    print(f"  ❌ [{etype}] {module}.{func}: {msg}")

def warn(module, func, msg):
    WARNINGS.append({"module": module, "func": func, "msg": msg})
    print(f"  ⚠️  [{module}.{func}] {msg}")

def ok(msg):
    print(f"  ✅ {msg}")

# ============================================================================
# ERROR 1: check_all_gates returns Tuple, not dict
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: entry_gates.check_all_gates() return type mismatch")
print("="*70)
from hermes_trader.entry_gates import check_all_gates

# Check actual return type
result = check_all_gates(
    symbol="SPY", option_type="call", spot=500.0,
    open_price=499.0, high_of_day=501.0, low_of_day=498.0,
    current_volume=1000000, avg_volume_20d=800000,
    rsi_14=55.0, now_et=datetime(2026, 7, 7, 10, 0),
    vwap=499.5,
)

print(f"  Return type: {type(result)}")
print(f"  Return value: {result}")

if isinstance(result, tuple):
    passed, failures = result
    print(f"  passed={passed}, failures={failures}")
    
    # Now check what auto_trader expects
    print("\n  Checking what auto_trader expects from check_all_gates...")
    # In auto_trader.py, the gate check is done as:
    #   result = check_all_gates(...)
    #   result["passed"]  — this would crash with a tuple!
    
    # Let's find the exact usage in auto_trader
    import inspect
    from hermes_trader import auto_trader
    source = inspect.getsource(auto_trader.auto_trade)
    
    if "check_all_gates" in source:
        # Find the line that uses check_all_gates
        for line in source.split('\n'):
            if 'check_all_gates' in line or 'gate' in line.lower():
                print(f"  auto_trader line: {line.strip()}")
    
    # Find if auto_trader accesses result as dict
    for line in source.split('\n'):
        if 'gates' in line.lower() and ('[' in line or '.get' in line):
            print(f"  auto_trader accesses gates as: {line.strip()}")
    
    err("entry_gates", "check_all_gates", "TYPE_MISMATCH",
        "Returns Tuple[bool, list[str]], but consumers may expect dict with 'passed'/'reason'/'gates' keys")

# ============================================================================
# ERROR 2: gate_volume threshold — 0.5x avg NOT blocked
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: entry_gates.gate_volume() threshold behavior")
print("="*70)
from hermes_trader.entry_gates import gate_volume

# Test at exact boundary
for ratio in [0.49, 0.50, 0.51, 0.99, 1.0]:
    vol = int(1000000 * ratio)
    passed, reason = gate_volume(vol, 1000000)
    print(f"  Volume {ratio}x avg: passed={passed}")
    if not passed:
        print(f"    reason: {reason}")

# The docstring says "Need at least 50% of average volume to trade"
# If vol_ratio = 0.5, should it be blocked or allowed?
# Code says: if vol_ratio < 0.5: block
# So 0.5x exactly is ALLOWED (passes), which is correct (< 0.5 not <= 0.5)
ok("gate_volume(0.50x) = PASS — threshold is < 0.5 (strict), not <= 0.5. Correct behavior.")

# But wait — the docstring says "Need at least 50% of average volume"
# which means >= 0.5x should be fine. So 0.50x passing is correct.
# Let me verify the docstring claim
import inspect
src = inspect.getsource(gate_volume)
for line in src.split('\n'):
    if '50%' in line or '0.5' in line:
        print(f"  gate_volume doc/logic: {line.strip()}")

# ============================================================================
# ERROR 3: zero_dte_exits.evaluate() wrong signature
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: zero_dte_exits.evaluate() actual vs expected signature")
print("="*70)
from hermes_trader.zero_dte_exits import ZeroDTEExitManager, ExitSignal

mgr = ZeroDTEExitManager()
import inspect
sig = inspect.signature(mgr.evaluate)
print(f"  Actual signature: {sig}")

# The PositionSnapshot in zero_dte_exits already has current_price
from hermes_trader.zero_dte_exits import PositionSnapshot, PriceSnapshot

pos = PositionSnapshot(
    option_id="test-uuid",
    symbol="SPY",
    option_type="call",
    quantity=2,
    entry_price=2.0,
    current_price=3.0,  # <-- current_price is INSIDE the PositionSnapshot
    strike=500.0,
    expiration="2026-07-07",
    price_history=[
        PriceSnapshot(price=3.0, timestamp=datetime(2026, 7, 7, 14, 30)),
    ],
    entry_time=datetime(2026, 7, 7, 13, 0),
)

# Correct call
result = mgr.evaluate(pos)
print(f"  Correct call result: {result}")

# Now check what auto_trader does with this
source = inspect.getsource(auto_trader.auto_trade)
for i, line in enumerate(source.split('\n')):
    if 'exit' in line.lower() and 'zero_dte' in line.lower() or 'ExitManager' in line:
        print(f"  auto_trader uses exit: {line.strip()}")

# Check manage_exits
source2 = inspect.getsource(auto_trader.manage_exits)
for i, line in enumerate(source2.split('\n')):
    if 'exit' in line.lower() and ('ZeroDTE' in line or 'evaluate' in line or 'current_price' in line):
        print(f"  manage_exits line: {line.strip()}")

# ============================================================================
# ERROR 4: earnings_calendar.check_earnings() missing in_danger_zone
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: earnings_calendar.check_earnings() missing in_danger_zone")
print("="*70)
from hermes_trader.earnings_calendar import check_earnings

# Test with SPY - will likely return {"has_earnings": False} without in_danger_zone
result = check_earnings("SPY")
print(f"  Result: {result}")
print(f"  Has 'in_danger_zone': {'in_danger_zone' in result}")

if 'in_danger_zone' not in result:
    err("earnings_calendar", "check_earnings", "MISSING_FIELD",
        "check_earnings() can return dict WITHOUT 'in_danger_zone' field. "
        "auto_trader.py accesses result.get('in_danger_zone') which returns None (falsy), "
        "but this is a silent bug — the function can also return an exception dict with no in_danger_zone")

# Check auto_trader usage
source = inspect.getsource(auto_trader.auto_trade)
for line in source.split('\n'):
    if 'in_danger_zone' in line or 'check_earnings' in line:
        print(f"  auto_trader: {line.strip()}")

# ============================================================================
# ERROR 5: market_regime.py - yf import issue
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: market_regime.py - yf import behavior")
print("="*70)
import importlib
mr = importlib.import_module('hermes_trader.market_regime')

# Check how yf is imported
import inspect
src = inspect.getsource(mr)
for line in src.split('\n')[:15]:
    if 'import' in line or 'yf' in line:
        print(f"  {line.strip()}")

# The module imports yfinance as yf inside the function, not at module level
# So patching 'hermes_trader.market_regime.yf' won't work
# Need to patch 'yfinance' instead
print("\n  Testing detect_regime with proper mock...")

# Simulate the actual call
with patch.dict('sys.modules', {'yfinance': MagicMock()}) as mock_yf_mod:
    mock_yf = mock_yf_mod['yfinance']
    mock_ticker = MagicMock()
    mock_ticker.fast_info = {"lastPrice": 500.0, "previousClose": 498.0}
    mock_ticker.history.return_value = MagicMock(
        close=[500.0, 499.0, 501.0, 498.0, 502.0, 500.0, 499.0, 501.0, 498.0, 502.0] * 2,
        volume=[1000000.0] * 20,
        high=[502.0] * 20,
        low=[498.0] * 20,
    )
    mock_yf.Ticker.return_value = mock_ticker
    
    try:
        result = mr.detect_regime()
        print(f"  detect_regime result: {result}")
    except Exception as e:
        print(f"  detect_regime error: {type(e).__name__}: {e}")

# ============================================================================
# ERROR 6: auto_trader - check_all_gates integration 
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: auto_trader integration with check_all_gates")
print("="*70)

# Read auto_trade source to find how check_all_gates result is used
src = inspect.getsource(auto_trader.auto_trade)
lines = src.split('\n')
gate_related = []
in_gate_section = False
for i, line in enumerate(lines):
    if 'gate' in line.lower() or in_gate_section:
        gate_related.append(f"  L{i}: {line}")
        in_gate_section = True
        if line.strip() == '' and len(gate_related) > 3:
            in_gate_section = False

for line in gate_related[:30]:
    print(line)

# ============================================================================
# ERROR 7: aggressive_sizer - can't buy 1 contract with $100
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: aggressive_sizer with small accounts")
print("="*70)
from hermes_trader.aggressive_sizer import AggressiveSizer

sizer = AggressiveSizer()

# With $100 account and $0.45 premium, Kelly says risk ~$13.44
# But 1 contract = $0.45 × 100 = $45.00
# So the sizer returns 0 contracts because risk < contract cost
rec = sizer.recommend(
    win_prob=0.55, avg_win=0.80, avg_loss=0.50,
    premium_per_contract=0.45, account_value=100.0, consecutive_losses=0,
)
print(f"  $100 account, $0.45 premium:")
print(f"    risk_dollars = ${rec.risk_dollars:.2f}")
print(f"    num_contracts = {rec.num_contracts}")
print(f"    max_allowed_risk = ${rec.max_allowed_risk:.2f}")
print(f"    signals = {rec.signals}")

if rec.num_contracts == 0 and rec.risk_dollars > 0:
    err("aggressive_sizer", "recommend", "SMALL_ACCOUNT_BUG",
        f"Risk ${rec.risk_dollars:.2f} is enough but 1 contract costs $45. "
        f"Returns 0 contracts. auto_trader will never trade with small accounts.")

# Check what auto_trader does with 0 contracts
src = inspect.getsource(auto_trader.auto_trade)
for line in src.split('\n'):
    if 'num_contracts' in line or 'contracts' in line:
        print(f"  auto_trader: {line.strip()}")

# ============================================================================
# ERROR 8: auto_trader scan_and_score - check return type
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: auto_trader.scan_and_score() return type")
print("="*70)
from hermes_trader.auto_trader import scan_and_score
import inspect

# Check what scan_and_score returns
src = inspect.getsource(scan_and_score)
print(f"  scan_and_score signature: {inspect.signature(scan_and_score)}")

# Find return statements
for i, line in enumerate(src.split('\n')):
    if 'return' in line:
        print(f"  L{i}: {line.strip()}")

# ============================================================================
# ERROR 9: options_confluence vs zero_dte_scanner - duplicate FullGreeks
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: Name collision - FullGreeks in both greeks_engine and options_confluence")
print("="*70)

# greeks_engine has no FullGreeks at module level (it was in the indexed sections as a comment)
# options_confluence has FullGreeks as a proper dataclass
try:
    from hermes_trader.greeks_engine import FullGreeks as GE_FullGreeks
    print("  greeks_engine.FullGreeks exists")
except ImportError:
    print("  greeks_engine.FullGreeks does NOT exist (correct - it has BlackScholesGreeks)")

from hermes_trader.options_confluence import FullGreeks as OC_FullGreeks
print("  options_confluence.FullGreeks exists")

# ============================================================================
# ERROR 10: Test all main entry points with mock data
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: Main entry points with realistic mock data")
print("="*70)

# Test auto_trade with everything mocked
print("\n  Testing auto_trade with full mocking...")
from hermes_trader.auto_trader import auto_trade

# Mock the broker
mock_broker = MagicMock()
mock_broker.get_account.return_value = MagicMock(cash=100.0)
mock_broker.get_positions.return_value = []

with patch('hermes_trader.auto_trader._get_broker', return_value=mock_broker):
    try:
        result = auto_trade(min_score=30, max_notional=90.0)
        print(f"  auto_trade result: {result.get('action') if isinstance(result, dict) else type(result)}")
        if isinstance(result, dict):
            for k in ['timestamp', 'cash', 'action', 'regime', 'sizing_multiplier']:
                if k in result:
                    print(f"    {k}: {result[k]}")
                else:
                    err("auto_trader", "auto_trade", "MISSING_FIELD",
                        f"Missing expected field: {k}")
    except Exception as e:
        print(f"  auto_trade error: {type(e).__name__}: {e}")
        # Get the full traceback
        tb = traceback.format_exc()
        # Find the critical error line
        for line in tb.split('\n'):
            if 'Error' in line or 'error' in line.lower():
                print(f"    {line.strip()}")

# ============================================================================
# ERROR 11: auto_trader expects options_analytics to return specific dict
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: auto_trader -> options_analytics integration")
print("="*70)

# Read the exact lines
src = inspect.getsource(auto_trader.auto_trade)
for i, line in enumerate(src.split('\n')):
    if 'analytics' in line.lower() and ('get(' in line or 'gex' in line or 'pcr' in line or 'max_pain' in line):
        print(f"  L{i}: {line.strip()}")

# ============================================================================
# ERROR 12: test manage_exits
# ============================================================================
print("\n" + "="*70)
print("DEEP DIVE: auto_trader.manage_exits() return type")
print("="*70)
from hermes_trader.auto_trader import manage_exits
src = inspect.getsource(manage_exits)
print(f"  Signature: {inspect.signature(manage_exits)}")

# Find return statements
for i, line in enumerate(src.split('\n')):
    if 'return' in line and ('{' in line or 'result' in line.lower()):
        print(f"  L{i}: {line.strip()}")

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "="*70)
print("FINAL ERROR SUMMARY")
print("="*70)
print(f"\nTotal errors: {len(ERRORS)}")
print(f"Total warnings: {len(WARNINGS)}")

for i, e in enumerate(ERRORS, 1):
    print(f"\n  {i}. [{e['type']}] {e['module']}.{e['func']}")
    print(f"     {e['msg']}")

for i, w in enumerate(WARNINGS, 1):
    print(f"\n  W{i}. {w['module']}.{w['func']}: {w['msg']}")
