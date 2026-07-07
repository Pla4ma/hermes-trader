"""News/Catalyst Awareness — Avoids trading during high-impact events.

Economic calendar integration:
- FOMC announcements (8x/year)
- Non-Farm Payrolls (NFP, 1st Friday monthly)
- CPI inflation reports (monthly)
- PPI, GDP, retail sales
- Major earnings (SPY components)

Integration with auto_trader:
- Block trades 30min before/after high-impact events
- Reduce position size during moderate-impact events
- Skip trading on event days entirely if risk-averse

Data sources:
- FRED API (free, requires key)
- Yahoo Finance earnings calendar
- Hardcoded economic calendar dates
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.news_catalyst")

ET = ZoneInfo("America/New_York")

# ── High-Impact Event Calendar (2026) ──
# Hardcoded for reliability — no API dependency
HIGH_IMPACT_EVENTS_2026 = [
    # FOMC Announcement Days (approximate — adjust based on actual Fed schedule)
    {"date": "2026-01-28", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    {"date": "2026-03-18", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    {"date": "2026-05-06", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    {"date": "2026-06-17", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    {"date": "2026-07-29", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    {"date": "2026-09-16", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    {"date": "2026-10-28", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    {"date": "2026-12-16", "event": "FOMC", "impact": "HIGH", "time": "14:00"},
    
    # NFP (Non-Farm Payrolls) — 1st Friday of each month
    {"date": "2026-01-09", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-02-06", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-03-06", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-04-03", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-05-08", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-06-05", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-07-02", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-08-07", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-09-04", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-10-02", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-11-06", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-12-04", "event": "NFP", "impact": "HIGH", "time": "08:30"},
    
    # CPI Reports (monthly, ~10th-13th)
    {"date": "2026-01-14", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-02-11", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-03-11", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-04-10", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-05-12", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-06-10", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-07-14", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-08-11", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-09-10", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-10-13", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-11-10", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    {"date": "2026-12-09", "event": "CPI", "impact": "HIGH", "time": "08:30"},
    
    # PPI Reports (day after CPI typically)
    {"date": "2026-01-15", "event": "PPI", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-02-12", "event": "PPI", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-03-12", "event": "PPI", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-04-11", "event": "PPI", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-05-13", "event": "PPI", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-06-11", "event": "PPI", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-07-15", "event": "PPI", "impact": "MEDIUM", "time": "08:30"},
    
    # GDP Reports (quarterly)
    {"date": "2026-01-29", "event": "GDP", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-04-30", "event": "GDP", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-07-30", "event": "GDP", "impact": "MEDIUM", "time": "08:30"},
    {"date": "2026-10-29", "event": "GDP", "impact": "MEDIUM", "time": "08:30"},
]

# Block window: 30 minutes before and after high-impact events
HIGH_IMPACT_BLOCK_MINUTES = 30
MEDIUM_IMPACT_BLOCK_MINUTES = 15


def get_events_for_date(date_str: str = None) -> List[Dict]:
    """Get all economic events for a given date.
    
    Args:
        date_str: Date in YYYY-MM-DD format. Defaults to today.
    
    Returns:
        List of event dicts with 'event', 'impact', 'time' keys.
    """
    if date_str is None:
        date_str = datetime.now(ET).strftime("%Y-%m-%d")
    
    return [e for e in HIGH_IMPACT_EVENTS_2026 if e["date"] == date_str]


def is_high_impact_event(date_str: str = None, check_window: bool = True) -> Tuple[bool, str]:
    """Check if current time is during a high-impact event window.
    
    Args:
        date_str: Date to check. Defaults to today.
        check_window: If True, check the ±30min window around events.
    
    Returns:
        (is_blocked, reason) tuple.
    """
    now = datetime.now(ET)
    if date_str is None:
        date_str = now.strftime("%Y-%m-%d")
    
    events = get_events_for_date(date_str)
    
    for event in events:
        event_time = datetime.strptime(
            f"{event['date']} {event['time']}", 
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ET)
        
        block_minutes = HIGH_IMPACT_BLOCK_MINUTES if event["impact"] == "HIGH" else MEDIUM_IMPACT_BLOCK_MINUTES
        
        if check_window:
            window_start = event_time - timedelta(minutes=block_minutes)
            window_end = event_time + timedelta(minutes=block_minutes)
            
            if window_start <= now <= window_end:
                return True, f"{event['impact']} impact: {event['event']} at {event['time']} ET (blocked ±{block_minutes}min)"
        else:
            # Just check if it's the same date with a high-impact event
            if event["impact"] == "HIGH":
                return True, f"High-impact event today: {event['event']}"
    
    return False, ""


def get_next_event() -> Optional[Dict]:
    """Get the next upcoming economic event.
    
    Returns:
        Next event dict with 'date', 'event', 'impact', 'time', 'hours_until' keys.
    """
    now = datetime.now(ET)
    today_str = now.strftime("%Y-%m-%d")
    
    # Check today's events first
    for event in get_events_for_date(today_str):
        event_time = datetime.strptime(
            f"{event['date']} {event['time']}", 
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ET)
        
        if event_time > now:
            hours_until = (event_time - now).total_seconds() / 3600
            return {**event, "hours_until": round(hours_until, 1)}
    
    # Check future dates (next 7 days)
    for days_ahead in range(1, 8):
        future_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        future_events = get_events_for_date(future_date)
        if future_events:
            event = future_events[0]  # First event of the day
            event_time = datetime.strptime(
                f"{event['date']} {event['time']}", 
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=ET)
            hours_until = (event_time - now).total_seconds() / 3600
            return {**event, "hours_until": round(hours_until, 1)}
    
    return None


def should_block_trade(reason_out: List[str] = None) -> bool:
    """Determine if trading should be blocked due to upcoming events.
    
    Returns:
        True if trading should be blocked.
    """
    is_blocked, block_reason = is_high_impact_event()
    if is_blocked:
        if reason_out is not None:
            reason_out.append(block_reason)
        logger.warning(f"TRADING BLOCKED: {block_reason}")
        return True
    
    # Also block if high-impact event is within 1 hour
    next_event = get_next_event()
    if next_event and next_event["impact"] == "HIGH" and next_event["hours_until"] < 1.0:
        reason = f"High-impact event in {next_event['hours_until']}h: {next_event['event']}"
        if reason_out is not None:
            reason_out.append(reason)
        logger.warning(f"TRADING BLOCKED: {reason}")
        return True
    
    return False


def get_position_size_multiplier() -> float:
    """Get position size multiplier based on event proximity.
    
    Returns:
        Multiplier between 0.0 and 1.0:
        - 0.0 = no trading (blocked)
        - 0.5 = reduced size (moderate event nearby)
        - 1.0 = full size (no events)
    """
    is_blocked, _ = is_high_impact_event()
    if is_blocked:
        return 0.0
    
    next_event = get_next_event()
    if next_event:
        if next_event["impact"] == "HIGH":
            if next_event["hours_until"] < 2.0:
                return 0.5
            elif next_event["hours_until"] < 4.0:
                return 0.75
        elif next_event["impact"] == "MEDIUM":
            if next_event["hours_until"] < 1.0:
                return 0.75
    
    return 1.0


def format_event_summary(date_str: str = None) -> str:
    """Format a human-readable summary of today's events."""
    events = get_events_for_date(date_str)
    if not events:
        return "No scheduled economic events today."
    
    lines = [f"📅 Economic Events ({date_str or 'today'}):"]
    for e in sorted(events, key=lambda x: x["time"]):
        emoji = "🔴" if e["impact"] == "HIGH" else "🟡"
        lines.append(f"  {emoji} {e['time']} ET — {e['event']} ({e['impact']})")
    
    next_event = get_next_event()
    if next_event:
        lines.append(f"\n⏰ Next: {next_event['event']} in {next_event['hours_until']}h")
    
    return "\n".join(lines)
