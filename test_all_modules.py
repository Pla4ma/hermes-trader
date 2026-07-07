#!/usr/bin/env python3
"""Comprehensive runtime error finder for hermes-trader modules.

Tests EVERY module independently for:
- Import errors / missing dependencies
- Wrong return types
- Missing fields in return dicts
- Division by zero
- None/NaN handling
- Type errors / crashes
"""
import sys
import os
import traceback
import importlib
from datetime import datetime, date, time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Setup path
sys.path.insert(0, "/opt/hermes-trader/src")

ERRORS = []
WARNINGS = []

def record_error(module, func, error_type, message, tb=None):
    """Record an error found during testing."""
    ERRORS.append({
        "module": module,
        "function": func,
        "error_type": error_type,
        "message": message,
        "traceback": tb,
    })
    print(f"  ❌ [{error_type}] {module}.{func}: {message}")

def record_warning(module, func, message):
    """Record a warning found during testing."""
    WARNINGS.append({
        "module": module,
        "function": func,
        "message": message,
    })
    print(f"  ⚠️  [{module}.{func}] {message}")

def check_return_type(module, func, result, expected_type, fields=None):
    """Check that a return value matches the expected type and has required fields."""
    if result is None:
        record_error(module, func, "RETURN_TYPE", f"Returns None, expected {expected_type.__name__}")
        return False
    if not isinstance(result, expected_type):
        record_error(module, func, "RETURN_TYPE", f"Returns {type(result).__name__}, expected {expected_type.__name__}")
        return False
    if fields and isinstance(result, dict):
        missing = [f for f in fields if f not in result]
        if missing:
            record_error(module, func, "MISSING_FIELDS", f"Missing fields: {missing}")
            return False
    return True

def safe_call(func, *args, **kwargs):
    """Call a function safely, catching all exceptions."""
    try:
        return True, func(*args, **kwargs)
    except Exception as e:
        return False, (type(e).__name__, str(e), traceback.format_exc())

# ============================================================================
# MODULE 1: config.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: config.py")
print("="*70)
try:
    from hermes_trader.config import config
    print("  ✅ Import successful")
    
    # Check config has expected attributes
    if not hasattr(config, 'project_root'):
        record_error("config", "config", "MISSING_ATTR", "config.project_root not defined")
    else:
        print(f"  ✅ config.project_root = {config.project_root}")
        
    # Test that config is a singleton
    from hermes_trader.config import config as config2
    if config is not config2:
        record_error("config", "config", "SINGLETON", "config is not a singleton")
    else:
        print("  ✅ config is singleton")
        
except Exception as e:
    record_error("config", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 2: constants.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: constants.py")
print("="*70)
try:
    from hermes_trader import constants
    print("  ✅ Import successful")
    # Check key constants exist
    for attr in ['ACCOUNT_MANDATE', 'TRADE_LIMITS', 'ASSETS', 'OPTIONS', 'ZERO_DTE_OPTIONS']:
        if hasattr(constants, attr):
            print(f"  ✅ constants.{attr} exists")
        else:
            record_warning("constants", "constants", f"Missing constant: {attr}")
except Exception as e:
    record_error("constants", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 3: models/
# ============================================================================
print("\n" + "="*70)
print("TESTING: models/")
print("="*70)
try:
    from hermes_trader.models.order_request import OrderRequest
    from hermes_trader.models.position_snapshot import AccountSnapshot, MarketSnapshot, PositionSnapshot, RiskSnapshot
    from hermes_trader.models.trade_candidate import TradeCandidate
    from hermes_trader.models.trade_decision import TradeDecision
    print("  ✅ All model imports successful")
except Exception as e:
    record_error("models", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 4: market_regime.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: market_regime.py")
print("="*70)
try:
    from hermes_trader.market_regime import detect_regime
    print("  ✅ Import successful")
    
    # Test detect_regime() - this normally calls yfinance, mock it
    with patch('hermes_trader.market_regime.yf', MagicMock()) as mock_yf:
        # Mock the ticker to return some data
        mock_ticker = MagicMock()
        mock_ticker.fast_info = {"lastPrice": 500.0, "previousClose": 498.0}
        mock_ticker.history = MagicMock(return_value=MagicMock(
            close=[500, 499, 501, 498, 502],
            volume=[1000000]*5,
            high=[502, 500, 503, 500, 504],
            low=[498, 497, 499, 496, 500]
        ))
        mock_yf.Ticker.return_value = mock_ticker
        
        ok, result = safe_call(detect_regime)
        if ok:
            if not check_return_type("market_regime", "detect_regime", result, dict,
                                    ["regime", "sizing_multiplier"]):
                pass
            else:
                print(f"  ✅ detect_regime() returned valid dict")
                print(f"     regime={result.get('regime')}, sizing_mult={result.get('sizing_multiplier')}")
        else:
            record_error("market_regime", "detect_regime", "CRASH", 
                        f"{result[0]}: {result[1]}")
except Exception as e:
    record_error("market_regime", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 5: aggressive_sizer.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: aggressive_sizer.py")
print("="*70)
try:
    from hermes_trader.aggressive_sizer import AggressiveSizer, SizeRecommendation
    print("  ✅ Import successful")
    
    sizer = AggressiveSizer()
    
    # Test 1: Normal recommend() call
    ok, result = safe_call(
        sizer.recommend,
        win_prob=0.55,
        avg_win=0.80,
        avg_loss=0.50,
        premium_per_contract=0.45,
        account_value=100.0,
        consecutive_losses=0,
    )
    if ok:
        if not isinstance(result, SizeRecommendation):
            record_error("aggressive_sizer", "recommend", "RETURN_TYPE", 
                        f"Returns {type(result).__name__}, expected SizeRecommendation")
        else:
            # Check required fields
            for field_name in ['risk_dollars', 'num_contracts', 'position_value', 
                              'kelly_fraction', 'kelly_half', 'base_risk_pct',
                              'theta_multiplier', 'loss_cooldown_multiplier',
                              'adjusted_risk_pct', 'max_allowed_risk', 'signals']:
                if not hasattr(result, field_name):
                    record_error("aggressive_sizer", "recommend", "MISSING_FIELD", 
                                f"Missing field: {field_name}")
                else:
                    print(f"  ✅ {field_name} = {getattr(result, field_name)}")
            # Verify num_contracts is integer
            if not isinstance(result.num_contracts, int):
                record_error("aggressive_sizer", "recommend", "TYPE_ERROR",
                            f"num_contracts is {type(result.num_contracts).__name__}, expected int")
    else:
        record_error("aggressive_sizer", "recommend", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test 2: Edge case - zero account value
    ok, result = safe_call(
        sizer.recommend,
        win_prob=0.55,
        avg_win=0.80,
        avg_loss=0.50,
        premium_per_contract=0.45,
        account_value=0.0,
        consecutive_losses=0,
    )
    if ok:
        if result.num_contracts != 0:
            record_warning("aggressive_sizer", "recommend", 
                         f"Zero account value returned {result.num_contracts} contracts (should be 0)")
        else:
            print("  ✅ Zero account returns 0 contracts")
    else:
        record_error("aggressive_sizer", "recommend_zero_acct", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test 3: Edge case - negative win_prob
    ok, result = safe_call(
        sizer.recommend,
        win_prob=-0.1,
        avg_win=0.80,
        avg_loss=0.50,
        premium_per_contract=0.45,
        account_value=100.0,
        consecutive_losses=0,
    )
    if ok:
        if result.risk_dollars < 0:
            record_error("aggressive_sizer", "recommend_neg_wp", "LOGIC_ERROR",
                        f"Negative win_prob returned negative risk: {result.risk_dollars}")
        else:
            print("  ✅ Negative win_prob handled (no negative risk)")
    else:
        record_error("aggressive_sizer", "recommend_neg_wp", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test 4: Edge case - avg_loss = 0 (division by zero)
    ok, result = safe_call(
        sizer.recommend,
        win_prob=0.55,
        avg_win=0.80,
        avg_loss=0.0,
        premium_per_contract=0.45,
        account_value=100.0,
        consecutive_losses=0,
    )
    if ok:
        if result.num_contracts > 100:
            record_error("aggressive_sizer", "recommend_zero_loss", "LOGIC_ERROR",
                        f"Zero avg_loss returned {result.num_contracts} contracts (infinite Kelly)")
        else:
            print("  ✅ Zero avg_loss handled (no infinite sizing)")
    else:
        record_error("aggressive_sizer", "recommend_zero_loss", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test 5: Edge case - premium = 0
    ok, result = safe_call(
        sizer.recommend,
        win_prob=0.55,
        avg_win=0.80,
        avg_loss=0.50,
        premium_per_contract=0.0,
        account_value=100.0,
        consecutive_losses=0,
    )
    if ok:
        if result.position_value != 0:
            record_warning("aggressive_sizer", "recommend_zero_prem", 
                         f"Zero premium returned position_value={result.position_value} (should be 0)")
        else:
            print("  ✅ Zero premium handled correctly")
    else:
        record_error("aggressive_sizer", "recommend_zero_prem", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test 6: Max consecutive losses
    ok, result = safe_call(
        sizer.recommend,
        win_prob=0.55,
        avg_win=0.80,
        avg_loss=0.50,
        premium_per_contract=0.45,
        account_value=100.0,
        consecutive_losses=10,  # Many consecutive losses
    )
    if ok:
        print(f"  ✅ 10 consecutive losses: risk={result.risk_dollars:.2f}, mult={result.loss_cooldown_multiplier}")
        if result.risk_dollars > 50:
            record_warning("aggressive_sizer", "recommend_many_losses",
                         f"Still risking ${result.risk_dollars:.2f} with 10 consecutive losses")
    else:
        record_error("aggressive_sizer", "recommend_many_losses", "CRASH",
                    f"{result[0]}: {result[1]}")

except Exception as e:
    record_error("aggressive_sizer", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 6: entry_gates.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: entry_gates.py")
print("="*70)
try:
    from hermes_trader.entry_gates import (
        gate_time, gate_extended_move, gate_pullback_bounce,
        gate_intraday_structure, gate_volume, gate_rsi, gate_vwap_chop,
        check_all_gates
    )
    print("  ✅ Import successful")
    
    # Test gate_time with various times
    ok, result = safe_call(gate_time, datetime(2026, 7, 7, 9, 50))  # 9:50 ET (in window)
    if ok:
        passed, reason = result
        if passed:
            print("  ✅ gate_time(9:50) = PASS (correct)")
        else:
            record_error("entry_gates", "gate_time_morning", "LOGIC_ERROR",
                        f"9:50 should be in morning window but got: {reason}")
    
    ok, result = safe_call(gate_time, datetime(2026, 7, 7, 12, 0))  # noon (lunch chop)
    if ok:
        passed, reason = result
        if not passed:
            print("  ✅ gate_time(noon) = BLOCKED (correct)")
        else:
            record_error("entry_gates", "gate_time_noon", "LOGIC_ERROR",
                        f"Noon should be blocked but passed: {reason}")
    
    ok, result = safe_call(gate_time, datetime(2026, 7, 7, 15, 20))  # 3:20 PM (in window)
    if ok:
        passed, reason = result
        if passed:
            print("  ✅ gate_time(15:20) = PASS (correct)")
        else:
            record_error("entry_gates", "gate_time_afternoon", "LOGIC_ERROR",
                        f"15:20 should be in afternoon window")
    
    ok, result = safe_call(gate_time, datetime(2026, 7, 7, 15, 45))  # 3:45 PM (after cutoff)
    if ok:
        passed, reason = result
        if not passed:
            print("  ✅ gate_time(15:45) = BLOCKED (correct)")
        else:
            record_error("entry_gates", "gate_time_late", "LOGIC_ERROR",
                        f"15:45 should be blocked")
    
    # Test gate_extended_move
    ok, result = safe_call(gate_extended_move, 500.0, 499.0, 500.5, 498.0, "call")
    if ok:
        passed, reason = result
        print(f"  ✅ gate_extended_move(calls, small move) = {passed}")
    
    ok, result = safe_call(gate_extended_move, 505.0, 499.0, 505.5, 498.0, "call")
    if ok:
        passed, reason = result
        if not passed:
            print("  ✅ gate_extended_move(calls, large move) = BLOCKED (correct)")
        else:
            record_error("entry_gates", "gate_extended_move_calls", "LOGIC_ERROR",
                        "Calls with +1.2% move should be blocked")
    
    # Test gate_pullback_bounce
    ok, result = safe_call(gate_pullback_bounce, 499.5, 501.0, 498.0, "call")
    if ok:
        passed, reason = result
        print(f"  ✅ gate_pullback_bounce = {passed}")
    
    # Test gate_volume
    ok, result = safe_call(gate_volume, 500000, 1000000)
    if ok:
        passed, reason = result
        if not passed:
            print("  ✅ gate_volume(0.5x avg) = BLOCKED (correct)")
        else:
            record_error("entry_gates", "gate_volume_low", "LOGIC_ERROR",
                        "Volume at 0.5x average should be blocked")
    
    ok, result = safe_call(gate_volume, 1500000, 1000000)
    if ok:
        passed, reason = result
        if passed:
            print("  ✅ gate_volume(1.5x avg) = PASS (correct)")
        else:
            record_error("entry_gates", "gate_volume_high", "LOGIC_ERROR",
                        "Volume at 1.5x average should pass")
    
    # Test gate_rsi
    ok, result = safe_call(gate_rsi, 75.0, "call")
    if ok:
        passed, reason = result
        if not passed:
            print("  ✅ gate_rsi(75, call) = BLOCKED (correct, overbought)")
        else:
            record_error("entry_gates", "gate_rsi_overbought", "LOGIC_ERROR",
                        "RSI 75 should block calls")
    
    ok, result = safe_call(gate_rsi, 25.0, "put")
    if ok:
        passed, reason = result
        if not passed:
            print("  ✅ gate_rsi(25, put) = BLOCKED (correct, oversold)")
        else:
            record_error("entry_gates", "gate_rsi_oversold", "LOGIC_ERROR",
                        "RSI 25 should block puts")
    
    # Test gate_vwap_chop
    ok, result = safe_call(gate_vwap_chop, 500.01, 500.0, "call")
    if ok:
        passed, reason = result
        if not passed:
            print("  ✅ gate_vwap_chop(close to VWAP) = BLOCKED (correct)")
        else:
            record_error("entry_gates", "gate_vwap_chop", "LOGIC_ERROR",
                        "Price within 0.002% of VWAP should be blocked")
    
    # Test gate_intraday_structure
    ok, result = safe_call(gate_intraday_structure, 501.0, 499.0, 502.0, 498.0, "call")
    if ok:
        passed, reason = result
        print(f"  ✅ gate_intraday_structure = {passed}")
    
    # Test check_all_gates - this needs a lot of data
    ok, result = safe_call(
        check_all_gates,
        symbol="SPY",
        spot=500.0,
        open_price=499.0,
        high_of_day=501.0,
        low_of_day=498.0,
        current_volume=1000000,
        avg_volume_20d=800000,
        rsi_14=55.0,
        vwap=499.5,
        option_type="call",
    )
    if ok:
        if not check_return_type("entry_gates", "check_all_gates", result, dict,
                                ["passed", "reason", "gates"]):
            pass
        else:
            print(f"  ✅ check_all_gates returned valid dict: passed={result.get('passed')}")
            if "gates" in result and isinstance(result["gates"], dict):
                print(f"     Gate results: {result['gates']}")
    else:
        record_error("entry_gates", "check_all_gates", "CRASH",
                    f"{result[0]}: {result[1]}")

except Exception as e:
    record_error("entry_gates", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 7: zero_dte_scanner.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: zero_dte_scanner.py")
print("="*70)
try:
    from hermes_trader.zero_dte_scanner import (
        scan_0dte, score_option, get_best_0dte_candidate,
        _estimate_greeks, _norm_cdf, _norm_pdf,
        _score_delta, _score_gamma, _score_volume, _score_spread, _score_iv
    )
    print("  ✅ Import successful")
    
    # Test _norm_cdf and _norm_pdf (pure math, no deps)
    ok, result = safe_call(_norm_cdf, 0.0)
    if ok:
        if abs(result - 0.5) > 0.001:
            record_error("zero_dte_scanner", "_norm_cdf", "MATH_ERROR",
                        f"_norm_cdf(0.0) = {result}, expected 0.5")
        else:
            print("  ✅ _norm_cdf(0.0) = 0.5 (correct)")
    
    ok, result = safe_call(_norm_cdf, 100.0)
    if ok:
        if abs(result - 1.0) > 0.001:
            record_error("zero_dte_scanner", "_norm_cdf_large", "MATH_ERROR",
                        f"_norm_cdf(100.0) = {result}, expected ~1.0")
        else:
            print("  ✅ _norm_cdf(100.0) ≈ 1.0 (correct)")
    
    ok, result = safe_call(_norm_pdf, 0.0)
    if ok:
        expected = 0.3989  # 1/sqrt(2*pi)
        if abs(result - expected) > 0.001:
            record_error("zero_dte_scanner", "_norm_pdf", "MATH_ERROR",
                        f"_norm_pdf(0.0) = {result}, expected {expected}")
        else:
            print("  ✅ _norm_pdf(0.0) ≈ 0.399 (correct)")
    
    # Test _norm_cdf with extreme values (edge cases)
    ok, result = safe_call(_norm_cdf, -100.0)
    if ok:
        if abs(result - 0.0) > 0.001:
            record_error("zero_dte_scanner", "_norm_cdf_neg", "MATH_ERROR",
                        f"_norm_cdf(-100.0) = {result}, expected ~0.0")
        else:
            print("  ✅ _norm_cdf(-100.0) ≈ 0.0 (correct)")
    
    # Test _estimate_greeks
    ok, result = safe_call(_estimate_greeks, 500.0, 500.0, 0.25, "call")
    if ok:
        if "delta" not in result or "gamma" not in result:
            record_error("zero_dte_scanner", "_estimate_greeks", "MISSING_FIELD",
                        f"Missing delta or gamma in result: {result}")
        else:
            print(f"  ✅ _estimate_greeks: delta={result['delta']:.4f}, gamma={result['gamma']:.6f}")
            # ATM call delta should be near 0.5
            if abs(result["delta"] - 0.5) > 0.1:
                record_error("zero_dte_scanner", "_estimate_greeks", "LOGIC_ERROR",
                            f"ATM call delta = {result['delta']}, expected ~0.5")
    
    # Test _estimate_greeks with zero IV (division by zero)
    ok, result = safe_call(_estimate_greeks, 500.0, 500.0, 0.0, "call")
    if ok:
        if result["delta"] == 0.0 and result["gamma"] == 0.0:
            print("  ✅ _estimate_greeks with 0 IV returns fallback zeros")
        else:
            # Zero IV falls back to 0.30 internally, so should still work
            print(f"  ✅ _estimate_greeks with 0 IV (fallback to 0.30): delta={result['delta']:.4f}")
    
    # Test score_option with valid data
    ok, result = safe_call(
        score_option,
        option_id="test-uuid-123",
        symbol="SPY",
        option_type="call",
        strike=500.0,
        bid=2.0,
        ask=2.10,
        volume=5000,
        open_interest=10000,
        iv=0.25,
        delta=0.45,
        gamma=0.003,
        spot=500.0,
        expiration_date="2026-07-07",
    )
    if ok:
        if not check_return_type("zero_dte_scanner", "score_option", result, dict,
                                ["option_id", "score", "symbol", "option_type", "strike"]):
            pass
        else:
            print(f"  ✅ score_option returned score={result.get('score')}")
            # Score should be between 0-100
            score = result.get("score", -1)
            if score < 0 or score > 100:
                record_error("zero_dte_scanner", "score_option", "LOGIC_ERROR",
                            f"Score {score} outside valid range [0, 100]")
    else:
        record_error("zero_dte_scanner", "score_option", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test score_option with edge cases
    # Zero mid price
    ok, result = safe_call(
        score_option,
        option_id="test", symbol="SPY", option_type="call",
        strike=500.0, bid=0.0, ask=0.0, volume=100,
        open_interest=100, iv=0.25, delta=0.45,
        gamma=0.003, spot=500.0, expiration_date="2026-07-07",
    )
    if ok:
        if result == {}:
            print("  ✅ score_option with zero bid/ask returns empty dict")
        else:
            record_warning("zero_dte_scanner", "score_option_zero_bidask",
                         f"Zero bid/ask returned score={result.get('score')} instead of empty dict")
    
    # Zero spot
    ok, result = safe_call(
        score_option,
        option_id="test", symbol="SPY", option_type="call",
        strike=500.0, bid=2.0, ask=2.10, volume=100,
        open_interest=100, iv=0.25, delta=0.45,
        gamma=0.003, spot=0.0, expiration_date="2026-07-07",
    )
    if ok:
        if result == {}:
            print("  ✅ score_option with zero spot returns empty dict")
        else:
            record_warning("zero_dte_scanner", "score_option_zero_spot",
                         f"Zero spot returned score={result.get('score')} instead of empty dict")
    
    # Test individual scoring functions
    for scorer, val, name in [
        (_score_delta, 0.35, "0.35 delta"),
        (_score_delta, 0.0, "0.0 delta"),
        (_score_delta, 1.0, "1.0 delta"),
        (_score_gamma, 0.003, "0.003 gamma"),
        (_score_gamma, 0.0, "0.0 gamma"),
        (_score_volume, 5000, "5000 vol"),
        (_score_volume, 0, "0 vol"),
        (_score_spread, 5.0, "5% spread"),
        (_score_spread, 0.5, "0.5% spread"),
        (_score_iv, 0.25, "0.25 IV"),
        (_score_iv, 0.0, "0.0 IV"),
    ]:
        ok, result = safe_call(scorer, val)
        if ok:
            if not isinstance(result, (int, float)):
                record_error("zero_dte_scanner", scorer.__name__, "RETURN_TYPE",
                            f"Returns {type(result).__name__} for {name}, expected float")
            else:
                print(f"  ✅ {scorer.__name__}({val}) = {result:.2f}")
        else:
            record_error("zero_dte_scanner", scorer.__name__, "CRASH",
                        f"Crash on {name}: {result[0]}: {result[1]}")

except Exception as e:
    record_error("zero_dte_scanner", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 8: options_analytics.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: options_analytics.py")
print("="*70)
try:
    from hermes_trader.options_analytics import OptionsAnalytics, calculate_gex
    print("  ✅ Import successful")
    
    # Test OptionsAnalytics instantiation
    ok, result = safe_call(OptionsAnalytics)
    if ok:
        oa = result
        print("  ✅ OptionsAnalytics() instantiated")
    else:
        record_error("options_analytics", "OptionsAnalytics.__init__", "CRASH",
                    f"{result[0]}: {result[1]}")
        oa = None
    
    # Test calculate_gex
    ok, result = safe_call(
        calculate_gex,
        spot=500.0,
        gamma_by_strike={500: 0.005, 499: 0.003, 501: 0.004},
        open_interest_by_strike={500: 1000, 499: 500, 501: 600},
    )
    if ok:
        if not check_return_type("options_analytics", "calculate_gex", result, dict,
                                ["total_gex", "regime"]):
            pass
        else:
            print(f"  ✅ calculate_gex: total={result.get('total_gex')}, regime={result.get('regime')}")
    else:
        record_error("options_analytics", "calculate_gex", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test calculate_gex with empty data
    ok, result = safe_call(
        calculate_gex,
        spot=500.0,
        gamma_by_strike={},
        open_interest_by_strike={},
    )
    if ok:
        if result is not None and "total_gex" in result:
            print(f"  ✅ calculate_gex with empty data: total={result.get('total_gex')}")
        else:
            record_error("options_analytics", "calculate_gex_empty", "MISSING_FIELD",
                        f"Empty data result missing fields: {result}")
    else:
        record_error("options_analytics", "calculate_gex_empty", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test get_full_analytics - needs MCP, so will fail gracefully
    if oa:
        ok, result = safe_call(oa.get_full_analytics, "SPY")
        if ok:
            if not check_return_type("options_analytics", "get_full_analytics", result, dict):
                pass
            else:
                # Check expected fields auto_trader uses
                expected_fields = ["gex", "put_call_ratio", "max_pain"]
                missing = [f for f in expected_fields if f not in result]
                if missing:
                    record_error("options_analytics", "get_full_analytics", "MISSING_FIELDS",
                                f"auto_trader expects: {missing}")
                else:
                    print(f"  ✅ get_full_analytics returned all expected fields")
        else:
            # This is expected to fail without MCP credentials
            if "token" in str(result[1]).lower() or "file" in str(result[1]).lower():
                print(f"  ⚠️  get_full_analytics failed (expected without MCP): {result[0]}")
            else:
                record_error("options_analytics", "get_full_analytics", "CRASH",
                            f"{result[0]}: {result[1]}")

except Exception as e:
    record_error("options_analytics", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 9: robinhood_broker.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: integrations/robinhood_broker.py")
print("="*70)
try:
    from hermes_trader.integrations.robinhood_broker import (
        RobinhoodBrokerAdapter, BrokerError, robinhood_mcp_call,
        ROBINHOOD_MCP_URL, ROBINHOOD_TOKEN_PATH, ROBINHOOD_ACCOUNT
    )
    print("  ✅ Import successful")
    
    # Check config constants
    print(f"  ✅ ROBINHOOD_MCP_URL = {ROBINHOOD_MCP_URL}")
    print(f"  ✅ ROBINHOOD_TOKEN_PATH = {ROBINHOOD_TOKEN_PATH}")
    print(f"  ✅ ROBINHOOD_ACCOUNT = {ROBINHOOD_ACCOUNT}")
    
    # Test BrokerError
    try:
        raise BrokerError("test error")
    except BrokerError as e:
        if str(e) == "test error":
            print("  ✅ BrokerError works correctly")
        else:
            record_error("robinhood_broker", "BrokerError", "ERROR",
                        f"BrokerError message mismatch: {e}")
    
    # Test RobinhoodBrokerAdapter instantiation
    ok, result = safe_call(RobinhoodBrokerAdapter)
    if ok:
        broker = result
        print("  ✅ RobinhoodBrokerAdapter() instantiated")
        
        # Check journal path
        if hasattr(broker, '_journal_path'):
            print(f"  ✅ journal_path = {broker._journal_path}")
        else:
            record_error("robinhood_broker", "RobinhoodBrokerAdapter", "MISSING_ATTR",
                        "Missing _journal_path attribute")
    else:
        record_error("robinhood_broker", "RobinhoodBrokerAdapter.__init__", "CRASH",
                    f"{result[0]}: {result[1]}")

except Exception as e:
    record_error("robinhood_broker", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 10: options_confluence.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: options_confluence.py")
print("="*70)
try:
    from hermes_trader.options_confluence import (
        OptionsConfluenceScanner, scan_options_confluence, scan_all_confluence,
        get_best_trade, FullGreeks, ScoreBreakdown, ConfluenceCandidate,
        ConfluenceResult
    )
    print("  ✅ Import successful")
    
    # Test FullGreeks dataclass
    ok, result = safe_call(FullGreeks)
    if ok:
        greeks = result
        d = greeks.to_dict()
        if not check_return_type("options_confluence", "FullGreeks.to_dict", d, dict,
                                ["delta", "gamma", "theta", "vega", "rho"]):
            pass
        else:
            print(f"  ✅ FullGreeks().to_dict() has all expected fields")
    
    # Test ScoreBreakdown
    ok, result = safe_call(ScoreBreakdown)
    if ok:
        sb = result
        sb.total = 75.0
        if sb.tier != "A":
            record_error("options_confluence", "ScoreBreakdown.tier", "LOGIC_ERROR",
                        f"Score 75 should be tier A, got {sb.tier}")
        else:
            print(f"  ✅ ScoreBreakdown.tier with total=75 -> 'A' (correct)")
        
        sb.total = 60.0
        if sb.tier != "B":
            record_error("options_confluence", "ScoreBreakdown.tier_B", "LOGIC_ERROR",
                        f"Score 60 should be tier B, got {sb.tier}")
        else:
            print(f"  ✅ ScoreBreakdown.tier with total=60 -> 'B' (correct)")
    
    # Test get_best_trade (will fail gracefully without market data)
    ok, result = safe_call(get_best_trade, "SPY")
    if ok:
        if not check_return_type("options_confluence", "get_best_trade", result, dict,
                                ["action", "underlying"]):
            pass
        else:
            print(f"  ✅ get_best_trade returned action={result.get('action')}")
    else:
        # Expected to fail without network
        print(f"  ⚠️  get_best_trade failed (expected without network): {result[0]}")

except Exception as e:
    record_error("options_confluence", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 11: zero_dte_exits.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: zero_dte_exits.py")
print("="*70)
try:
    from hermes_trader.zero_dte_exits import (
        ZeroDTEExitManager, ExitAction, ExitReason,
        PriceSnapshot, PositionSnapshot as ExitPositionSnapshot, ExitSignal
    )
    print("  ✅ Import successful")
    
    mgr = ZeroDTEExitManager()
    
    # Create test position
    pos = ExitPositionSnapshot(
        option_id="test-uuid",
        symbol="SPY",
        option_type="call",
        quantity=2,
        entry_price=2.0,
        current_price=3.0,
        strike=500.0,
        expiration="2026-07-07",
        price_history=[
            PriceSnapshot(price=3.0, timestamp=datetime(2026, 7, 7, 14, 30)),
            PriceSnapshot(price=2.8, timestamp=datetime(2026, 7, 7, 14, 0)),
            PriceSnapshot(price=2.5, timestamp=datetime(2026, 7, 7, 13, 30)),
        ],
        entry_time=datetime(2026, 7, 7, 13, 0),
    )
    
    # Test evaluate with profit scenario
    ok, result = safe_call(
        mgr.evaluate,
        pos,
        current_price=3.0,
        entry_price=2.0,
        timestamps={"open": datetime(2026, 7, 7, 9, 30)},
    )
    if ok:
        if result is None:
            print("  ✅ evaluate(profit) returned None (no exit needed)")
        elif isinstance(result, ExitSignal):
            print(f"  ✅ evaluate(profit) returned ExitSignal: action={result.action}, reason={result.reason}")
        else:
            record_error("zero_dte_exits", "evaluate", "RETURN_TYPE",
                        f"Returns {type(result).__name__}, expected ExitSignal or None")
    else:
        record_error("zero_dte_exits", "evaluate", "CRASH",
                    f"{result[0]}: {result[1]}")
    
    # Test evaluate with loss scenario (50% loss)
    pos_loss = ExitPositionSnapshot(
        option_id="test-uuid",
        symbol="SPY",
        option_type="call",
        quantity=2,
        entry_price=2.0,
        current_price=0.8,  # -60% loss
        strike=500.0,
        expiration="2026-07-07",
        price_history=[
            PriceSnapshot(price=0.8, timestamp=datetime(2026, 7, 7, 14, 30)),
        ],
        entry_time=datetime(2026, 7, 7, 13, 0),
    )
    
    ok, result = safe_call(
        mgr.evaluate,
        pos_loss,
        current_price=0.8,
        entry_price=2.0,
        timestamps={"open": datetime(2026, 7, 7, 9, 30)},
    )
    if ok:
        if result is None:
            record_warning("zero_dte_exits", "evaluate_loss", 
                         "60% loss returned None — should trigger stop loss")
        elif isinstance(result, ExitSignal):
            print(f"  ✅ evaluate(60% loss): action={result.action}, reason={result.reason}")
        else:
            record_error("zero_dte_exits", "evaluate", "RETURN_TYPE",
                        f"Returns {type(result).__name__}")
    else:
        record_error("zero_dte_exits", "evaluate_loss", "CRASH",
                    f"{result[0]}: {result[1]}")

except Exception as e:
    record_error("zero_dte_exits", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 12: greeks_engine.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: greeks_engine.py")
print("="*70)
try:
    from hermes_trader.greeks_engine import BlackScholesGreeks, FullGreeks as GEFullGreeks
    print("  ✅ Import successful")
    
    # Test Black-Scholes delta
    ok, result = safe_call(
        BlackScholesGreeks.delta,
        S=500.0, K=500.0, r=0.05, q=0.01, sigma=0.25, tau=30/365, option_type='call'
    )
    if ok:
        if abs(result - 0.5) > 0.1:
            record_error("greeks_engine", "delta", "MATH_ERROR",
                        f"ATM call delta = {result}, expected ~0.5")
        else:
            print(f"  ✅ BlackScholesGreeks.delta(ATM call) = {result:.4f} (expected ~0.5)")
    
    # Test gamma
    ok, result = safe_call(
        BlackScholesGreeks.gamma,
        S=500.0, K=500.0, r=0.05, q=0.01, sigma=0.25, tau=30/365
    )
    if ok:
        if result <= 0:
            record_error("greeks_engine", "gamma", "MATH_ERROR",
                        f"Gamma should be positive, got {result}")
        else:
            print(f"  ✅ BlackScholesGreeks.gamma(ATM) = {result:.6f}")
    
    # Test theta (should be negative for long options)
    ok, result = safe_call(
        BlackScholesGreeks.theta,
        S=500.0, K=500.0, r=0.05, q=0.01, sigma=0.25, tau=30/365, option_type='call'
    )
    if ok:
        if result >= 0:
            record_warning("greeks_engine", "theta", 
                         f"Long call theta = {result:.4f}, expected negative")
        else:
            print(f"  ✅ BlackScholesGreeks.theta(ATM call) = {result:.4f} (negative = correct)")
    
    # Test vega (should be positive)
    ok, result = safe_call(
        BlackScholesGreeks.vega,
        S=500.0, K=500.0, r=0.05, q=0.01, sigma=0.25, tau=30/365
    )
    if ok:
        if result <= 0:
            record_error("greeks_engine", "vega", "MATH_ERROR",
                        f"Vega should be positive, got {result}")
        else:
            print(f"  ✅ BlackScholesGreeks.vega(ATM) = {result:.6f}")
    
    # Test FullGreeks dataclass
    ok, result = safe_call(GEFullGreeks)
    if ok:
        greeks = result
        d = greeks.to_dict()
        print(f"  ✅ FullGreeks().to_dict() has {len(d)} fields")
        # Check essential fields
        for field in ['delta', 'gamma', 'theta', 'vega', 'rho']:
            if field not in d:
                record_error("greeks_engine", "FullGreeks", "MISSING_FIELD", f"Missing {field}")
    
    # Test edge case: tau = 0 (expiration)
    ok, result = safe_call(
        BlackScholesGreeks.delta,
        S=500.0, K=500.0, r=0.05, q=0.01, sigma=0.25, tau=0.0, option_type='call'
    )
    if ok:
        print(f"  ✅ BlackScholesGreeks.delta(tau=0) = {result:.4f}")
    else:
        record_error("greeks_engine", "delta_tau0", "CRASH",
                    f"tau=0 crashed: {result[0]}: {result[1]}")

except Exception as e:
    record_error("greeks_engine", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 13: iv_surface.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: iv_surface.py")
print("="*70)
try:
    from hermes_trader.iv_surface import IVSurfaceBuilder
    print("  ✅ Import successful")
    
    # Test instantiation
    ok, result = safe_call(IVSurfaceBuilder)
    if ok:
        builder = result
        print("  ✅ IVSurfaceBuilder() instantiated")
        
        # Test with mock data
        ok2, result2 = safe_call(builder.build, [{"strike": 500, "iv": 0.25, "expiration": "2026-07-07"}])
        if ok2:
            print(f"  ✅ IVSurfaceBuilder.build() succeeded")
        else:
            # Expected to fail with minimal data
            print(f"  ⚠️  IVSurfaceBuilder.build() failed: {result2[0]}")
    else:
        record_error("iv_surface", "IVSurfaceBuilder.__init__", "CRASH",
                    f"{result[0]}: {result[1]}")

except Exception as e:
    record_error("iv_surface", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 14: intelligent_risk.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: intelligent_risk.py")
print("="*70)
try:
    from hermes_trader.intelligent_risk import IntelligentRiskLayer
    print("  ✅ Import successful")
    
    ok, result = safe_call(IntelligentRiskLayer)
    if ok:
        print("  ✅ IntelligentRiskLayer() instantiated")
    else:
        record_error("intelligent_risk", "IntelligentRiskLayer.__init__", "CRASH",
                    f"{result[0]}: {result[1]}")
except Exception as e:
    record_error("intelligent_risk", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 15: trailing_stops.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: trailing_stops.py")
print("="*70)
try:
    from hermes_trader.trailing_stops import _mcp_result_text
    print("  ✅ Import successful")
    
    # Test _mcp_result_text with mock MCP response
    mock_response = {
        "result": {
            "content": [
                {"type": "text", "text": '{"positions": [{"symbol": "SPY", "quantity": 1}]}'}
            ]
        }
    }
    # We can't easily call this without a network, but we can test the parsing logic
    # by importing the parsing function
    import json
    content = mock_response["result"]["content"]
    if content and content[0].get("type") == "text":
        raw = content[0]["text"]
        try:
            parsed = json.loads(raw)
            print(f"  ✅ MCP response parsing works: {parsed}")
        except:
            record_error("trailing_stops", "response_parsing", "PARSE_ERROR", "Failed to parse mock response")
except Exception as e:
    record_error("trailing_stops", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 16: gamma_positioning.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: gamma_positioning.py")
print("="*70)
try:
    from hermes_trader.gamma_positioning import GammaPositionManager
    print("  ✅ Import successful")
    
    ok, result = safe_call(GammaPositionManager)
    if ok:
        print("  ✅ GammaPositionManager() instantiated")
    else:
        record_error("gamma_positioning", "GammaPositionManager.__init__", "CRASH",
                    f"{result[0]}: {result[1]}")
except Exception as e:
    record_error("gamma_positioning", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 17: earnings_calendar.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: earnings_calendar.py")
print("="*70)
try:
    from hermes_trader.earnings_calendar import check_earnings
    print("  ✅ Import successful")
    
    ok, result = safe_call(check_earnings, "SPY")
    if ok:
        if not check_return_type("earnings_calendar", "check_earnings", result, dict,
                                ["in_danger_zone"]):
            pass
        else:
            print(f"  ✅ check_earnings returned dict with in_danger_zone={result.get('in_danger_zone')}")
    else:
        # Expected to fail without network/credentials
        print(f"  ⚠️  check_earnings failed: {result[0]}")
except Exception as e:
    record_error("earnings_calendar", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 18: options_v3.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: options_v3.py")
print("="*70)
try:
    from hermes_trader.options_v3 import OptionsV3Engine
    print("  ✅ Import successful")
    
    ok, result = safe_call(OptionsV3Engine)
    if ok:
        print("  ✅ OptionsV3Engine() instantiated")
    else:
        record_error("options_v3", "OptionsV3Engine.__init__", "CRASH",
                    f"{result[0]}: {result[1]}")
except Exception as e:
    record_error("options_v3", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 19: auto_trader.py (the main orchestrator)
# ============================================================================
print("\n" + "="*70)
print("TESTING: auto_trader.py (the main orchestrator)")
print("="*70)
try:
    from hermes_trader.auto_trader import (
        auto_trade, manage_exits, scan_and_score,
        _mcp_call, _mcp_call_direct
    )
    print("  ✅ Import successful")
    
    # Test _mcp_call (will fail without token, but should handle gracefully)
    ok, result = safe_call(_mcp_call, "get_accounts")
    if not ok:
        if "token" in str(result[1]).lower() or "not found" in str(result[1]).lower():
            print(f"  ✅ _mcp_call fails gracefully without token: {result[0]}")
        else:
            record_error("auto_trader", "_mcp_call", "UNEXPECTED_ERROR",
                        f"{result[0]}: {result[1]}")
    
    # Test auto_trade - needs full MCP, so will fail gracefully
    ok, result = safe_call(auto_trade)
    if not ok:
        # This is expected to fail without MCP credentials
        print(f"  ⚠️  auto_trade failed (expected without full MCP setup): {result[0]}")
    else:
        if not check_return_type("auto_trader", "auto_trade", result, dict,
                                ["timestamp", "action"]):
            pass
        else:
            print(f"  ✅ auto_trade returned: action={result.get('action')}")
    
    # Test manage_exits
    ok, result = safe_call(manage_exits)
    if not ok:
        print(f"  ⚠️  manage_exits failed: {result[0]}")
    else:
        if not check_return_type("auto_trader", "manage_exits", result, dict):
            pass
        else:
            print(f"  ✅ manage_exits returned: {list(result.keys())[:5]}")

except Exception as e:
    record_error("auto_trader", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 20: options_engine.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: options_engine.py")
print("="*70)
try:
    from hermes_trader.options_engine import OptionsEngine
    print("  ✅ Import successful")
    
    ok, result = safe_call(OptionsEngine)
    if ok:
        print("  ✅ OptionsEngine() instantiated")
    else:
        record_error("options_engine", "OptionsEngine.__init__", "CRASH",
                    f"{result[0]}: {result[1]}")
except Exception as e:
    record_error("options_engine", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 21: options_trader.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: options_trader.py")
print("="*70)
try:
    from hermes_trader.options_trader import OptionsTrader
    print("  ✅ Import successful")
except Exception as e:
    record_error("options_trader", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 22: backtest_engine.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: backtest_engine.py")
print("="*70)
try:
    from hermes_trader.backtest_engine import BacktestEngine
    print("  ✅ Import successful")
except Exception as e:
    record_error("backtest_engine", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 23: performance_tracker.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: performance_tracker.py")
print("="*70)
try:
    from hermes_trader.performance_tracker import PerformanceTracker
    print("  ✅ Import successful")
except Exception as e:
    record_error("performance_tracker", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 24: daily_summary.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: daily_summary.py")
print("="*70)
try:
    from hermes_trader.daily_summary import DailySummary
    print("  ✅ Import successful")
except Exception as e:
    record_error("daily_summary", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 25: health_check.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: health_check.py")
print("="*70)
try:
    from hermes_trader.health_check import HealthCheck
    print("  ✅ Import successful")
except Exception as e:
    record_error("health_check", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 26: monitoring/
# ============================================================================
print("\n" + "="*70)
print("TESTING: monitoring/")
print("="*70)
try:
    from hermes_trader.monitoring.position_monitor import PositionMonitor
    from hermes_trader.monitoring.advanced_position_monitor import AdvancedPositionMonitor
    from hermes_trader.monitoring.telegram_reporter import TelegramReporter
    print("  ✅ All monitoring imports successful")
except Exception as e:
    record_error("monitoring", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 27: workflow/
# ============================================================================
print("\n" + "="*70)
print("TESTING: workflow/")
print("="*70)
try:
    from hermes_trader.workflow.enhanced_daily_workflow import EnhancedDailyWorkflow
    print("  ✅ workflow import successful")
except Exception as e:
    record_error("workflow", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 28: policy/
# ============================================================================
print("\n" + "="*70)
print("TESTING: policy/")
print("="*70)
try:
    from hermes_trader.policy.scoring import ScoringEngine
    from hermes_trader.policy.risk_gate import RiskGate
    print("  ✅ policy imports successful")
except Exception as e:
    record_error("policy", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 29: engine_upgrades.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: engine_upgrades.py")
print("="*70)
try:
    from hermes_trader.engine_upgrades import EngineUpgrades
    print("  ✅ Import successful")
except Exception as e:
    record_error("engine_upgrades", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 30: engine_config.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: engine_config.py")
print("="*70)
try:
    from hermes_trader.engine_config import EngineConfig
    print("  ✅ Import successful")
except Exception as e:
    record_error("engine_config", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 31: indicators_config.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: indicators_config.py")
print("="*70)
try:
    from hermes_trader.indicators_config import IndicatorsConfig
    print("  ✅ Import successful")
except Exception as e:
    record_error("indicators_config", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 32: research/
# ============================================================================
print("\n" + "="*70)
print("TESTING: research/")
print("="*70)
try:
    from hermes_trader.research.agents_client import TradingAgentsClient
    from hermes_trader.research.vibe_client import VibeClient
    from hermes_trader.research.technical_scan import TechnicalScan
    from hermes_trader.research.backtest_validator import BacktestValidator
    print("  ✅ research imports successful")
except Exception as e:
    record_error("research", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 33: risk_dashboard.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: risk_dashboard.py")
print("="*70)
try:
    from hermes_trader.risk_dashboard import RiskDashboard
    print("  ✅ Import successful")
except Exception as e:
    record_error("risk_dashboard", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 34: portfolio_report.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: portfolio_report.py")
print("="*70)
try:
    from hermes_trader.portfolio_report import PortfolioReport
    print("  ✅ Import successful")
except Exception as e:
    record_error("portfolio_report", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 35: rebalancer.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: rebalancer.py")
print("="*70)
try:
    from hermes_trader.rebalancer import Rebalancer
    print("  ✅ Import successful")
except Exception as e:
    record_error("rebalancer", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# MODULE 36: cli.py
# ============================================================================
print("\n" + "="*70)
print("TESTING: cli.py")
print("="*70)
try:
    from hermes_trader.cli import main
    print("  ✅ Import successful")
except Exception as e:
    record_error("cli", "import", "IMPORT_ERROR", str(e))

# ============================================================================
# CROSS-MODULE INTEGRATION TEST
# ============================================================================
print("\n" + "="*70)
print("TESTING: Cross-module integration (auto_trader -> all dependencies)")
print("="*70)

# Check that auto_trader can import all its dependencies
print("  Testing auto_trader dependency imports...")
auto_deps = [
    "hermes_trader.options_analytics",
    "hermes_trader.market_regime", 
    "hermes_trader.zero_dte_scanner",
    "hermes_trader.aggressive_sizer",
    "hermes_trader.entry_gates",
    "hermes_trader.earnings_calendar",
    "hermes_trader.integrations.robinhood_broker",
]
for dep in auto_deps:
    ok, result = safe_call(importlib.import_module, dep)
    if ok:
        print(f"  ✅ {dep}")
    else:
        record_error("auto_trader_integration", dep, "IMPORT_ERROR",
                    f"auto_trader depends on {dep} but import failed: {result[1]}")

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print(f"\nTotal errors found: {len(ERRORS)}")
print(f"Total warnings: {len(WARNINGS)}")

if ERRORS:
    print("\n🔴 ERRORS (must fix):")
    for i, err in enumerate(ERRORS, 1):
        print(f"\n  {i}. [{err['module']}.{err['function']}] {err['error_type']}")
        print(f"     {err['message']}")

if WARNINGS:
    print("\n🟡 WARNINGS (should review):")
    for i, warn in enumerate(WARNINGS, 1):
        print(f"\n  {i}. [{warn['module']}.{warn['function']}]")
        print(f"     {warn['message']}")
