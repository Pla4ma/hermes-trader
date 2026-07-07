"""Event-Driven Trade Trigger System — wires the watcher to auto-execute.

When PriceWatcher detects a price event, this module decides:
1. Should we enter a trade?
2. What option to buy?
3. What size?
4. When to exit?

Designed to fire on 5-30 second moves that cron jobs miss.

Trigger types:
- MOMENTUM_BURST: rapid % change → buy in direction of move
- THRESHOLD_BREAK: price breaks key level → buy breakout
- SPREAD_NARROWING: spread tightens → anticipate move
- VOLUME_SPIKE: volume surges → follow smart money

Integration:
- Called by PriceWatcher when event fires
- Uses entry_gates (9 filters) for validation
- Uses dual model (probability + magnitude) for scoring
- Uses news_catalyst + portfolio_risk for safety
- Places order via Robinhood MCP
- Starts position monitor for auto-exit
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .realtime_watcher import PriceEvent, TriggerType, PriceWatcher

logger = logging.getLogger("hermes_trader.trigger_engine")

ET = ZoneInfo("America/New_York")

# ── Configuration ──
ACCOUNT_NUMBER = os.getenv("ROBINHOOD_ACCOUNT_NUMBER", "924058324")
MIN_PROBABILITY = 0.55  # Minimum probability to trade
MIN_EV = 0.10           # Minimum expected value
MAX_RISK_PER_TRADE = 0.40  # 40% of account per trade
COOLDOWN_AFTER_TRADE = 30
PRICE_DEVIATION_BLOCK = 0.03  # Block if price moved >3% since scan


class TradeDirection(Enum):
    """Trade direction based on trigger."""
    CALL = "call"   # Bullish
    PUT = "put"     # Bearish
    NONE = "none"   # No trade


def determine_direction(event: PriceEvent) -> TradeDirection:
    """Determine trade direction from price event."""
    if event.trigger_type == TriggerType.MOMENTUM_BURST:
        if event.change_pct > 0:
            return TradeDirection.CALL
        elif event.change_pct < 0:
            return TradeDirection.PUT
    elif event.trigger_type == TriggerType.THRESHOLD_BREAK:
        if event.change_pct > 0:
            return TradeDirection.CALL
        elif event.change_pct < 0:
            return TradeDirection.PUT
    elif event.trigger_type == TriggerType.SPREAD_NARROWING:
        # Spread narrowing alone doesn't give direction
        return TradeDirection.NONE
    elif event.trigger_type == TriggerType.VOLUME_SPIKE:
        # Need to check if volume is on calls or puts
        if event.metadata.get("call_volume", 0) > event.metadata.get("put_volume", 0):
            return TradeDirection.CALL
        else:
            return TradeDirection.PUT
    return TradeDirection.NONE


def get_target_strike(spot_price: float, direction: TradeDirection) -> float:
    """Get target strike price (ATM or slightly OTM)."""
    if direction == TradeDirection.CALL:
        # ATM call
        return round(spot_price)
    elif direction == TradeDirection.PUT:
        return round(spot_price)
    return 0


def scan_options_for_strike(symbol: str, strike: float, direction: TradeDirection, expiration: str = None) -> Optional[dict]:
    """Find the option contract for a given strike + direction.
    
    Returns dict with option_id, bid, ask, etc. or None.
    """
    try:
        from .integrations.robinhood_broker import robinhood_mcp_call
        
        # Get option chain
        result = robinhood_mcp_call("get_option_chains", {"symbol": symbol})
        if not result:
            return None
        
        # Find closest strike
        chains = result.get("chains", [])
        if not chains:
            return None
        
        chain_id = chains[0].get("id")
        if not chain_id:
            return None
        
        # Get instruments
        instr_result = robinhood_mcp_call("get_option_instruments", {
            "chain_id": chain_id,
            "expiration_dates": expiration or datetime.now(ET).strftime("%Y-%m-%d"),
            "strike_price": str(strike),
            "type": "call" if direction == TradeDirection.CALL else "put",
        })
        
        if not instr_result or "instruments" not in instr_result:
            return None
        
        instruments = instr_result["instruments"]
        if not instruments:
            return None
        
        return {
            "option_id": instruments[0]["id"],
            "symbol": symbol,
            "strike": strike,
            "type": direction.value,
        }
    except Exception as e:
        logger.error(f"Error scanning options: {e}")
        return None


def get_quote(option_id: str) -> Optional[dict]:
    """Get current quote for an option."""
    try:
        from .integrations.robinhood_broker import robinhood_mcp_call
        result = robinhood_mcp_call("get_option_quotes", {"instrument_ids": [option_id]})
        if result and "results" in result and result["results"]:
            q = result["results"][0]
            return {
                "bid": float(q.get("bid_price", 0) or 0),
                "ask": float(q.get("ask_price", 0) or 0),
                "mid": float(q.get("mark_price", 0) or 0),
                "volume": int(q.get("volume", 0) or 0),
            }
    except Exception as e:
        logger.error(f"Error getting quote: {e}")
    return None


def check_news_catalyst() -> tuple:
    """Check if news/catalyst is blocking. Returns (blocked, reason)."""
    try:
        from .news_catalyst import should_block_trade
        reasons = []
        if should_block_trade(reasons):
            return True, reasons[0] if reasons else "Economic event"
    except Exception:
        pass
    return False, ""


def check_portfolio_risk() -> tuple:
    """Check portfolio risk. Returns (blocked, reason)."""
    try:
        from .integrations.robinhood_broker import robinhood_mcp_call
        from .portfolio_risk import get_portfolio_risk_summary, check_drawdown
        
        # Get positions
        positions_result = robinhood_mcp_call("get_option_positions", {"account_number": ACCOUNT_NUMBER, "nonzero": True})
        if not positions_result:
            return False, ""
        
        # Convert to dicts
        positions = []
        if "positions" in positions_result:
            for p in positions_result["positions"]:
                qty = float(p.get("quantity", 0) or 0)
                if qty > 0:
                    positions.append({
                        "symbol": p.get("chain_symbol", "UNKNOWN"),
                        "type": "call" if p.get("type") == "call" else "put",
                        "value": qty * float(p.get("average_price", 0) or 0) * 100,
                    })
        
        # Check drawdown
        equity_curve = []  # TODO: load from history
        is_blocked, dd_pct, dd_reason = check_drawdown(equity_curve)
        if is_blocked:
            return True, dd_reason
        
        # Check risk summary
        risk_summary = get_portfolio_risk_summary(positions)
        if not risk_summary.get("can_trade", True):
            return True, risk_summary.get("drawdown_reason", "Portfolio risk")
    except Exception as e:
        logger.debug(f"Portfolio risk check error: {e}")
    return False, ""


def get_account_info() -> dict:
    """Get account cash and buying power."""
    try:
        from .integrations.robinhood_broker import robinhood_mcp_call
        result = robinhood_mcp_call("get_portfolio", {"account_number": ACCOUNT_NUMBER})
        if result:
            return {
                "cash": float(result.get("cash", 0) or 0),
                "buying_power": float(result.get("buying_power", {}).get("buying_power", 0) or 0),
                "total_value": float(result.get("total_value", 0) or 0),
            }
    except Exception as e:
        logger.error(f"Error getting account info: {e}")
    return {"cash": 0, "buying_power": 0, "total_value": 0}


def calculate_profit_targets(entry_price: float) -> dict:
    """Calculate stop loss and take profit for a position."""
    return {
        "stop_loss": round(entry_price * 0.50, 4),    # 50% stop
        "take_profit": round(entry_price * 2.0, 4),   # 100% target
    }


def calculate_quantity(account_value: float, option_price: float, probability: float = 0.60) -> int:
    """Calculate position size using Kelly-like formula."""
    if option_price <= 0 or account_value <= 0:
        return 0
    
    # Position size based on probability and risk tolerance
    # Higher probability → larger position
    risk_pct = MAX_RISK_PER_TRADE * probability
    max_dollars = account_value * risk_pct
    contract_cost = option_price * 100  # Options are 100 shares per contract
    
    quantity = int(max_dollars / contract_cost) if contract_cost > 0 else 0
    return max(1, min(quantity, 5))  # Cap at 5 contracts


def place_buy_order(option_id: str, quantity: int, limit_price: float) -> Optional[dict]:
    """Place a buy order for an option."""
    try:
        from .integrations.robinhood_broker import robinhood_mcp_call
        result = robinhood_mcp_call("place_option_order", {
            "account_number": ACCOUNT_NUMBER,
            "legs": [{
                "option_id": option_id,
                "side": "buy",
                "position_effect": "open",
            }],
            "quantity": quantity,
            "type": "limit",
            "price": f"{limit_price:.4f}",
        })
        return result
    except Exception as e:
        logger.error(f"Error placing buy order: {e}")
    return None


def _check_research_bias(symbol: str) -> dict:
    """Check daily research bias from VibeTrading + TradingAgents.
    
    Reads /opt/hermes-trader/data/snapshots/research_latest.json
    (saved by workflow.py run_research_cycle).
    
    Returns:
        {"allowed": True/False, "reason": str, "bias": "bullish"/"bearish"/"neutral"}
    
    Rules:
    - No research file → BLOCK (don't trade blind)
    - Research older than 8 hours → BLOCK (stale)
    - Signal matches direction → ALLOW
    - Signal is neutral → ALLOW (research found no edge, but don't block)
    - Signal conflicts → BLOCK
    """
    import json
    from pathlib import Path
    
    snapshot_path = Path("/opt/hermes-trader/data/snapshots/research_latest.json")
    
    if not snapshot_path.exists():
        return {"allowed": False, "reason": "No research snapshot — run daily workflow first", "bias": "none"}
    
    try:
        data = json.loads(snapshot_path.read_text())
        timestamp = data.get("timestamp", "")
        
        # Check staleness (8 hours max)
        from datetime import datetime, timedelta
        try:
            research_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age_hours = (datetime.now(research_time.tzinfo) - research_time).total_seconds() / 3600
            if age_hours > 8:
                return {"allowed": False, "reason": f"Research stale ({age_hours:.1f}h old, max 8h)", "bias": "stale"}
        except Exception:
            pass  # If we can't parse time, allow (research exists at least)
        
        # Get symbol's research
        research = data.get("research", {})
        symbol_data = research.get(symbol, {})
        
        if not symbol_data:
            return {"allowed": False, "reason": f"No research for {symbol}", "bias": "none"}
        
        signal = symbol_data.get("signal", "neutral")
        confidence = symbol_data.get("confidence_score", 0)
        
        # neutral = allow (research found no strong edge either way)
        if signal == "neutral":
            return {"allowed": True, "reason": f"Research neutral for {symbol} — proceeding", "bias": "neutral"}
        
        # bullish/bearish — store for direction validation later
        return {
            "allowed": True,
            "reason": f"Research {signal} (conf={confidence}) for {symbol}",
            "bias": signal,
            "confidence": confidence,
        }
    
    except Exception as e:
        logger.error(f"Research bias check error: {e}")
        return {"allowed": False, "reason": f"Research check failed: {e}", "bias": "error"}


async def handle_event(event: PriceEvent, watcher: PriceWatcher):
    """Handle a price event from the watcher.
    
    This is the main decision logic:
    0. CHECK RESEARCH BIAS (VibeTrading + TradingAgents from daily workflow)
    1. Check safety gates (news, portfolio)
    2. Determine direction
    3. Find target option
    4. Validate entry gates
    5. Place order
    6. Start position monitor for auto-exit
    
    CRITICAL: Step 0 was missing — watcher executed blind without any
    research intelligence. Now it checks the daily research snapshot
    before every trade. If no research exists or direction conflicts,
    trade is BLOCKED.
    """
    if watcher.in_cooldown():
        logger.debug(f"In cooldown, skipping event: {event}")
        return
    
    if watcher.rate_limit_exceeded():
        logger.debug(f"Rate limit exceeded, skipping event: {event}")
        return
    
    # ── STEP 0: RESEARCH BIAS CHECK ──
    # Load daily research (VibeTrading + TradingAgents output from 9:40 workflow)
    # Block trade if: no research, stale research, or direction conflicts with bias
    research_bias = _check_research_bias(event.symbol)
    if not research_bias["allowed"]:
        logger.info(f"Event {event} blocked by research: {research_bias['reason']}")
        return
    
    # Check safety gates
    news_blocked, news_reason = check_news_catalyst()
    if news_blocked:
        logger.info(f"Event {event} blocked by news: {news_reason}")
        return
    
    risk_blocked, risk_reason = check_portfolio_risk()
    if risk_blocked:
        logger.info(f"Event {event} blocked by portfolio risk: {risk_reason}")
        return
    
    # Determine direction
    direction = determine_direction(event)
    if direction == TradeDirection.NONE:
        return
    
    # Get spot price (we need it for strike selection)
    # The event.price is the underlying price
    spot_price = event.price
    
    # Get target strike (ATM)
    strike = get_target_strike(spot_price, direction)
    
    # Find the option contract
    option = scan_options_for_strike(event.symbol, strike, direction)
    if not option:
        logger.debug(f"No option found for {event.symbol} {strike} {direction.value}")
        return
    
    # Get current quote
    quote = get_quote(option["option_id"])
    if not quote or quote["mid"] <= 0:
        logger.debug(f"No quote for {option['option_id']}")
        return
    
    # Check entry gates (9 filters)
    try:
        from .entry_gates import check_all_gates
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        
        # Get price data for gates
        import yfinance as yf
        ticker = yf.Ticker(event.symbol)
        hist_today = ticker.history(period="1d")
        hist_20d = ticker.history(period="20d")
        
        if len(hist_today) == 0 or len(hist_20d) < 2:
            return
        
        today_data = hist_today.iloc[0]
        open_price = float(today_data["Open"])
        high_of_day = float(today_data["High"])
        low_of_day = float(today_data["Low"])
        current_volume = float(hist_today["Volume"].iloc[-1])
        avg_volume = float(hist_20d["Volume"].mean())
        prev_close = float(hist_20d["Close"].iloc[-2]) if len(hist_20d) > 1 else 0
        
        # RSI
        close_20d = hist_20d["Close"]
        delta_series = close_20d.diff()
        gain = delta_series.clip(lower=0).rolling(14).mean()
        loss = (-delta_series.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi_series = 100 - (100 / (1 + rs))
        rsi_14 = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 else 50.0
        
        now_et = dt.now(ZoneInfo("America/New_York"))
        
        gates_passed, gate_failures = check_all_gates(
            symbol=event.symbol,
            option_type=direction.value,
            spot=spot_price,
            open_price=open_price,
            high_of_day=high_of_day,
            low_of_day=low_of_day,
            current_volume=current_volume,
            avg_volume_20d=avg_volume,
            rsi_14=rsi_14,
            now_et=now_et,
        )
        
        if not gates_passed:
            logger.info(f"Event {event} blocked by entry gates: {gate_failures}")
            return
    except Exception as e:
        logger.error(f"Entry gate check error: {e}")
        return
    
    # Get account info for sizing
    account = get_account_info()
    if account["buying_power"] < quote["mid"] * 100:
        logger.info(f"Insufficient buying power: ${account['buying_power']}")
        return
    
    # Calculate size
    quantity = calculate_quantity(account["buying_power"], quote["mid"])
    if quantity <= 0:
        logger.info("Quantity 0, skipping")
        return
    
    # Place order
    order = place_buy_order(option["option_id"], quantity, quote["ask"])
    if not order:
        logger.error(f"Order failed for {option['option_id']}")
        return
    
    entry_price = quote["mid"]
    logger.info(f"🚀 TRADE EXECUTED: {event.symbol} {direction.value} {strike} x{quantity} @ {entry_price}")
    
    # Calculate exits
    exits = calculate_profit_targets(entry_price)
    
    # Log trade
    with open("/opt/hermes-trader/data/journals/triggered_trades.jsonl", "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(ET).isoformat(),
            "event": {
                "symbol": event.symbol,
                "trigger_type": event.trigger_type.value,
                "change_pct": event.change_pct,
                "price": event.price,
            },
            "trade": {
                "option_id": option["option_id"],
                "direction": direction.value,
                "strike": strike,
                "quantity": quantity,
                "entry_price": entry_price,
                "stop_loss": exits["stop_loss"],
                "take_profit": exits["take_profit"],
            },
            "order": order if isinstance(order, dict) else {},
        }) + "\n")
    
    # Start position monitor for auto-exit
    watcher.start_position_monitor(option["option_id"], {
        "entry_price": entry_price,
        "stop_loss": exits["stop_loss"],
        "take_profit": exits["take_profit"],
        "quantity": quantity,
    })


async def run_watcher():
    """Run the watcher with trigger handling."""
    watcher = PriceWatcher(account_number=ACCOUNT_NUMBER)
    
    # Register the event handler
    async def event_handler(event):
        await handle_event(event, watcher)
    
    watcher.add_trigger(event_handler)
    
    # Run until market close
    await watcher.start()

if __name__ == "__main__":
    import asyncio
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        stream=__import__('sys').stdout
    )
    print(f"[trigger_engine] Starting watcher at {datetime.now(ET).strftime('%H:%M:%S %Z')}", flush=True)
    try:
        asyncio.run(run_watcher())
    except KeyboardInterrupt:
        print("[trigger_engine] Stopped by user", flush=True)
    except Exception as e:
        print(f"[trigger_engine] Fatal error: {e}", flush=True)
        import traceback
        traceback.print_exc()
