"""Portfolio-Level Risk Management — Correlation, VaR, and Drawdown Protection.

Monitors portfolio-level risks:
- Position correlation (SPY calls + QQQ calls = doubled directional risk)
- Max drawdown protection (stop trading after X% drawdown)
- Kelly criterion with portfolio constraints
- Concentration limits (max % in one direction)

Integration with auto_trader:
- check_portfolio_risk() called before every trade
- Reduces position size when correlation is high
- Blocks trading during drawdown
"""

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.portfolio_risk")

ET = ZoneInfo("America/New_York")

# ── Configuration ──
MAX_PORTFOLIO_RISK_PCT = 0.15      # Max 15% of portfolio at risk
MAX_CORRELATED_RISK_PCT = 0.10     # Max 10% in correlated positions
MAX_DRAWDOWN_PCT = 0.10            # 10% max drawdown triggers stop
MAX_SAME_DIRECTION_PCT = 0.20      # Max 20% in same direction
KELLY_FRACTION = 0.25              # Quarter Kelly for safety
POSITION_HISTORY_FILE = Path("/opt/hermes-trader/data/journals/position_history.jsonl")


def calculate_portfolio_correlation(positions: List[Dict]) -> float:
    """Calculate correlation between open positions.
    
    Positions with the same underlying or same direction are correlated.
    
    Args:
        positions: List of position dicts with 'symbol', 'type', 'quantity', 'value'
    
    Returns:
        Correlation score 0.0-1.0 (1.0 = fully correlated)
    """
    if len(positions) <= 1:
        return 0.0
    
    total_value = sum(p.get("value", 0) for p in positions)
    if total_value <= 0:
        return 0.0
    
    # Group by underlying
    by_underlying = {}
    for p in positions:
        sym = p.get("symbol", "UNKNOWN")
        by_underlying.setdefault(sym, []).append(p)
    
    # Group by direction
    calls_value = sum(p.get("value", 0) for p in positions if p.get("type") == "call")
    puts_value = sum(p.get("value", 0) for p in positions if p.get("type") == "put")
    
    # Calculate correlation metrics
    concentration = 0.0
    for sym, syms in by_underlying.items():
        sym_value = sum(p.get("value", 0) for p in syms)
        sym_pct = sym_value / total_value
        concentration += sym_pct ** 2  # Herfindahl index
    
    # Directional correlation
    directional_pct = max(calls_value, puts_value) / total_value if total_value > 0 else 0
    
    # Combined correlation score
    correlation = (concentration * 0.5 + directional_pct * 0.5)
    return min(1.0, correlation)


def calculate_portfolio_var(positions: List[Dict], confidence: float = 0.95) -> float:
    """Calculate portfolio Value at Risk (VaR).
    
    Simple VaR = sqrt(sum(weight_i² × var_i² + 2×sum(weight_i×weight_j×var_i×var_j×corr_ij))
    
    For simplicity, assumes 1.0 correlation within same underlying.
    
    Args:
        positions: List of position dicts with 'symbol', 'value', 'volatility'
        confidence: VaR confidence level (0.95 = 95%)
    
    Returns:
        VaR in dollar terms
    """
    total_value = sum(p.get("value", 0) for p in positions)
    if total_value <= 0:
        return 0.0
    
    # Z-score for confidence level
    z_scores = {0.90: 1.28, 0.95: 1.645, 0.99: 2.326}
    z = z_scores.get(confidence, 1.645)
    
    # Group by underlying for correlation
    by_underlying = {}
    for p in positions:
        sym = p.get("symbol", "UNKNOWN")
        by_underlying.setdefault(sym, []).append(p)
    
    total_var_squared = 0.0
    
    for sym, syms in by_underlying.items():
        # Within same underlying: perfect correlation
        sym_var_squared = 0.0
        for p in syms:
            weight = p.get("value", 0) / total_value
            vol = p.get("volatility", 0.20)  # Default 20% vol
            var_i = weight * vol * z * total_value
            sym_var_squared += var_i ** 2
        
        # Add cross-term (correlation = 1.0 within same underlying)
        for i, p1 in enumerate(syms):
            for j, p2 in enumerate(syms):
                if i != j:
                    w1 = p1.get("value", 0) / total_value
                    w2 = p2.get("value", 0) / total_value
                    v1 = p1.get("volatility", 0.20)
                    v2 = p2.get("volatility", 0.20)
                    cross = w1 * w2 * v1 * v2 * z ** 2 * total_value ** 2
                    sym_var_squared += cross
        
        total_var_squared += sym_var_squared
    
    # Cross-underlying: assume 0.6 correlation (SPY-QQQ high, others lower)
    underlyings = list(by_underlying.keys())
    for i in range(len(underlyings)):
        for j in range(i + 1, len(underlyings)):
            corr = 0.6 if underlyings[i] in ["SPY", "QQQ"] and underlyings[j] in ["SPY", "QQQ"] else 0.3
            for p1 in by_underlying[underlyings[i]]:
                for p2 in by_underlying[underlyings[j]]:
                    w1 = p1.get("value", 0) / total_value
                    w2 = p2.get("value", 0) / total_value
                    v1 = p1.get("volatility", 0.20)
                    v2 = p2.get("volatility", 0.20)
                    cross = w1 * w2 * v1 * v2 * corr * z ** 2 * total_value ** 2
                    total_var_squared += cross
    
    return math.sqrt(max(0, total_var_squared))


def check_concentration_risk(positions: List[Dict], new_trade: Dict) -> Tuple[bool, str]:
    """Check if new trade creates concentration risk.
    
    Args:
        positions: Current open positions
        new_trade: Proposed trade dict with 'symbol', 'type', 'value'
    
    Returns:
        (passes, reason) tuple
    """
    total_value = sum(p.get("value", 0) for p in positions) + new_trade.get("value", 0)
    if total_value <= 0:
        return True, ""
    
    # Check same underlying concentration
    new_sym = new_trade.get("symbol", "UNKNOWN")
    existing_sym_value = sum(p.get("value", 0) for p in positions if p.get("symbol") == new_sym)
    new_sym_total = existing_sym_value + new_trade.get("value", 0)
    sym_pct = new_sym_total / total_value
    
    if sym_pct > 0.30:
        return False, f"CONCENTRATION: {new_sym} would be {sym_pct:.0%} of portfolio (max 30%)"
    
    # Check same direction concentration
    new_type = new_trade.get("type", "call")
    existing_dir_value = sum(p.get("value", 0) for p in positions if p.get("type") == new_type)
    new_dir_total = existing_dir_value + new_trade.get("value", 0)
    dir_pct = new_dir_total / total_value
    
    if dir_pct > MAX_SAME_DIRECTION_PCT:
        return False, f"DIRECTION: {new_type}s would be {dir_pct:.0%} of portfolio (max {MAX_SAME_DIRECTION_PCT:.0%})"
    
    # Check total risk
    total_risk = sum(p.get("value", 0) for p in positions) + new_trade.get("value", 0)
    risk_pct = total_risk / total_value if total_value > 0 else 0
    
    if risk_pct > MAX_PORTFOLIO_RISK_PCT:
        return False, f"RISK: Total exposure {risk_pct:.0%} exceeds {MAX_PORTFOLIO_RISK_PCT:.0%}"
    
    return True, ""


def check_drawdown(equity_curve: List[float]) -> Tuple[bool, float, str]:
    """Check if portfolio is in drawdown.
    
    Args:
        equity_curve: List of portfolio values over time
    
    Returns:
        (is_trading_blocked, drawdown_pct, reason) tuple
    """
    if len(equity_curve) < 2:
        return False, 0.0, ""
    
    peak = max(equity_curve)
    current = equity_curve[-1]
    
    if peak <= 0:
        return False, 0.0, ""
    
    drawdown_pct = (peak - current) / peak
    
    if drawdown_pct >= MAX_DRAWDOWN_PCT:
        return True, drawdown_pct, f"DRAWDOWN: {drawdown_pct:.1%} from peak ${peak:.2f} to ${current:.2f}"
    
    return False, drawdown_pct, ""


def kelly_with_portfolio_constraint(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    portfolio_value: float,
    current_positions: List[Dict],
    new_trade_value: float,
) -> Tuple[float, str]:
    """Calculate Kelly size with portfolio-level constraints.
    
    Args:
        win_rate: Expected win rate
        avg_win: Average win percentage
        avg_loss: Average loss percentage
        portfolio_value: Total portfolio value
        current_positions: Current open positions
        new_trade_value: Value of proposed new trade
    
    Returns:
        (recommended_size, reason) tuple
    """
    # Base Kelly
    b = avg_win / avg_loss if avg_loss > 0 else 1.0
    kelly = (b * win_rate - (1 - win_rate)) / b if b > 0 else 0
    
    # Apply fractional Kelly
    kelly *= KELLY_FRACTION
    
    # Cap at max position size
    max_position = portfolio_value * 0.20  # Max 20% per trade
    recommended = min(kelly * portfolio_value, max_position)
    
    # Check correlation constraint
    total_exposure = sum(p.get("value", 0) for p in current_positions) + new_trade_value
    if total_exposure > portfolio_value * MAX_CORRELATED_RISK_PCT:
        recommended *= 0.5  # Reduce size when exposure is high
        reason = f"Reduced: high exposure ({total_exposure/portfolio_value:.0%})"
    else:
        reason = f"Full Kelly: {kelly:.2%}"
    
    return max(0, recommended), reason


def get_portfolio_risk_summary(positions: List[Dict], equity_curve: List[float] = None) -> Dict:
    """Generate comprehensive portfolio risk summary.
    
    Returns:
        Dict with risk metrics and recommendations
    """
    summary = {
        "timestamp": datetime.now(ET).isoformat(),
        "num_positions": len(positions),
        "total_exposure": sum(p.get("value", 0) for p in positions),
    }
    
    # Correlation
    summary["correlation"] = calculate_portfolio_correlation(positions)
    
    # Concentration
    by_underlying = {}
    for p in positions:
        sym = p.get("symbol", "UNKNOWN")
        by_underlying.setdefault(sym, 0)
        by_underlying[sym] += p.get("value", 0)
    summary["concentration"] = by_underlying
    
    # VaR
    if positions:
        summary["var_95"] = calculate_portfolio_var(positions, 0.95)
    else:
        summary["var_95"] = 0.0
    
    # Drawdown
    if equity_curve:
        is_blocked, dd_pct, dd_reason = check_drawdown(equity_curve)
        summary["drawdown_pct"] = dd_pct
        summary["drawdown_blocked"] = is_blocked
        summary["drawdown_reason"] = dd_reason
    else:
        summary["drawdown_pct"] = 0.0
        summary["drawdown_blocked"] = False
    
    # Recommendations
    recommendations = []
    if summary["correlation"] > 0.7:
        recommendations.append("High correlation — diversify underlyings or directions")
    if summary["num_positions"] > 3:
        recommendations.append("Many positions — consider reducing to 1-2 high-conviction trades")
    if summary["drawdown_blocked"]:
        recommendations.append("DRAWDOWN BREACH — stop trading until recovery")
    
    summary["recommendations"] = recommendations
    summary["can_trade"] = not summary.get("drawdown_blocked", False)
    
    return summary
