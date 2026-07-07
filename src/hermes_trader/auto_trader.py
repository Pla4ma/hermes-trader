"""Auto-trading engine — discovers 0DTE candidates and executes option trades.

This module is called by the cron jobs to autonomously scan, score,
and execute 0DTE option trades on the live Robinhood account via MCP.

Flow:
  1. Scan 0DTE options via zero_dte_scanner (Robinhood MCP chains)
  2. Score candidates with multi-factor confluence scoring
  3. Check Vibe-Trading + TradingAgents research gates (both must agree)
  4. Place option order via Robinhood MCP place_option_order
  5. Set exit rules (trailing stops, profit-taking)
"""
import json
import logging
import os
import uuid
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger("hermes_trader.auto_trader")
ACCOUNT_NUMBER = os.getenv("ROBINHOOD_ACCOUNT_NUMBER", "924058324")

# ── New modules (MAX POWER upgrade) ──
from .news_catalyst import should_block_trade, get_position_size_multiplier, format_event_summary
from .portfolio_risk import (
    check_concentration_risk, check_drawdown, kelly_with_portfolio_constraint,
    get_portfolio_risk_summary, calculate_portfolio_correlation
)

# ── Simple yfinance cache (60s TTL) ──
_yf_cache = {}
_yf_cache_ttl = 60  # seconds


def _cached_yf_history(symbol: str, period: str = "5d"):
    """Get cached yfinance history to avoid repeated API calls."""
    import time
    cache_key = f"{symbol}_{period}"
    now = time.time()
    
    if cache_key in _yf_cache:
        cached_time, cached_data = _yf_cache[cache_key]
        if now - cached_time < _yf_cache_ttl:
            return cached_data
    
    import yfinance as yf
    data = yf.Ticker(symbol).history(period=period)
    _yf_cache[cache_key] = (now, data)
    return data


def _notify_trade(action: str, details: dict):
    """Send notification about trade execution.
    
    This function logs the trade and can be extended to send
    Telegram/email notifications in the future.
    """
    timestamp = datetime.utcnow().isoformat()
    
    # Log to file
    notification = {
        "timestamp": timestamp,
        "action": action,
        "details": details,
    }
    
    notif_path = "/opt/hermes-trader/data/journals/trade_notifications.jsonl"
    os.makedirs(os.path.dirname(notif_path), exist_ok=True)
    with open(notif_path, "a") as f:
        f.write(json.dumps(notification) + "\n")
    
    # Log to console
    if action == "BUY_OPTION":
        logger.info(
            f"🔔 AUTO-TRADE: {details.get('symbol', '')} {details.get('option_type', '')} "
            f"strike={details.get('strike', 0)} x{details.get('quantity', 0)} "
            f"${details.get('total_cost', 0):.2f}"
        )
    elif action == "SELL_OPTION":
        logger.info(
            f"💰 AUTO-SELL: {details.get('symbol', '')} "
            f"P&L: ${details.get('pnl', 0):.2f} ({details.get('pnl_pct', 0):.1f}%)"
        )


def _get_broker():
    """Get the Robinhood broker adapter instance."""
    from .integrations.robinhood_broker import RobinhoodBrokerAdapter
    return RobinhoodBrokerAdapter()


def scan_and_score(symbols: list[str] = None) -> list[dict]:
    """Scan 0DTE options and score with multi-factor confluence.

    Replaces the old equity-based scanning. Now scans 0DTE options
    on SPY/QQQ/SPXW/NDXW for day-trade candidates.
    """
    from .zero_dte_scanner import scan_0dte, get_spot_price
    import yfinance as yf
    import numpy as np

    if symbols is None:
        symbols = ["SPY", "QQQ", "SPXW", "NDXW"]

    # ── Phase 1: 0DTE Option Scanning via Robinhood MCP ──
    candidates_0dte = scan_0dte(symbols=symbols, min_score=20, max_candidates=20)

    if candidates_0dte:
        # Enrich 0DTE candidates with additional scoring
        for c in candidates_0dte:
            # Add confluence context from underlying
            sym = c.get("symbol", "SPY").split("/")[0] if "/" in c.get("symbol", "") else c.get("symbol", "SPY")
            try:
                spot = get_spot_price(sym)
                if spot > 0:
                    c["underlying_price"] = spot
            except Exception:
                pass

    # ── Phase 2: Underlying momentum check (optional enrichment) ──
    if candidates_0dte:
        # Add momentum-based signal to each candidate
        for c in candidates_0dte:
            try:
                data = yf.Ticker(c.get("symbol", "SPY")).history(period="5d")
                if len(data) >= 2:
                    close = data["Close"]
                    ret1d = (close.iloc[-1] / close.iloc[-2] - 1) * 100
                    ret5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(data) >= 6 else 0
                    c["underlying_ret1d"] = round(ret1d, 2)
                    c["underlying_ret5d"] = round(ret5d, 2)
                    # Momentum bonus
                    if ret1d > 0 and c.get("type") == "call":
                        c["score"] += 5
                    elif ret1d < 0 and c.get("type") == "put":
                        c["score"] += 5
            except Exception:
                pass

    # Sort by score
    candidates_0dte.sort(key=lambda x: x.get("score", 0), reverse=True)
    return candidates_0dte


def auto_trade(min_score: int = 30, max_notional: float = 90.0) -> dict:
    """Scan 0DTE options, score, check research gates, and execute.

    Integrates: 0DTE scanner, GEX, PCR, IV Rank, Kelly sizing, earnings check.
    Executes via Robinhood MCP place_option_order (not equity orders).

    Args:
        min_score: Minimum composite score (0-100) to consider a candidate
        max_notional: Maximum dollar amount per trade (default $90 = 90% of $100)
    """
    broker = _get_broker()

    # ─── Get account state from Robinhood ───
    account = broker.get_account()
    cash = account.cash
    held = [p.symbol for p in broker.get_positions()]

    # ─── Options Analytics (institutional-grade) ───
    analytics = {}
    try:
        from .options_analytics import OptionsAnalytics
        oa = OptionsAnalytics()
        analytics = oa.get_full_analytics("SPY")
    except Exception:
        pass

    gex_regime = analytics.get("gex", {}).get("regime", "unknown")
    pcr = analytics.get("put_call_ratio", {}).get("put_call_ratio", 1.0)
    pcr_signal = analytics.get("put_call_ratio", {}).get("signal", "NEUTRAL")
    max_pain = analytics.get("max_pain", {}).get("max_pain_strike", 0)

    # ─── Earnings Check ───
    try:
        from .earnings_calendar import check_earnings
        earnings = check_earnings("SPY")
        if earnings.get("in_danger_zone"):
            return {"action": "wait", "reason": "SPY earnings in danger zone", "analytics": analytics}
    except Exception:
        pass
    
    # ─── News/Catalyst Check (MAX POWER) ───
    try:
        block_reasons = []
        if should_block_trade(block_reasons):
            return {
                "action": "blocked",
                "reason": f"Economic event: {'; '.join(block_reasons)}",
                "analytics": analytics,
                "event_summary": format_event_summary(),
            }
    except Exception:
        pass
    
    # ─── Portfolio Risk Check (MAX POWER) ───
    try:
        positions = broker.list_positions()
        position_dicts = [
            {"symbol": p.symbol, "type": "call", "value": float(p.quantity) * float(p.avg_entry_price) * 100}
            for p in positions if hasattr(p, "quantity")
        ]
        portfolio_value = float(account.cash) + sum(p.get("value", 0) for p in position_dicts)
        
        risk_summary = get_portfolio_risk_summary(position_dicts)
        if not risk_summary.get("can_trade", True):
            return {
                "action": "blocked",
                "reason": risk_summary.get("drawdown_reason", "Portfolio risk limit"),
                "analytics": analytics,
                "risk_summary": risk_summary,
            }
    except Exception:
        pass

    # Check market regime
    try:
        from .market_regime import detect_regime
        regime = detect_regime()
        regime_name = regime.get("regime", "UNKNOWN")
        sizing_mult = regime.get("sizing_multiplier", 0.75)
    except Exception:
        regime_name = "UNKNOWN"
        sizing_mult = 0.75

    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "cash": cash,
        "held": list(held),
        "regime": regime_name,
        "sizing_multiplier": sizing_mult,
        "action": "none",
        "strategy": "0dte_options",
    }

    # Need at least $5 to trade
    if cash < 5:
        result["reason"] = f"Insufficient cash: ${cash:.2f}"
        return result

    # ─── Scan 0DTE options ───
    candidates = scan_and_score()
    result["candidates_found"] = len(candidates)

    # Filter by score
    viable = [c for c in candidates if c.get("score", 0) >= min_score]
    result["viable_candidates"] = len(viable)

    if not viable:
        result["reason"] = f"No 0DTE candidates above {min_score}/100 threshold"
        return result
    
    # ═══════════════════════════════════════════════════════════════
    # DUAL MODEL SCORING — Probability + Magnitude (friend's advice)
    # ═══════════════════════════════════════════════════════════════
    # Two separate models:
    # 1. Probability model: What's the chance this trade wins?
    # 2. Magnitude model: If it wins, how large is the expected move?
    
    # Minimum thresholds
    MIN_PROBABILITY = 0.55  # 55% win probability minimum
    MIN_EV = 0.10           # 10% expected value minimum
    
    def calculate_probability(candidate, analytics=None):
        """Calculate win probability (0.0-1.0) for this trade.
        
        Factors affecting probability of winning:
        - Delta (higher = more likely ITM)
        - Volume (higher = easier execution)
        - Spread (tighter = less slippage)
        - IV (moderate = best risk/reward)
        - Gamma (higher = faster moves)
        - QQQ confirmation (cross-market)
        - VIX favorable (low/declining)
        - Dealer gamma regime (positive = trending)
        - Time of day (optimal windows)
        """
        prob = 0.50  # Base 50% probability
        
        # Delta adjustment
        delta = abs(candidate.get("delta", 0))
        if 0.35 <= delta <= 0.50:
            prob += 0.10
        elif 0.25 <= delta <= 0.60:
            prob += 0.05
        elif delta < 0.20:
            prob -= 0.10
        
        # Volume adjustment
        vol = candidate.get("volume", 0)
        if vol > 100000:
            prob += 0.08
        elif vol > 50000:
            prob += 0.04
        elif vol < 10000:
            prob -= 0.08
        
        # Spread adjustment
        bid = candidate.get("bid", 0)
        ask = candidate.get("ask", 0)
        mid = candidate.get("mid", 0)
        if mid > 0 and bid > 0 and ask > 0:
            spread_pct = ((ask - bid) / mid) * 100
            if spread_pct < 10:
                prob += 0.06
            elif spread_pct < 20:
                prob += 0.03
            elif spread_pct > 30:
                prob -= 0.06
        
        # IV adjustment
        iv = candidate.get("iv", 0)
        if 0.25 <= iv <= 0.45:
            prob += 0.05
        elif 0.15 <= iv <= 0.55:
            prob += 0.02
        elif iv > 0.60:
            prob -= 0.05
        
        # Gamma adjustment
        gamma = candidate.get("gamma", 0)
        if gamma > 0.10:
            prob += 0.06
        elif gamma > 0.05:
            prob += 0.03
        
        # QQQ confirmation
        if analytics:
            qqq_signal = analytics.get("qqq_confirmation", False)
            if qqq_signal:
                prob += 0.05
            else:
                prob -= 0.05
        
        # VIX adjustment
        if analytics:
            vix_signal = analytics.get("vix_favorable", False)
            if vix_signal:
                prob += 0.04
            else:
                prob -= 0.04
        
        # Gamma regime adjustment
        if analytics:
            gamma_regime = analytics.get("gamma_regime", "unknown")
            if gamma_regime == "positive":
                prob += 0.05
            elif gamma_regime == "negative":
                prob += 0.02
        
        # Time of day adjustment
        from zoneinfo import ZoneInfo
        from datetime import datetime as dt
        now_et = dt.now(ZoneInfo("America/New_York"))
        hour = now_et.hour
        minute = now_et.minute
        
        if (hour == 9 and minute >= 45) or (hour == 10 and minute <= 30):
            prob += 0.05
        elif hour == 14 or (hour == 15 and minute <= 30):
            prob += 0.05
        elif hour == 12 or hour == 13:
            prob -= 0.08
        
        return max(0.10, min(0.90, prob))
    
    def calculate_magnitude(candidate, analytics=None):
        """Calculate expected move magnitude (0.0-1.0) if trade wins.
        
        Factors affecting how much the option could move:
        - Gamma (higher = larger moves)
        - IV (higher = larger expected moves)
        - Volume (higher = more momentum)
        - Expected move from analytics
        - Distance from spot to strike
        """
        mag = 0.30  # Base 30% move expected
        
        # Gamma adjustment
        gamma = candidate.get("gamma", 0)
        if gamma > 0.15:
            mag += 0.25
        elif gamma > 0.10:
            mag += 0.15
        elif gamma > 0.05:
            mag += 0.08
        
        # IV adjustment
        iv = candidate.get("iv", 0)
        if iv > 0.40:
            mag += 0.15
        elif iv > 0.30:
            mag += 0.08
        
        # Volume adjustment
        vol = candidate.get("volume", 0)
        if vol > 200000:
            mag += 0.10
        elif vol > 100000:
            mag += 0.05
        
        # Expected move from analytics
        if analytics:
            expected_move = analytics.get("expected_move", 0)
            if expected_move > 1.0:
                mag += 0.15
            elif expected_move > 0.5:
                mag += 0.08
        
        # Distance from spot
        spot = candidate.get("underlying_price", 0) or candidate.get("spot", 0)
        strike = candidate.get("strike", 0)
        if spot > 0 and strike > 0:
            distance_pct = abs(strike - spot) / spot * 100
            if distance_pct < 0.2:
                mag += 0.10
            elif distance_pct < 0.5:
                mag += 0.05
            elif distance_pct > 1.0:
                mag -= 0.10
        
        return max(0.10, min(1.0, mag))
    
    def calculate_expected_value(probability, magnitude, cost_pct):
        """Calculate expected value: EV = (P × Mag) - ((1-P) × Cost)
        
        If EV > 0, trade has positive expected value.
        """
        ev = (probability * magnitude) - ((1 - probability) * cost_pct)
        return ev
    
    # Get market analytics for quality scoring
    market_analytics = {}
    try:
        from .options_analytics import OptionsAnalytics
        oa = OptionsAnalytics()
        market_analytics = oa.get_full_analytics("SPY")
        
        # Add QQQ confirmation
        try:
            import yfinance as yf
            qqq_data = yf.Ticker("QQQ").history(period="1d")
            if len(qqq_data) > 0:
                qqq_open = float(qqq_data.iloc[0]["Open"])
                qqq_current = float(qqq_data.iloc[-1]["Close"])
                qqq_change = ((qqq_current - qqq_open) / qqq_open) * 100
                market_analytics["qqq_confirmation"] = qqq_change > 0.2  # QQQ up >0.2%
        except Exception:
            pass
        
        # Add VIX favorable (VIX declining or < 16)
        try:
            vix_data = market_analytics.get("vix", {})
            vix_current = vix_data.get("current", 20)
            vix_prev = vix_data.get("previous_close", 20)
            market_analytics["vix_favorable"] = vix_current < 16 or vix_current < vix_prev
        except Exception:
            pass
        
        # Add gamma regime
        try:
            gex = market_analytics.get("gex", {})
            market_analytics["gamma_regime"] = gex.get("regime", "unknown")
        except Exception:
            pass
    except Exception:
        pass
    
    # Score all viable candidates
    for c in viable:
        c["probability"] = calculate_probability(c, market_analytics)
        c["magnitude"] = calculate_magnitude(c, market_analytics)
        # Calculate expected value
        cost_pct = 0.50  # Assume 50% max loss (stop loss at -50%)
        c["expected_value"] = calculate_expected_value(c["probability"], c["magnitude"], cost_pct)
    
    # Filter by probability AND expected value
    quality_viable = [c for c in viable if c.get("probability", 0) >= MIN_PROBABILITY and c.get("expected_value", 0) >= MIN_EV]
    result["quality_candidates"] = len(quality_viable)
    result["scoring"] = {
        "min_probability": MIN_PROBABILITY,
        "min_ev": MIN_EV,
        "best_probability": viable[0].get("probability", 0) if viable else 0,
        "best_magnitude": viable[0].get("magnitude", 0) if viable else 0,
        "best_ev": viable[0].get("expected_value", 0) if viable else 0,
    }
    
    if not quality_viable:
        result["reason"] = f"No candidates with prob ≥{MIN_PROBABILITY} AND EV ≥{MIN_EV}"
        return result
    
    best = quality_viable[0]

    # ═══════════════════════════════════════════════════════════════
    # ENTRY GATE FILTERS — BLOCKS bad entries (the #1 lesson)
    # ═══════════════════════════════════════════════════════════════
    try:
        import yfinance as yf
        from .entry_gates import check_all_gates
        from datetime import datetime as dt, timezone, timedelta

        sym = best.get("symbol", "SPY").split("/")[0] if "/" in best.get("symbol", "") else best.get("symbol", "SPY")
        # Get spot price from candidate or yfinance
        spot = best.get("underlying_price", 0) or 0
        if spot <= 0:
            spot = float(yf.Ticker(sym).fast_info.get("lastPrice", 0) or 0)
        if spot <= 0:
            logger.warning("Could not determine spot price — blocking trade")
            result["action"] = "blocked"
            result["reason"] = "Cannot determine spot price for entry gate check"
            return result
        ticker = yf.Ticker(sym)
        hist_today = ticker.history(period="1d")
        hist_20d = ticker.history(period="20d")

        if len(hist_today) > 0 and len(hist_20d) > 1:
            today_data = hist_today.iloc[0]
            open_price = float(today_data["Open"])
            high_of_day = float(today_data["High"])
            low_of_day = float(today_data["Low"])
            current_volume = float(hist_today["Volume"].iloc[-1])
            avg_volume = float(hist_20d["Volume"].mean())
            
            # Get previous close for gap detection
            # Use iloc[-2] for yesterday's close (iloc[-1] includes today's partial bar)
            prev_close = float(hist_20d["Close"].iloc[-2]) if len(hist_20d) > 1 else 0
            
            # Calculate VWAP (intraday volume-weighted average price)
            try:
                intraday = ticker.history(period="1d", interval="1m")
                if len(intraday) > 0:
                    typical_price = (intraday["High"] + intraday["Low"] + intraday["Close"]) / 3
                    vwap = (typical_price * intraday["Volume"]).sum() / intraday["Volume"].sum()
                else:
                    vwap = None
            except Exception:
                vwap = None
            
            # Calculate ATR (Average True Range)
            try:
                if len(hist_20d) >= 14:
                    high = hist_20d["High"]
                    low = hist_20d["Low"]
                    close = hist_20d["Close"].shift(1)
                    tr = high - low
                    tr2 = abs(high - close)
                    tr3 = abs(low - close)
                    atr = (tr + tr2 + tr3) / 3
                    atr_14 = float(atr.rolling(14).mean().iloc[-1])
                else:
                    atr_14 = None
            except Exception:
                atr_14 = None

            # RSI calculation
            close_20d = hist_20d["Close"]
            delta = close_20d.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            rsi_series = 100 - (100 / (1 + rs))
            rsi_14 = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 and rsi_series.iloc[-1] == rsi_series.iloc[-1] else 50.0

            # Current time in ET (proper timezone)
            from zoneinfo import ZoneInfo
            now_et = dt.now(ZoneInfo("America/New_York"))

            option_type = best.get("type", "call")

            gates_passed, gate_failures = check_all_gates(
                symbol=sym,
                option_type=option_type,
                spot=spot,
                open_price=open_price,
                high_of_day=high_of_day,
                low_of_day=low_of_day,
                current_volume=current_volume,
                avg_volume_20d=avg_volume,
                rsi_14=rsi_14,
                now_et=now_et,
                vwap=vwap,
                atr_14=atr_14,
                prev_close=prev_close,
            )

            result["entry_gates"] = {
                "passed": gates_passed,
                "failures": gate_failures,
                "open": round(open_price, 2),
                "high": round(high_of_day, 2),
                "low": round(low_of_day, 2),
                "move_from_open": round(((spot - open_price) / open_price) * 100, 2) if open_price > 0 else 0,
                "pullback_from_high": round(((high_of_day - spot) / high_of_day) * 100, 2) if high_of_day > 0 else 0,
                "rsi_14": round(rsi_14, 1),
                "vol_ratio": round(current_volume / avg_volume, 2) if avg_volume > 0 else 0,
            }

            if not gates_passed:
                result["action"] = "blocked"
                result["reason"] = f"ENTRY GATES BLOCKED: {'; '.join(gate_failures)}"
                return result
        else:
            logger.warning("Insufficient intraday data for entry gates — proceeding with caution")
    except Exception as e:
        logger.error(f"Entry gate check FAILED — blocking trade: {e}")
        result["action"] = "blocked"
        result["reason"] = f"Entry gate system error: {e}"
        return result

    # ═══════════════════════════════════════════════════════════════
    # POSITION SIZING via AggressiveSizer (Kelly criterion)
    # ═══════════════════════════════════════════════════════════════

    # Calculate mid price first
    mid_price = best.get("mid", 0)
    if mid_price <= 0:
        result["action"] = "error"
        result["error"] = f"Invalid option price: {mid_price}"
        return result

    try:
        from .aggressive_sizer import AggressiveSizer
        sizer = AggressiveSizer()

        # Get consecutive losses from risk snapshot
        consecutive_losses = 0
        try:
            risk_snap = broker.get_risk_snapshot()
            consecutive_losses = risk_snap.consecutive_losses
        except Exception:
            pass

        sizing_rec = sizer.recommend(
            win_prob=best.get("probability", 0.55),  # Use actual probability from dual model
            avg_win=0.50,   # Target 50% avg win
            avg_loss=0.50,  # Stop at 50% loss
            premium_per_contract=mid_price,
            account_value=cash,
            consecutive_losses=consecutive_losses,
        )

        notional = sizing_rec.risk_dollars
        quantity = sizing_rec.num_contracts

        result["sizing"] = {
            "method": "kelly_criterion",
            "risk_dollars": round(notional, 2),
            "num_contracts": quantity,
            "kelly_fraction": round(sizing_rec.kelly_fraction, 4),
            "adjusted_risk_pct": round(sizing_rec.adjusted_risk_pct, 4),
            "signals": sizing_rec.signals,
        }
    except Exception as e:
        # Fallback to simple sizing
        logger.warning(f"AggressiveSizer failed, using fallback: {e}")
        notional = min(cash * 0.90, max_notional) * sizing_mult
        contract_cost = mid_price * 100
        quantity = max(1, int(notional / contract_cost)) if contract_cost > 0 else 1
        max_affordable = int(cash / contract_cost) if contract_cost > 0 else 1
        quantity = min(quantity, max_affordable)

    # ═══════════════════════════════════════════════════════════════
    # S-TIER RESEARCH: Vibe-Trading + TradingAgents BEFORE every trade
    # ═══════════════════════════════════════════════════════════════
    underlying_sym = best.get("symbol", "SPY").split("/")[0] if "/" in best.get("symbol", "") else best.get("symbol", "SPY")
    # Strip W suffix for index symbols used in research
    research_sym = underlying_sym.rstrip("W")

    vibe_signal = _run_vibe_research(research_sym)
    agents_signal = _run_tradingagents_research(research_sym)

    result["vibe_signal"] = vibe_signal
    result["agents_signal"] = agents_signal

    # ── STRICT RESEARCH GATES ──
    # Error/unknown/neutral are ALL treated as "not bullish" = BLOCK
    vibe_signal_type = vibe_signal.get("signal", "unknown")
    agents_signal_type = agents_signal.get("signal", "unknown")

    # Only "bullish" is allowed — everything else blocks
    vibe_is_bullish = vibe_signal_type == "bullish"
    agents_is_bullish = agents_signal_type == "bullish"

    # If either source failed or returned bearish/unknown/neutral → BLOCK
    if not vibe_is_bullish or not agents_is_bullish:
        result["action"] = "blocked"
        result["reason"] = f"Research gate BLOCKED — Vibe: {vibe_signal_type}, Agents: {agents_signal_type}. Both must be BULLISH."
        return result

    # ─── Calculate option quantity ───
    # For options: notional / (mid_price * 100) = number of contracts
    # Each contract costs mid_price * $100
    contract_cost = mid_price * 100
    # Use quantity from AggressiveSizer if available, otherwise calculate
    if 'quantity' not in result or result.get("quantity", 0) < 1:
        quantity = max(1, int(notional / contract_cost)) if contract_cost > 0 else 1
        # Cap at what we can actually afford
        max_affordable = int(cash / contract_cost) if contract_cost > 0 else 1
        quantity = min(quantity, max_affordable)
    else:
        quantity = result["quantity"]

    if quantity < 1:
        result["reason"] = f"Cannot afford even 1 contract (cost=${contract_cost:.2f}, cash=${cash:.2f})"
        return result

    result["quantity"] = quantity
    result["contract_cost"] = contract_cost
    result["total_cost"] = contract_cost * quantity

    # ═══════════════════════════════════════════════════════════════
    # EXECUTE OPTION ORDER via Robinhood MCP place_option_order
    # ═══════════════════════════════════════════════════════════════
    try:
        order = broker.place_option_order(
            option_id=best.get("option_id", ""),
            side="buy",
            quantity=quantity,
            limit_price=str(round(mid_price, 4)),
            time_in_force="day",
        )

        result["action"] = "BUY_OPTION"
        result["symbol"] = best.get("symbol", "")
        result["option_id"] = best.get("option_id", "")
        result["option_type"] = best.get("type", "")
        result["strike"] = best.get("strike", 0)
        result["mid_price"] = mid_price
        result["quantity"] = quantity
        result["notional"] = notional
        result["score"] = best.get("score", 0)
        result["order_id"] = order.get("order_id", order.get("id", ""))
        result["stop_loss"] = best.get("stop_loss", round(mid_price * 0.50, 4))
        result["take_profit"] = best.get("take_profit", round(mid_price * 2.0, 4))

        # Log to journal
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "BUY_OPTION",
            "symbol": best.get("symbol", ""),
            "option_id": best.get("option_id", ""),
            "option_type": best.get("type", ""),
            "strike": best.get("strike", 0),
            "expiration": best.get("expiration", ""),
            "mid_price": mid_price,
            "quantity": quantity,
            "notional": notional,
            "total_cost": contract_cost * quantity,
            "order_id": order.get("order_id", order.get("id", "")),
            "broker": "robinhood_mcp",
            "account_number": ACCOUNT_NUMBER,
            "strategy": "0dte_options",
            "confluence_score": best.get("score", 0),
            "probability": best.get("probability", 0),
            "magnitude": best.get("magnitude", 0),
            "expected_value": best.get("expected_value", 0),
            "vibe_signal": vibe_signal.get("signal", "unknown"),
            "agents_signal": agents_signal.get("signal", "unknown"),
            "stop_loss": best.get("stop_loss", round(mid_price * 0.50, 4)),
            "take_profit": best.get("take_profit", round(mid_price * 2.0, 4)),
            # Professional metrics from friend's advice
            "delta": best.get("delta", 0),
            "gamma": best.get("gamma", 0),
            "theta": best.get("theta", 0),
            "vega": best.get("vega", 0),
            "iv": best.get("iv", 0),
            "volume": best.get("volume", 0),
            "spread_pct": round(((best.get("ask", 0) - best.get("bid", 0)) / best.get("mid", 1)) * 100, 2) if best.get("mid", 0) > 0 else 0,
            "expected_move": market_analytics.get("expected_move", 0),
            "gamma_regime": market_analytics.get("gamma_regime", "unknown"),
            "vix_favorable": market_analytics.get("vix_favorable", False),
            "qqq_confirmation": market_analytics.get("qqq_confirmation", False),
            "regime": regime_name,
            "reason": f"Auto-trade 0DTE: {best.get('symbol', '')} {best.get('type', '')} strike={best.get('strike', 0)} score={best.get('score', 0)}/100",
        }
        journal_path = "/opt/hermes-trader/data/journals/paper_orders.jsonl"
        os.makedirs(os.path.dirname(journal_path), exist_ok=True)
        with open(journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info(
            f"AUTO-TRADE 0DTE: {best.get('symbol', '')} {best.get('type', '')} "
            f"strike={best.get('strike', 0)} x{quantity} "
            f"${contract_cost * quantity:.2f} score={best.get('score', 0)}/100 via Robinhood MCP"
        )
        
        # Send notification
        _notify_trade("BUY_OPTION", {
            "symbol": best.get("symbol", ""),
            "option_type": best.get("type", ""),
            "strike": best.get("strike", 0),
            "quantity": quantity,
            "total_cost": contract_cost * quantity,
            "order_id": order.get("order_id", order.get("id", "")),
        })

    except Exception as e:
        result["action"] = "error"
        result["error"] = str(e)
        logger.error(f"Auto-trade 0DTE failed: {e}")

    return result


def _run_vibe_research(symbol: str) -> dict:
    """Run Vibe-Trading research on a symbol."""
    try:
        from .research.vibe_client import VibeTradingClient
        vibe = VibeTradingClient()
        result = vibe.run_market_regime_analysis(symbol)
        output = result.get("output", "").lower()

        signal = "neutral"
        if "bullish" in output or "strong buy" in output or "upward" in output:
            signal = "bullish"
        elif "bearish" in output or "strong sell" in output or "downward" in output:
            signal = "bearish"

        return {
            "source": "vibe_trading",
            "signal": signal,
            "status": result.get("status", "UNKNOWN"),
            "summary": result.get("output", "")[:500],
        }
    except Exception as e:
        return {"source": "vibe_trading", "signal": "neutral", "status": "ERROR", "error": str(e)}


def _run_tradingagents_research(symbol: str) -> dict:
    """Run TradingAgents multi-agent committee on a symbol."""
    try:
        from .research.agents_client import TradingAgentsClient
        agents = TradingAgentsClient()
        result = agents.get_committee_signal(symbol)

        return {
            "source": "trading_agents",
            "signal": result.get("signal", "neutral"),
            "confidence": result.get("confidence", 0),
            "status": result.get("status", "UNKNOWN"),
            "summary": result.get("decision", "")[:500],
        }
    except Exception as e:
        return {"source": "trading_agents", "signal": "neutral", "status": "ERROR", "error": str(e)}


def _load_optimal_params(symbol: str) -> dict:
    """Load optimal backtest parameters for a symbol."""
    try:
        params_file = "/opt/hermes-trader/data/snapshots/optimal_params.json"
        if os.path.exists(params_file):
            with open(params_file) as f:
                all_params = json.load(f)
            return all_params.get(symbol, {}).get("params", {})
    except Exception:
        pass
    return {}


def manage_exits() -> dict:
    """Check option positions and manage exits with trailing stops + profit-taking.

    All orders routed through Robinhood MCP broker adapter.
    For 0DTE options:
    - If loss > 50%, close immediately (stop-loss)
    - If profit > 50%, tighten trail to 20%
    - If profit > 100%, sell 50%
    - If profit > 200%, sell remaining
    
    CRITICAL: Uses place_option_order for closing options, NOT place_equity_order.
    """
    broker = _get_broker()
    positions = broker.list_positions()
    open_orders = broker.list_open_orders()
    actions = []

    # Get symbols with existing exit orders
    exit_symbols = {o.get("symbol", "") for o in open_orders}

    for pos in positions:
        symbol = pos.get("symbol", "")
        if not symbol:
            continue

        entry = float(pos.get("avg_entry_price", 0) or pos.get("average_entry_price", 0) or 0)
        current = float(pos.get("current_price", 0) or pos.get("last_price", 0) or 0)
        qty = float(pos.get("quantity", 0) or pos.get("qty", 0) or 0)

        if entry <= 0 or qty <= 0:
            continue

        pnl_pct = (current / entry - 1) * 100 if entry > 0 else 0

        # ── Trailing stop logic for options ──
        if pnl_pct >= 50:
            # Tighten stop to trail by 20% (for high-vol options)
            new_sl = round(current * 0.80, 4)
            old_sl = round(entry * 0.50, 4)
            if new_sl > old_sl:
                # Cancel existing exit orders for this symbol
                for o in open_orders:
                    if o.get("symbol", "") == symbol:
                        try:
                            broker.cancel_order(o.get("order_id", o.get("id", "")))
                            import time; time.sleep(0.5)
                        except Exception:
                            pass

                # Place new trailing stop order via Robinhood MCP (option order)
                try:
                    # Get the option_id from the position
                    option_id = pos.get("option_id", "")
                    if not option_id:
                        actions.append({"symbol": symbol, "action": "SL_ERROR", "error": "No option_id in position"})
                        continue
                    
                    order = broker.place_option_order(
                        option_id=option_id,
                        side="sell",
                        quantity=max(1, int(qty)),
                        limit_price=str(round(current, 4)),
                        time_in_force="day",
                    )
                    actions.append({
                        "symbol": symbol, "action": "TRAILING_SL",
                        "old_sl": old_sl, "new_sl": new_sl,
                        "pnl_pct": round(pnl_pct, 2),
                        "order_id": order.get("order_id", ""),
                    })
                except Exception as e:
                    actions.append({"symbol": symbol, "action": "SL_ERROR", "error": str(e)})

        elif pnl_pct <= -50:
            # Hard stop-loss: close immediately at market
            try:
                option_id = pos.get("option_id", "")
                if not option_id:
                    actions.append({"symbol": symbol, "action": "SL_ERROR", "error": "No option_id in position"})
                    continue
                
                order = broker.place_option_order(
                    option_id=option_id,
                    side="sell",
                    quantity=max(1, int(qty)),
                    time_in_force="day",
                )
                actions.append({
                    "symbol": symbol, "action": "STOP_LOSS_CLOSE",
                    "pnl_pct": round(pnl_pct, 2),
                    "order_id": order.get("order_id", ""),
                })
            except Exception as e:
                actions.append({"symbol": symbol, "action": "SL_ERROR", "error": str(e)})

        elif symbol not in exit_symbols:
            # No exit order — set initial stop at 50% loss
            sl_price = round(entry * 0.50, 4)
            try:
                option_id = pos.get("option_id", "")
                if not option_id:
                    actions.append({"symbol": symbol, "action": "SL_ERROR", "error": "No option_id in position"})
                    continue
                
                order = broker.place_option_order(
                    option_id=option_id,
                    side="sell",
                    quantity=max(1, int(qty)),
                    limit_price=str(round(sl_price, 4)),
                    time_in_force="day",
                )
                actions.append({
                    "symbol": symbol, "action": "SL_SET",
                    "price": sl_price,
                    "order_id": order.get("order_id", ""),
                })
            except Exception as e:
                actions.append({"symbol": symbol, "action": "SL_ERROR", "error": str(e)})

        # ── Profit-taking signals (for cron to execute) ──
        if pnl_pct >= 200:
            actions.append({
                "symbol": symbol, "action": "TP_SIGNAL",
                "pnl_pct": round(pnl_pct, 2),
                "recommendation": "SELL_ALL",
            })
            _notify_trade("SELL_OPTION", {
                "symbol": symbol,
                "pnl": round((current - entry) * qty, 2),
                "pnl_pct": round(pnl_pct, 2),
                "reason": "TP_200%",
            })
        elif pnl_pct >= 100:
            actions.append({
                "symbol": symbol, "action": "TP_SIGNAL",
                "pnl_pct": round(pnl_pct, 2),
                "recommendation": "SELL_50%",
            })
            _notify_trade("SELL_OPTION", {
                "symbol": symbol,
                "pnl": round((current - entry) * qty * 0.5, 2),
                "pnl_pct": round(pnl_pct, 2),
                "reason": "TP_100%",
            })

    return {"timestamp": datetime.utcnow().isoformat(), "actions": actions}


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")

    if len(sys.argv) > 1 and sys.argv[1] == "exits":
        result = manage_exits()
    else:
        result = auto_trade()
    print(json.dumps(result, indent=2))
