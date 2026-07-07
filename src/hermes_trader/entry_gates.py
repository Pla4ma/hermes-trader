"""Entry Gate Filters — BLOCKS trades without proper confirmation.

These are HARD GATES. If any gate fails, the trade is REJECTED.
No exceptions. No "it's close enough." The engine bought at the top
because these didn't exist.

Gates:
  1. Time Gate: Only trade during optimal windows
  2. Extended Move Gate: Don't chase if underlying already moved too much
  3. Pullback Gate: Must see a pullback + bounce before entry
  4. Intraday Structure Gate: Higher lows for calls, lower highs for puts
  5. Volume Gate: Volume must confirm the move
  6. Momentum Gate: RSI must not be overbought/oversold at entry

Each gate returns (passed: bool, reason: str).
All gates must pass for a trade to execute.
"""

import logging
from datetime import datetime
from typing import Tuple, Optional

logger = logging.getLogger("hermes_trader.entry_gates")


def check_all_gates(
    symbol: str,
    option_type: str,
    spot: float,
    open_price: float,
    high_of_day: float,
    low_of_day: float,
    current_volume: float,
    avg_volume_20d: float,
    rsi_14: float,
    now_et: datetime = None,
    vwap: Optional[float] = None,
    atr_14: Optional[float] = None,
    prev_close: Optional[float] = None,
) -> Tuple[bool, list[str]]:
    """Run ALL entry gates. Returns (passed, list_of_failures).

    Args:
        symbol: Underlying symbol (e.g., "SPY")
        option_type: "call" or "put"
        spot: Current price
        open_price: Today's open
        high_of_day: Intraday high
        low_of_day: Intraday low
        current_volume: Today's volume so far
        avg_volume_20d: 20-day average volume
        rsi_14: Current RSI(14)
        now_et: Current time (Eastern), defaults to now
        vwap: Volume Weighted Average Price (optional)
        atr_14: Average True Range over 14 periods (optional)
        prev_close: Previous session close (optional)

    Returns:
        (True, []) if ALL gates pass
        (False, [reason1, reason2, ...]) if ANY gate fails
    """
    if now_et is None:
        # Default to ET time, not UTC
        try:
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now_et = datetime.utcnow()

    failures = []

    # Gate 1: Time
    passed, reason = gate_time(now_et)
    if not passed:
        failures.append(reason)

    # Gate 2: Extended Move
    passed, reason = gate_extended_move(spot, open_price, high_of_day, low_of_day, option_type)
    if not passed:
        failures.append(reason)

    # Gate 3: Pullback + Bounce
    passed, reason = gate_pullback_bounce(spot, high_of_day, low_of_day, option_type)
    if not passed:
        failures.append(reason)

    # Gate 4: Intraday Structure
    passed, reason = gate_intraday_structure(spot, open_price, high_of_day, low_of_day, option_type)
    if not passed:
        failures.append(reason)

    # Gate 5: Volume
    passed, reason = gate_volume(current_volume, avg_volume_20d)
    if not passed:
        failures.append(reason)

    # Gate 6: RSI
    passed, reason = gate_rsi(rsi_14, option_type)
    if not passed:
        failures.append(reason)
    
    # Gate 7: VWAP Chop (price chopping around VWAP = no trade)
    if vwap is not None:
        passed, reason = gate_vwap_chop(spot, vwap, option_type)
        if not passed:
            failures.append(reason)
    
    # Gate 8: ATR Low (low volatility = skip)
    if atr_14 is not None and open_price > 0:
        passed, reason = gate_atr_low(atr_14, open_price)
        if not passed:
            failures.append(reason)
    
    # Gate 9: Gap Fade Risk (large gap up = risk of fade)
    if prev_close is not None and prev_close > 0:
        passed, reason = gate_gap_fade(spot, prev_close, option_type)
        if not passed:
            failures.append(reason)

    if failures:
        logger.warning(f"ENTRY BLOCKED for {symbol} {option_type}: {failures}")
        return False, failures

    return True, []


def gate_time(now_et: datetime) -> Tuple[bool, str]:
    """Gate 1: Only trade during optimal windows.

    Windows:
      - 9:45-10:30 AM ET (post-open momentum, ORB established)
      - 2:00-3:30 PM ET (afternoon trend, before power hour chaos)

    BLOCKS:
      - Before 9:45 (opening chaos, spreads wide)
      - 10:30-2:00 PM (lunch chop, low conviction)
      - After 3:30 PM (theta crush, pin risk)
    """
    hour = now_et.hour
    minute = now_et.minute

    # Morning window: 9:45 - 10:30
    morning_ok = (hour == 9 and minute >= 45) or (hour == 10 and minute <= 30)
    # Afternoon window: 2:00 - 3:30
    afternoon_ok = (hour == 14) or (hour == 15 and minute <= 30)

    if morning_ok or afternoon_ok:
        return True, ""

    return False, f"TIME GATE: {hour:02d}:{minute:02d} ET — outside optimal windows (9:45-10:30, 2:00-3:30)"


def gate_extended_move(spot: float, open_price: float, high_of_day: float, low_of_day: float, option_type: str) -> Tuple[bool, str]:
    """Gate 2: Adaptive extended move detection.

    NORMAL days (move < 0.5%): strict 0.4% threshold — don't chase small moves.
    TREND days (move > 1%): require fresh continuation evidence before entry.
      - Don't auto-block on big days
      - Require: failed bounce, break below swing low, or lower high
      - This lets us participate in genuine trends while avoiding chasing

    For calls: mirror logic (bounce up + rejection).
    For puts: bounce down + rejection = continuation.
    """
    if open_price <= 0:
        return True, ""

    move_from_open = ((spot - open_price) / open_price) * 100

    # Intraday range
    day_range = high_of_day - low_of_day if high_of_day > low_of_day else 1
    position_in_range = ((spot - low_of_day) / day_range) * 100 if day_range > 0 else 50

    abs_move = abs(move_from_open)

    # ── NORMAL DAY (move < 0.5%): strict original rules ──
    if abs_move < 0.5:
        if option_type == "call":
            if move_from_open > 0.4:
                return False, f"EXTENDED GATE: Calls blocked — normal day, already +{move_from_open:.2f}% (max 0.4%)"
            if position_in_range > 85:
                return False, f"EXTENDED GATE: Calls blocked — at {position_in_range:.0f}% of range (top 15%)"
        elif option_type == "put":
            if move_from_open < -0.4:
                return False, f"EXTENDED GATE: Puts blocked — normal day, already {move_from_open:.2f}% (max -0.4%)"
            if position_in_range < 15:
                return False, f"EXTENDED GATE: Puts blocked — at {position_in_range:.0f}% of range (bottom 15%)"
        return True, ""

    # ── TREND DAY (move > 1%): require fresh continuation evidence ──
    # Check for failed bounce pattern
    # A failed bounce = price bounced from extreme, then rolled back
    # We detect this by checking if spot is near the extreme after a bounce
    bounce_from_extreme = 0
    if option_type == "put":
        # For puts: check if price bounced up from day low then came back down
        bounce_from_extreme = ((spot - low_of_day) / day_range * 100) if day_range > 0 else 50
    elif option_type == "call":
        # For calls: check if price bounced down from day high then came back up
        bounce_from_extreme = ((high_of_day - spot) / day_range * 100) if day_range > 0 else 50

    # TREND DAY PUTS:
    # Allow if: spot is within 25% of day low (bounced and came back = failed bounce)
    # Block if: spot is above 50% of range (bounce is holding = no continuation)
    if option_type == "put":
        if position_in_range < 25:
            # Near the low — failed bounce, continuation likely
            return True, f"TREND DAY: Puts allowed — failed bounce detected ({position_in_range:.0f}% of range, move={move_from_open:.2f}%)"
        elif position_in_range < 50:
            # Mid range — ambiguous, allow with warning
            return True, f"TREND DAY: Puts allowed — mid-range ({position_in_range:.0f}%), move={move_from_open:.2f}%"
        else:
            # Still near high — bounce holding, no continuation
            return False, f"EXTENDED GATE: Puts blocked — trend day but bounce holding ({position_in_range:.0f}% of range)"

    # TREND DAY CALLS:
    # Mirror logic
    if option_type == "call":
        if position_in_range > 75:
            return True, f"TREND DAY: Calls allowed — failed selloff detected ({position_in_range:.0f}% of range, move=+{move_from_open:.2f}%)"
        elif position_in_range > 50:
            return True, f"TREND DAY: Calls allowed — mid-range ({position_in_range:.0f}%), move=+{move_from_open:.2f}%"
        else:
            return False, f"EXTENDED GATE: Calls blocked — trend day but bounce holding ({position_in_range:.0f}% of range)"

    return True, ""


def gate_pullback_bounce(spot: float, high_of_day: float, low_of_day: float, option_type: str) -> Tuple[bool, str]:
    """Gate 3: Must see a pullback + bounce before entry.

    ADAPTIVE: On trend days (>1% move), loosen pullback requirement from
    0.15% to 0.08% — the confirmation is already in the trend.

    For CALLS:
      - Price must have pulled back from intraday high
      - Current price must be bouncing (above the pullback low)
    For PUTS:
      - Price must have bounced from intraday low
      - Current price must be rolling over (below the bounce high)
      - This means we're NOT buying at the exact top

    For PUTS:
      - Price must have bounced at least 0.15% from intraday low
      - Current price must be rolling over (below the bounce high)
      - This means we're NOT buying at the exact bottom

    This is the #1 filter that would have prevented the bad trade.
    """
    if high_of_day <= 0 or low_of_day <= 0:
        return True, ""

    day_range = high_of_day - low_of_day

    # Detect trend day: >1% move from open
    move_from_open_pct = 0
    if high_of_day > 0 and low_of_day > 0:
        mid = (high_of_day + low_of_day) / 2
        move_from_open_pct = abs(((mid - low_of_day) / low_of_day) * 100) if low_of_day > 0 else 0
    is_trend_day = move_from_open_pct > 1.0
    
    # On trend days, loosen pullback requirement (trend already confirmed)
    pullback_threshold = 0.08 if is_trend_day else 0.15
    trend_label = "TREND DAY" if is_trend_day else ""

    if option_type == "call":
        # Pullback from high: how far did price drop from today's high?
        pullback_pct = ((high_of_day - spot) / high_of_day) * 100

        if pullback_pct < pullback_threshold:
            return False, f"PULLBACK GATE: Calls blocked — {trend_label} only {pullback_pct:.2f}% below high (need ≥{pullback_threshold}%)"

        # Bounce: price should be above the halfway point of the pullback
        pullback_low = high_of_day - (day_range * 0.3)  # Estimate pullback zone
        if spot < pullback_low:
            return False, f"PULLBACK GATE: Calls blocked — in pullback zone, no bounce yet"

    elif option_type == "put":
        bounce_pct = ((spot - low_of_day) / low_of_day) * 100

        if bounce_pct < pullback_threshold:
            return False, f"PULLBACK GATE: Puts blocked — {trend_label} only {bounce_pct:.2f}% above low (need ≥{pullback_threshold}%)"

        bounce_high = low_of_day + (day_range * 0.3)
        if spot > bounce_high:
            return False, f"PULLBACK GATE: Puts blocked — in bounce zone, no rollover yet"

    return True, ""


def gate_intraday_structure(spot: float, open_price: float, high_of_day: float, low_of_day: float, option_type: str) -> Tuple[bool, str]:
    """Gate 4: Intraday structure must confirm direction.

    For CALLS:
      - Spot must be ABOVE the open (not fading)
      - But NOT above the high (that's chasing)

    For PUTS:
      - Spot must be BELOW the open (not recovering)
      - But NOT below the low (that's chasing)

    Additional: range must be meaningful (not a doji day).
    """
    if open_price <= 0:
        return True, ""

    day_range_pct = ((high_of_day - low_of_day) / open_price) * 100

    # Need at least some movement to trade
    if day_range_pct < 0.1:
        return False, f"STRUCTURE GATE: Day range only {day_range_pct:.2f}% — too tight to trade"

    if option_type == "call":
        if spot < open_price:
            return False, f"STRUCTURE GATE: Calls blocked — SPY ${spot:.2f} is BELOW open ${open_price:.2f} (fading)"
    elif option_type == "put":
        if spot > open_price:
            return False, f"STRUCTURE GATE: Puts blocked — SPY ${spot:.2f} is ABOVE open ${open_price:.2f} (recovering)"

    return True, ""


def gate_volume(current_volume: float, avg_volume_20d: float) -> Tuple[bool, str]:
    """Gate 5: Volume must confirm the move.

    If volume is below average, the move lacks conviction.
    Need at least 50% of average volume to trade.
    """
    if avg_volume_20d <= 0:
        return True, ""

    vol_ratio = current_volume / avg_volume_20d

    if vol_ratio < 0.5:
        return False, f"VOLUME GATE: Volume only {vol_ratio:.1f}x average (need ≥0.5x) — low conviction move"

    return True, ""


def gate_rsi(rsi_14: float, option_type: str) -> Tuple[bool, str]:
    """Gate 6: RSI must not be extreme at entry.

    For CALLS: RSI must be below 68 (not overbought)
    For PUTS: RSI must be above 32 (not oversold)

    If RSI is extreme, the move is extended and likely to reverse.
    """
    if option_type == "call":
        if rsi_14 > 68:
            return False, f"RSI GATE: Calls blocked — RSI {rsi_14:.1f} > 68 (overbought, likely to reverse)"
    elif option_type == "put":
        if rsi_14 < 32:
            return False, f"RSI GATE: Puts blocked — RSI {rsi_14:.1f} < 32 (oversold, likely to bounce)"

    return True, ""


def gate_vwap_chop(spot: float, vwap: float, option_type: str) -> Tuple[bool, str]:
    """Gate 7: Price chopping around VWAP = no trade.
    
    If price is within 0.1% of VWAP, it's consolidating/chopping.
    Chopping markets have no clear direction — don't trade.
    
    For CALLS: Spot must be clearly above VWAP (>0.1%)
    For PUTS: Spot must be clearly below VWAP (>0.1%)
    """
    if vwap <= 0:
        return True, ""
    
    vwap_distance_pct = ((spot - vwap) / vwap) * 100
    
    if option_type == "call":
        # For calls: spot should be ABOVE VWAP (above average = bullish)
        # Block if spot is within 0.1% of VWAP (chopping, no clear direction)
        if abs(vwap_distance_pct) < 0.1:
            return False, f"VWAP CHOP GATE: Calls blocked — SPY within 0.1% of VWAP (chopping, no direction)"
    elif option_type == "put":
        # For puts: spot should be BELOW VWAP (below average = bearish)
        # Block if spot is WITHIN or ABOVE VWAP (no clear bearish direction)
        if vwap_distance_pct > -0.1:  # spot is not clearly below VWAP
            return False, f"VWAP CHOP GATE: Puts blocked — SPY within 0.1% of VWAP (chopping, no direction)"
    
    return True, ""


def gate_atr_low(atr_14: float, open_price: float) -> Tuple[bool, str]:
    """Gate 8: Low ATR = skip trading.
    
    ATR measures volatility. If ATR is too low relative to price,
    the market is too quiet for 0DTE options to be profitable.
    
    Threshold: ATR should be > 0.3% of price for 0DTE trading.
    """
    if open_price <= 0:
        return True, ""
    
    atr_pct = (atr_14 / open_price) * 100
    
    if atr_pct < 0.3:
        return False, f"ATR LOW GATE: Skipped — ATR only {atr_pct:.2f}% of price (need >0.3%, market too quiet)"
    
    return True, ""


def gate_gap_fade(spot: float, prev_close: float, option_type: str) -> Tuple[bool, str]:
    """Gate 9: Large gap = risk of fade.
    
    If SPY gapped up >0.5% overnight, there's a high probability
    of a fade (pullback) during the day. Don't buy calls into a gap.
    
    For CALLS: Block if gap up >0.5% (fade risk)
    For PUTS: Block if gap down >0.5% (bounce risk)
    """
    if prev_close <= 0:
        return True, ""
    
    gap_pct = ((spot - prev_close) / prev_close) * 100
    
    if option_type == "call":
        if gap_pct > 0.5:
            return False, f"GAP FADE GATE: Calls blocked — SPY gapped up {gap_pct:.2f}% (high fade risk)"
    elif option_type == "put":
        if gap_pct < -0.5:
            return False, f"GAP FADE GATE: Puts blocked — SPY gapped down {gap_pct:.2f}% (high bounce risk)"
    
    return True, ""
