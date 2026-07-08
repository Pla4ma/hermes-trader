"""Smart Exit System — sells at the RIGHT time to maximize profit.

Unlike basic stop-loss (only protects downside), this module:
1. Sells 50% at first profit target (lock gains)
2. Trails remaining with momentum-based stops
3. Uses VWAP as dynamic reference
4. Sells into strength (not weakness)
5. Respects time decay for 0DTE (tighten near close)

Key insight: 0DTE options lose value FAST after 2 PM.
The best exits are BEFORE the theta crush accelerates.
"""

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.smart_exits")

ET = ZoneInfo("America/New_York")


def calculate_smart_exit(
    entry_price: float,
    current_price: float,
    spot: float,
    strike: float,
    option_type: str,
    entry_time: Optional[datetime] = None,
    vwap: Optional[float] = None,
    rsi: Optional[float] = None,
    iv: Optional[float] = None,
) -> dict:
    """Calculate the smart exit action for a position.
    
    Returns:
        {
            "action": "hold" | "sell_all" | "sell_half" | "tighten_stop",
            "reason": str,
            "stop_price": float,
            "target_price": float,
            "urgency": "low" | "medium" | "high" | "critical",
        }
    """
    now = datetime.now(ET)
    if entry_time is None:
        entry_time = now
    # Ensure both datetimes are timezone-aware for subtraction
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=ET)
    
    pnl_pct = ((current_price / entry_price) - 1) * 100 if entry_price > 0 else 0
    minutes_held = (now - entry_time).total_seconds() / 60
    minutes_to_close = (16 * 60) - (now.hour * 60 + now.minute)  # 4:00 PM ET
    
    result = {
        "action": "hold",
        "reason": "",
        "stop_price": round(entry_price * 0.50, 4),  # default 50% stop
        "target_price": round(entry_price * 2.0, 4),  # default 100% target
        "urgency": "low",
    }
    
    # ═══════════════════════════════════════════════════════════════
    # RULE 1: Time decay — 0DTE options die after 2 PM ET
    # Sell everything by 3:30 PM no matter what
    # ═══════════════════════════════════════════════════════════════
    if minutes_to_close <= 30:
        result["action"] = "sell_all"
        result["reason"] = f"CRITICAL: 30 min to close, selling to avoid total decay"
        result["urgency"] = "critical"
        return result
    
    if minutes_to_close <= 60 and pnl_pct < 50:
        result["action"] = "sell_all"
        result["reason"] = f"1 hour to close, profit under 50% — locking in"
        result["urgency"] = "high"
        return result
    
    # ═══════════════════════════════════════════════════════════════
    # RULE 2: Profit targets — sell into strength
    # ═══════════════════════════════════════════════════════════════
    
    # Target 3: +100% — sell everything
    if pnl_pct >= 100:
        result["action"] = "sell_all"
        result["reason"] = f"+{pnl_pct:.0f}% profit — DOUBLE, take it all"
        result["urgency"] = "high"
        result["target_price"] = current_price
        return result
    
    # Target 2: +50% — sell half, trail rest
    if pnl_pct >= 50:
        result["action"] = "sell_half"
        result["reason"] = f"+{pnl_pct:.0f}% — selling half, trailing rest"
        result["urgency"] = "medium"
        result["stop_price"] = round(entry_price * 1.10, 4)  # trail at +10% above entry
        result["target_price"] = current_price
        return result
    
    # Target 1: +25% — tighten stop
    if pnl_pct >= 25:
        result["action"] = "tighten_stop"
        result["reason"] = f"+{pnl_pct:.0f}% — tightening stop to breakeven"
        result["urgency"] = "medium"
        result["stop_price"] = round(entry_price * 1.02, 4)  # 2% above entry (breakeven+)
        return result
    
    # ═══════════════════════════════════════════════════════════════
    # RULE 3: VWAP-based exits
    # ═══════════════════════════════════════════════════════════════
    if vwap and spot and vwap > 0:
        if option_type == "put" and spot > vwap:
            # SPY above VWAP while we have puts — bad
            if pnl_pct > 0:
                result["action"] = "sell_all"
                result["reason"] = f"SPY above VWAP ({vwap:.2f}) — momentum shifting against puts"
                result["urgency"] = "medium"
                return result
            elif pnl_pct < -20:
                result["action"] = "sell_all"
                result["reason"] = f"SPY above VWAP + {pnl_pct:.0f}% loss — cutting"
                result["urgency"] = "high"
                return result
        
        elif option_type == "call" and spot < vwap:
            # SPY below VWAP while we have calls — bad
            if pnl_pct > 0:
                result["action"] = "sell_all"
                result["reason"] = f"SPY below VWAP ({vwap:.2f}) — momentum shifting against calls"
                result["urgency"] = "medium"
                return result
            elif pnl_pct < -20:
                result["action"] = "sell_all"
                result["reason"] = f"SPY below VWAP + {pnl_pct:.0f}% loss — cutting"
                result["urgency"] = "high"
                return result
    
    # ═══════════════════════════════════════════════════════════════
    # RULE 4: Momentum fade — option losing steam
    # ═══════════════════════════════════════════════════════════════
    if minutes_held > 5 and pnl_pct < -15:
        result["action"] = "sell_all"
        result["reason"] = f"Held {minutes_held:.0f} min, down {pnl_pct:.0f}% — momentum faded"
        result["urgency"] = "high"
        return result
    
    # ═══════════════════════════════════════════════════════════════
    # RULE 5: Quick scalp profit — don't be greedy
    # ═══════════════════════════════════════════════════════════════
    if pnl_pct >= 15 and minutes_held < 5:
        result["action"] = "sell_all"
        result["reason"] = f"+{pnl_pct:.0f}% in {minutes_held:.0f} min — fast scalp, take profit"
        result["urgency"] = "medium"
        return result
    
    # ═══════════════════════════════════════════════════════════════
    # RULE 6: Stop loss — hard cut
    # ═══════════════════════════════════════════════════════════════
    if pnl_pct <= -30:
        result["action"] = "sell_all"
        result["reason"] = f"-{abs(pnl_pct):.0f}% loss — stop loss triggered"
        result["urgency"] = "high"
        result["stop_price"] = current_price
        return result
    
    # ═══════════════════════════════════════════════════════════════
    # RULE 7: RSI overbought/oversold reversal
    # ═══════════════════════════════════════════════════════════════
    if rsi:
        if option_type == "put" and rsi > 70:
            # RSI > 70 means underlying oversold, puts at risk of bounce
            if pnl_pct > 10:
                result["action"] = "sell_all"
                result["reason"] = f"RSI {rsi:.0f} oversold — bounce risk, taking {pnl_pct:.0f}%"
                result["urgency"] = "medium"
                return result
        
        elif option_type == "call" and rsi < 30:
            # RSI < 30 means underlying overbought, calls at risk of pullback
            if pnl_pct > 10:
                result["action"] = "sell_all"
                result["reason"] = f"RSI {rsi:.0f} overbought — pullback risk, taking {pnl_pct:.0f}%"
                result["urgency"] = "medium"
                return result
    
    # ═══════════════════════════════════════════════════════════════
    # DEFAULT: Hold with trailing stop
    # ═══════════════════════════════════════════════════════════════
    if pnl_pct > 0:
        # In profit — trail stop at 50% of current profit
        result["stop_price"] = round(current_price * 0.70, 4)  # give back 30% of gains max
    else:
        # In loss — stop at 30% below entry
        result["stop_price"] = round(entry_price * 0.70, 4)
    
    result["reason"] = f"Holding: {pnl_pct:+.1f}%, {minutes_held:.0f}min held, {minutes_to_close}min to close"
    return result
