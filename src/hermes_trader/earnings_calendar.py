"""Earnings Calendar Integration — avoid trading before earnings.

Key insight: Options IV spikes 2-5 days before earnings.
After earnings, IV drops 30-60% (IV crush).
Don't sell premium before earnings unless specifically playing the crush.
"""

import yfinance as yf
from datetime import datetime, timedelta


def check_earnings(symbol: str, days_ahead: int = 7) -> dict:
    """Check if symbol has earnings coming up."""
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or cal.empty:
            return {"has_earnings": False, "symbol": symbol}

        # Get next earnings date
        next_earnings = cal.index[0] if len(cal) > 0 else None
        if next_earnings is None:
            return {"has_earnings": False, "symbol": symbol}

        today = datetime.utcnow().date()
        earnings_date = next_earnings.date() if hasattr(next_earnings, 'date') else next_earnings
        days_until = (earnings_date - today).days

        in_danger_zone = 0 <= days_until <= days_ahead

        return {
            "has_earnings": True,
            "symbol": symbol,
            "earnings_date": str(earnings_date),
            "days_until": days_until,
            "in_danger_zone": in_danger_zone,
            "recommendation": "AVOID selling premium" if in_danger_zone else "OK to trade",
            "earnings_this_week": days_until <= 7,
        }
    except Exception as e:
        return {"has_earnings": False, "symbol": symbol, "error": str(e)}


def check_watchlist_earnings(symbols: list = None) -> list:
    """Check earnings for entire watchlist."""
    if symbols is None:
        symbols = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL", "AMZN"]

    results = []
    for sym in symbols:
        result = check_earnings(sym)
        results.append(result)

    danger = [r for r in results if r.get("in_danger_zone")]
    upcoming = [r for r in results if r.get("earnings_this_week")]

    return {
        "results": results,
        "danger_count": len(danger),
        "upcoming_count": len(upcoming),
        "danger_symbols": [r["symbol"] for r in danger],
        "recommendation": "REDUCE exposure" if danger else "No earnings risk this week",
    }
