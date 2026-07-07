"""Real-Time Price Watcher — asyncio-based sub-second price detection.

Catches 5-30 second moves on 0DTE options. Replaces cron-based polling with
true real-time monitoring using asyncio.gather() and tight event loops.

Architecture:
    PriceWatcher (asyncio)
    ├── Monitor SPY/QQQ equity (every 1s) — detect micro-trends
    ├── Monitor active option positions (every 1s) — manage exits
    ├── PriceChange event → TriggerDetector → AutoExecute
    └── Shutdown on market close (4:00 PM ET) or kill switch

Key design decisions:
    - asyncio (not threads): lower overhead, deterministic
    - 1-second polling for equities, 2-second for options
    - Caching: avoid repeated API calls for same symbol
    - Burst protection: don't fire 100 trades in 1 second
    - Graceful shutdown: cancel all tasks on signal

Usage:
    from hermes_trader.realtime_watcher import PriceWatcher

    async def main():
        watcher = PriceWatcher(account_number="924058324")
        await watcher.start()

    # In auto_trader.py:
    #   asyncio.run(watcher.start())  # blocks until market close
"""

import asyncio
import json
import logging
import signal
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Callable, Set
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.realtime_watcher")

ET = ZoneInfo("America/New_York")

# ── Configuration ──
EQUITY_POLL_INTERVAL = 5.0        # seconds — increased from 1s to avoid rate limits
OPTION_POLL_INTERVAL = 2.0        # seconds — how often to check option quotes
PRICE_CHANGE_THRESHOLD = 0.05     # % change to trigger event
MOMENTUM_WINDOW = 5               # seconds — lookback for momentum calculation
MOMENTUM_THRESHOLD = 0.10         # % change in window to fire momentum trigger
MAX_EVENTS_PER_MINUTE = 10        # rate limit
COOLDOWN_AFTER_TRADE = 30         # seconds — pause after any trade
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0


class TriggerType(Enum):
    """Types of price triggers that can fire."""
    THRESHOLD_BREAK = "threshold_break"       # price above/below level
    MOMENTUM_BURST = "momentum_burst"         # rapid % change in window
    SPREAD_NARROWING = "spread_narrowing"     # bid/ask spread tightens
    VOLUME_SPIKE = "volume_spike"             # sudden volume increase
    TIME_DECAY = "time_decay"                 # approaching market close


class PriceEvent:
    """A price change event that may trigger a trade."""
    __slots__ = ("symbol", "timestamp", "price", "prev_price", "change_pct",
                 "trigger_type", "metadata")

    def __init__(self, symbol, price, prev_price, trigger_type, metadata=None):
        self.symbol = symbol
        self.timestamp = datetime.now(ET)
        self.price = price
        self.prev_price = prev_price
        self.change_pct = ((price - prev_price) / prev_price * 100) if prev_price > 0 else 0
        self.trigger_type = trigger_type
        self.metadata = metadata or {}

    def __repr__(self):
        return f"<PriceEvent {self.symbol} {self.trigger_type.value} {self.change_pct:+.2f}% @ {self.price:.2f}>"


class PriceWatcher:
    """Asyncio-based real-time price watcher for 0DTE options.
    
    Continuously polls market data, detects micro-moves, and fires
    trigger events. Designed to catch 5-30 second price moves that
    cron jobs miss.
    """
    
    def __init__(self, account_number: str = "924058324", watch_symbols: List[str] = None):
        self.account_number = account_number
        try:
            from .config import config as _cfg
            self.watch_symbols = watch_symbols or list(_cfg.allowed_underlyings)[:10]  # cap at 10 for rate limits
        except Exception:
            self.watch_symbols = watch_symbols or ["SPY", "QQQ"]
        
        # Price state
        self.last_prices: Dict[str, float] = {}
        self.price_history: Dict[str, List[tuple]] = {}  # (timestamp, price)
        self.last_quote: Dict[str, dict] = {}  # full quote data
        
        # Event handling
        self.triggers: List[Callable] = []
        self.events: List[PriceEvent] = []
        self.events_this_minute: int = 0
        self.last_minute_reset: datetime = datetime.now(ET)
        self.last_trade_time: Optional[datetime] = None
        
        # State
        self.running = False
        self.tasks: Set[asyncio.Task] = set()
        self._stop_event = asyncio.Event()
        self._rate_limit_cooldown = 0  # seconds to wait after rate limit
        self._consecutive_failures = 0
    
    def add_trigger(self, callback: Callable[[PriceEvent], None]):
        """Register a callback to fire on price events.
        
        Args:
            callback: Async or sync function that takes a PriceEvent.
        """
        self.triggers.append(callback)
    
    def is_market_open(self) -> bool:
        """Check if market is currently open."""
        now = datetime.now(ET)
        # Weekday check (0=Mon, 6=Sun)
        if now.weekday() >= 5:
            return False
        # Hour check
        market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
        market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
        return market_open <= now <= market_close
    
    def in_cooldown(self) -> bool:
        """Check if we're in cooldown after a recent trade."""
        if self.last_trade_time is None:
            return False
        elapsed = (datetime.now(ET) - self.last_trade_time).total_seconds()
        return elapsed < COOLDOWN_AFTER_TRADE
    
    def rate_limit_exceeded(self) -> bool:
        """Check if we've exceeded max events per minute."""
        now = datetime.now(ET)
        if (now - self.last_minute_reset).total_seconds() >= 60:
            self.events_this_minute = 0
            self.last_minute_reset = now
        return self.events_this_minute >= MAX_EVENTS_PER_MINUTE
    
    async def _get_equity_quote(self, symbol: str) -> Optional[dict]:
        """Fetch equity quote via Robinhood MCP with rate limit backoff."""
        # If in cooldown, skip the call entirely
        if self._rate_limit_cooldown > 0:
            return None
        try:
            from .integrations.robinhood_broker import robinhood_mcp_call
            result = robinhood_mcp_call("get_equity_quotes", {"symbols": [symbol]}, retries=1)
            if result and "results" in result:
                q = result["results"][0] if result["results"] else {}
                if q and "last_trade_price" in q:
                    self._consecutive_failures = 0  # reset on success
                    return {
                        "price": float(q["last_trade_price"]),
                        "bid": float(q.get("bid_price", 0) or 0),
                        "ask": float(q.get("ask_price", 0) or 0),
                        "timestamp": datetime.now(ET),
                    }
            # Check for rate limit in response
            if result and "RATE_LIMITED" in str(result):
                self._consecutive_failures += 1
                self._rate_limit_cooldown = min(120, max(30, 2 ** self._consecutive_failures))
                logger.warning(f"Rate limited, backing off {self._rate_limit_cooldown}s (fail #{self._consecutive_failures})")
        except Exception as e:
            self._consecutive_failures += 1
            if "rate" in str(e).lower() or "429" in str(e):
                self._rate_limit_cooldown = min(120, max(30, 2 ** self._consecutive_failures))
                logger.warning(f"Rate limited (exception), backing off {self._rate_limit_cooldown}s")
            else:
                logger.debug(f"Quote fetch error for {symbol}: {e}")
        return None
    
    async def _get_option_quote(self, option_id: str) -> Optional[dict]:
        """Fetch option quote via Robinhood MCP."""
        try:
            from .integrations.robinhood_broker import robinhood_mcp_call
            result = robinhood_mcp_call("get_option_quotes", {"instrument_ids": [option_id]})
            if result and "results" in result:
                q = result["results"][0] if result["results"] else {}
                if q and "mark_price" in q:
                    return {
                        "price": float(q["mark_price"]),
                        "bid": float(q.get("bid_price", 0) or 0),
                        "ask": float(q.get("ask_price", 0) or 0),
                        "volume": int(q.get("volume", 0) or 0),
                        "timestamp": datetime.now(ET),
                    }
        except Exception as e:
            logger.debug(f"Option quote error for {option_id}: {e}")
        return None
    
    def _detect_momentum(self, symbol: str, current_price: float) -> Optional[PriceEvent]:
        """Detect rapid price change in MOMENTUM_WINDOW seconds."""
        if symbol not in self.price_history:
            return None
        history = self.price_history[symbol]
        now = datetime.now(ET)
        window_start = now - timedelta(seconds=MOMENTUM_WINDOW)
        recent = [p for t, p in history if t >= window_start]
        if len(recent) < 2:
            return None
        baseline = recent[0]
        if baseline <= 0:
            return None
        change_pct = (current_price - baseline) / baseline * 100
        if abs(change_pct) >= MOMENTUM_THRESHOLD:
            return PriceEvent(symbol, current_price, baseline, TriggerType.MOMENTUM_BURST, {
                "window_seconds": MOMENTUM_WINDOW,
                "magnitude": change_pct,
            })
        return None
    
    def _detect_threshold_break(self, symbol: str, current_price: float) -> Optional[PriceEvent]:
        """Detect price crossing a significant threshold."""
        if symbol not in self.last_prices:
            return None
        prev = self.last_prices[symbol]
        if prev <= 0:
            return None
        change_pct = abs((current_price - prev) / prev * 100)
        if change_pct >= PRICE_CHANGE_THRESHOLD:
            return PriceEvent(symbol, current_price, prev, TriggerType.THRESHOLD_BREAK, {
                "magnitude": change_pct,
            })
        return None
    
    def _detect_spread_narrowing(self, symbol: str, quote: dict) -> Optional[PriceEvent]:
        """Detect bid/ask spread tightening (often precedes moves)."""
        if symbol not in self.last_quote:
            return None
        prev = self.last_quote[symbol]
        if not all(k in prev for k in ("bid", "ask", "price")):
            return None
        if not all(k in quote for k in ("bid", "ask", "price")):
            return None
        prev_spread = (prev["ask"] - prev["bid"]) / prev["price"] * 100 if prev["price"] > 0 else 100
        curr_spread = (quote["ask"] - quote["bid"]) / quote["price"] * 100 if quote["price"] > 0 else 100
        # Spread tightened by >30% (good signal — market makers committing)
        if prev_spread > 0 and curr_spread < prev_spread * 0.70:
            return PriceEvent(symbol, quote["price"], prev["price"], TriggerType.SPREAD_NARROWING, {
                "prev_spread_pct": prev_spread,
                "curr_spread_pct": curr_spread,
            })
        return None
    
    def _record_price(self, symbol: str, price: float):
        """Record price in history (for momentum calc)."""
        now = datetime.now(ET)
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        self.price_history[symbol].append((now, price))
        # Keep only last 5 minutes of data
        cutoff = now - timedelta(minutes=5)
        self.price_history[symbol] = [(t, p) for t, p in self.price_history[symbol] if t >= cutoff]
    
    async def _fire_triggers(self, event: PriceEvent):
        """Fire all registered triggers for an event."""
        if self.rate_limit_exceeded():
            logger.debug(f"Rate limit exceeded, skipping {event}")
            return
        self.events.append(event)
        self.events_this_minute += 1
        logger.info(f"⚡ {event}")
        for trigger in self.triggers:
            try:
                result = trigger(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Trigger error: {e}")
    

    async def _batch_poll_all(self):
        """Batch-poll all watch symbols in ONE MCP call. Replaces per-symbol tasks."""
        from .integrations.robinhood_broker import robinhood_mcp_call
        while not self._stop_event.is_set():
            try:
                if self._rate_limit_cooldown > 0:
                    logger.info(f"Rate limit cooldown: sleeping {self._rate_limit_cooldown}s")
                    await asyncio.sleep(self._rate_limit_cooldown)
                    self._rate_limit_cooldown = 0
                    continue
                
                # ONE call for all symbols
                result = robinhood_mcp_call("get_equity_quotes", {"symbols": self.watch_symbols}, retries=1)
                
                if result and "RATE_LIMITED" in str(result):
                    self._consecutive_failures += 1
                    self._rate_limit_cooldown = min(120, max(30, 2 ** self._consecutive_failures))
                    logger.warning(f"Rate limited on batch poll, backing off {self._rate_limit_cooldown}s")
                    continue
                
                if result and "results" in result:
                    self._consecutive_failures = 0
                    for q in result["results"] or []:
                        sym = q.get("symbol", "")
                        if sym and "last_trade_price" in q:
                            price = float(q["last_trade_price"])
                            quote = {
                                "price": price,
                                "bid": float(q.get("bid_price", 0) or 0),
                                "ask": float(q.get("ask_price", 0) or 0),
                                "timestamp": datetime.now(ET),
                            }
                            self._record_price(sym, price)
                            self.last_quote[sym] = quote
                            
                            # Detect events
                            event = self._detect_momentum(sym, price)
                            event = event or self._detect_threshold_break(sym, price)
                            if event is None:
                                event = self._detect_spread_narrowing(sym, quote)
                            if event:
                                await self._fire_triggers(event)
                            
                            self.last_prices[sym] = price
            except Exception as e:
                if "rate" in str(e).lower() or "429" in str(e):
                    self._consecutive_failures += 1
                    self._rate_limit_cooldown = min(120, max(30, 2 ** self._consecutive_failures))
                    logger.warning(f"Rate limited (exception), backing off {self._rate_limit_cooldown}s")
                else:
                    logger.debug(f"Batch poll error: {e}")
            
            await asyncio.sleep(EQUITY_POLL_INTERVAL)

    async def _monitor_equity(self, symbol: str):
        """Continuously monitor an equity symbol."""
        while not self._stop_event.is_set():
            try:
                quote = await self._get_equity_quote(symbol)
                if quote and quote["price"] > 0:
                    # Record price
                    self._record_price(symbol, quote["price"])
                    self.last_quote[symbol] = quote
                    
                    # Detect events
                    event = None
                    # Momentum (fastest, most important)
                    event = self._detect_momentum(symbol, quote["price"]) or event
                    # Threshold break
                    event = event or self._detect_threshold_break(symbol, quote["price"])
                    # Spread narrowing
                    if event is None:
                        event = self._detect_spread_narrowing(symbol, quote)
                    
                    if event:
                        await self._fire_triggers(event)
                    
                    # Update last price
                    self.last_prices[symbol] = quote["price"]
            except Exception as e:
                logger.debug(f"Monitor error for {symbol}: {e}")
            
            # Use longer sleep when rate limited
            if self._rate_limit_cooldown > 0:
                logger.info(f"Rate limit cooldown: sleeping {self._rate_limit_cooldown}s")
                await asyncio.sleep(self._rate_limit_cooldown)
                self._rate_limit_cooldown = 0  # reset after cooldown
            else:
                await asyncio.sleep(EQUITY_POLL_INTERVAL)
    
    async def _monitor_position(self, option_id: str, position_data: dict):
        """Continuously monitor an option position for exit triggers."""
        entry_price = position_data.get("entry_price", 0)
        stop_loss = position_data.get("stop_loss", entry_price * 0.50)
        take_profit = position_data.get("take_profit", entry_price * 2.0)
        quantity = position_data.get("quantity", 1)
        
        while not self._stop_event.is_set():
            try:
                quote = await self._get_option_quote(option_id)
                if quote and quote["price"] > 0:
                    self.last_quote[option_id] = quote
                    price = quote["price"]
                    
                    # Stop loss
                    if price <= stop_loss:
                        logger.warning(f"🛑 STOP LOSS triggered: {option_id} @ {price} (stop={stop_loss})")
                        await self._execute_exit(option_id, quantity, "stop_loss", price)
                        break
                    
                    # Take profit
                    if price >= take_profit:
                        logger.info(f"🎯 TAKE PROFIT triggered: {option_id} @ {price} (target={take_profit})")
                        await self._execute_exit(option_id, quantity, "take_profit", price)
                        break
                    
                    # Check time stop
                    if datetime.now(ET).hour >= 15 and datetime.now(ET).minute >= 45:
                        logger.info(f"⏰ TIME STOP triggered: {option_id} @ {price}")
                        await self._execute_exit(option_id, quantity, "time_stop", price)
                        break
                    
                    # Update trailing stop if in profit
                    pnl_pct = (price - entry_price) / entry_price if entry_price > 0 else 0
                    if pnl_pct >= 0.30:  # 30% profit — activate trailing stop
                        new_stop = price * 0.80
                        if new_stop > stop_loss:
                            stop_loss = new_stop
                            logger.debug(f"Trailing stop raised: {option_id} → {new_stop:.2f}")
            except Exception as e:
                logger.debug(f"Position monitor error for {option_id}: {e}")
            
            await asyncio.sleep(OPTION_POLL_INTERVAL)
    
    async def _execute_exit(self, option_id: str, quantity: int, reason: str, price: float):
        """Execute an exit order."""
        try:
            from .integrations.robinhood_broker import robinhood_mcp_call
            result = robinhood_mcp_call("place_option_order", {
                "account_number": self.account_number,
                "legs": [{
                    "option_id": option_id,
                    "side": "sell",
                    "position_effect": "close",
                }],
                "quantity": quantity,
                "type": "limit",
                "price": f"{price:.4f}",
            })
            logger.info(f"✅ Exit executed: {option_id} x{quantity} @ {price} reason={reason}")
            self.last_trade_time = datetime.now(ET)
            
            # Log exit
            with open("/opt/hermes-trader/data/journals/exit_log.jsonl", "a") as f:
                f.write(json.dumps({
                    "timestamp": datetime.now(ET).isoformat(),
                    "option_id": option_id,
                    "quantity": quantity,
                    "price": price,
                    "reason": reason,
                    "order_id": result.get("id", result.get("order_id", "")) if isinstance(result, dict) else "",
                }) + "\n")
        except Exception as e:
            logger.error(f"Exit execution FAILED: {option_id} — {e}")
    
    def start_position_monitor(self, option_id: str, position_data: dict):
        """Start monitoring a position for exits. Returns the task."""
        task = asyncio.create_task(self._monitor_position(option_id, position_data))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task
    
    async def start(self):
        """Start the price watcher. Blocks until market close or stop signal."""
        if not self.is_market_open():
            logger.info("Market closed — watcher not started")
            return
        
        self.running = True
        logger.info(f"🚀 PriceWatcher started: monitoring {self.watch_symbols}")
        
        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop_event.set)
            except NotImplementedError:
                # Windows / non-Unix
                pass
        
        # Start batch equity monitor (ONE call for all symbols)
        task = asyncio.create_task(self._batch_poll_all())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        
        # Wait for stop signal
        await self._stop_event.wait()
        
        # Cancel all tasks
        logger.info("Stopping PriceWatcher...")
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.running = False
        logger.info("PriceWatcher stopped")
    
    def stop(self):
        """Signal the watcher to stop."""
        self._stop_event.set()
    
    def get_state(self) -> dict:
        """Get current state for debugging/monitoring."""
        return {
            "running": self.running,
            "watch_symbols": self.watch_symbols,
            "last_prices": self.last_prices,
            "events_count": len(self.events),
            "events_this_minute": self.events_this_minute,
            "active_tasks": len(self.tasks),
            "market_open": self.is_market_open(),
            "in_cooldown": self.in_cooldown(),
        }
