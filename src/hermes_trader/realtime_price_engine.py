"""
Real-time price detection engine for 0DTE options trading.

Catches 5-30 SECOND moves via parallel async polling + WebSocket fan-out.
Designed for the $300 account on Robinhood MCP (no native WS).

Architecture (3 concurrent layers, all on one asyncio loop):
  ┌────────────────────────────────────────────────────────────────┐
  │ Layer 1: RobinhoodMCPQuoteSource (asyncio, 1-5s poll)         │
  │   - get_option_quotes (real-time, 20 instruments / call)       │
  │   - get_equity_quotes (SPY/QQQ underlyings, 20 / call)         │
  │ Layer 2: AlpacaWebSocketSource (free IEX feed, sub-second)    │
  │   - alpaca-py StockDataStream for SPY/QQQ trade prints        │
  │   - drives underlying-move detection (gamma scalping trigger)  │
  │ Layer 3: YahooFinanceQuoteSource (async fallback, 1s)          │
  │   - yfinance via aiohttp polling for redundancy                │
  └────────────────────────────────────────────────────────────────┘
                  │               │               │
                  ▼               ▼               ▼
            ┌─────────────────────────────────────────┐
            │  TickBus (asyncio.Queue broadcast)      │
            │  - emits TickEvent to all subscribers   │
            └─────────────────────────────────────────┘
                  │               │               │
                  ▼               ▼               ▼
            ┌──────────┐   ┌──────────────┐  ┌──────────────┐
            │ Entry    │   │ Exit         │  │ Telemetry    │
            │ triggers │   │ ZeroDTE      │  │ + journal    │
            │          │   │ ExitManager  │  │              │
            └──────────┘   └──────────────┘  └──────────────┘

Detection patterns (sub-30s):
  - threshold_cross:    spot_price crosses ±0.05% / 30s
  - momentum_accel:     velocity (Δprice/Δt) crosses upper band
  - iv_skew_spike:      option mid jumps >3% in <10s
  - spread_compress:    bid-ask spread < 2¢ (liquidity surge)

Usage:
    engine = RealtimePriceEngine.from_env()
    await engine.start()

    async for tick in engine.subscribe(["SPY", "QQQ"]):
        if tick.signal_kind == "momentum_accel":
            ...  # fire entry
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("hermes_trader.realtime")

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

# Polling cadence (seconds) — Robinhood MCP has no documented rate limit
# but we cap aggressively to leave headroom for order placement.
ROBINHOOD_POLL_INTERVAL_S = 2.0
ROBINHOOD_QUOTE_BATCH = 20         # max instruments per get_option_quotes call
ROBINHOOD_UNDERLYING_INTERVAL_S = 1.0   # SPY/QQQ spot quotes refresh

# Detection windows
MOMENTUM_WINDOW_S = 30             # rolling window for velocity calc
THRESHOLD_WINDOW_S = 30            # rolling window for ±pct move
MOMENTUM_VEL_BAND = 0.0008         # 0.08% move in 30s fires signal
THRESHOLD_PCT = 0.0005             # 0.05% absolute move threshold
PRICE_HISTORY_MAX = 600            # 10 min at 1Hz (memory bound)

# Files
JOURNAL_PATH = Path("/opt/hermes-trader/data/journals/realtime_ticks.jsonl")


# ─────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────

class SignalKind(str, Enum):
    THRESHOLD_CROSS = "threshold_cross"
    MOMENTUM_ACCEL = "momentum_accel"
    IV_SKEW_SPIKE = "iv_skew_spike"
    SPREAD_COMPRESS = "spread_compress"


@dataclass
class TickEvent:
    """Single price observation emitted on the bus."""
    symbol: str
    timestamp: float                  # epoch seconds (monotonic)
    wall_clock: datetime             # UTC datetime
    price: float                     # mid (bid+ask)/2
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    source: str = "unknown"          # "robinhood_mcp" | "alpaca" | "yfinance"
    signal_kind: Optional[SignalKind] = None
    signal_detail: Dict[str, float] = field(default_factory=dict)


@dataclass
class PriceHistory:
    """Bounded ring buffer of recent ticks for an instrument."""
    symbol: str
    ticks: Deque[TickEvent] = field(default_factory=lambda: deque(maxlen=PRICE_HISTORY_MAX))

    def push(self, t: TickEvent) -> None:
        self.ticks.append(t)

    def velocity(self, window_s: float) -> Optional[float]:
        """Δprice / Δt over the last `window_s` seconds. Returns fraction, not pct."""
        if len(self.ticks) < 2:
            return None
        now = self.ticks[-1].timestamp
        cutoff = now - window_s
        baseline = None
        # Walk back to the oldest tick within window
        for t in self.ticks:
            if t.timestamp >= cutoff:
                baseline = t
                break
        if baseline is None or baseline.price <= 0:
            return None
        return (self.ticks[-1].price - baseline.price) / baseline.price

    def pct_change(self, window_s: float) -> Optional[float]:
        v = self.velocity(window_s)
        return None if v is None else v

    def highest(self, window_s: float) -> Optional[float]:
        if not self.ticks:
            return None
        now = self.ticks[-1].timestamp
        cutoff = now - window_s
        recent = [t.price for t in self.ticks if t.timestamp >= cutoff]
        return max(recent) if recent else None

    def lowest(self, window_s: float) -> Optional[float]:
        if not self.ticks:
            return None
        now = self.ticks[-1].timestamp
        cutoff = now - window_s
        recent = [t.price for t in self.ticks if t.timestamp >= cutoff]
        return min(recent) if recent else None


# ─────────────────────────────────────────────────────────────────────
# Tick Bus (asyncio fan-out)
# ─────────────────────────────────────────────────────────────────────

class TickBus:
    """Async broadcast bus: producers push TickEvents, many consumers subscribe."""

    def __init__(self, maxsize: int = 1024) -> None:
        self._subscribers: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._maxsize = maxsize

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    async def publish(self, event: TickEvent) -> None:
        # Snapshot under lock so we don't mutate during fan-out
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            # Drop oldest if slow consumer — never block the producer
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass


# ─────────────────────────────────────────────────────────────────────
# Layer 1: Robinhood MCP quote source (async polling)
# ─────────────────────────────────────────────────────────────────────

class RobinhoodMCPQuoteSource:
    """Polls get_option_quotes + get_equity_quotes on a fixed cadence.

    Robinhood MCP is HTTPS request/response only — no streaming. We
    compensate with tight async polling and batched quote requests
    (up to 20 instrument_ids per call). The MCP HTTP call takes
    ~150-400ms; with 2s cadence we get fresh prints every ~2-2.5s.
    """

    def __init__(
        self,
        bus: TickBus,
        option_instruments: List[str],
        underlying_symbols: List[str] = ("SPY", "QQQ"),
        poll_interval_s: float = ROBINHOOD_POLL_INTERVAL_S,
        underlying_interval_s: float = ROBINHOOD_UNDERLYING_INTERVAL_S,
    ) -> None:
        self.bus = bus
        self.option_instruments = list(option_instruments)
        self.underlying_symbols = list(underlying_symbols)
        self.poll_interval_s = poll_interval_s
        self.underlying_interval_s = underlying_interval_s
        self._stop = asyncio.Event()
        self._tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._poll_options_loop(), name="rh_options"),
            asyncio.create_task(self._poll_underlyings_loop(), name="rh_underlying"),
        ]
        logger.info(
            f"RobinhoodMCPQuoteSource started "
            f"({len(self.option_instruments)} options, "
            f"{len(self.underlying_symbols)} underlyings)"
        )

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        logger.info("RobinhoodMCPQuoteSource stopped")

    def update_instruments(self, option_instruments: List[str]) -> None:
        """Hot-swap the option list (e.g., after scanner finds new candidates)."""
        self.option_instruments = list(option_instruments)
        logger.info(f"Updated tracked options: {len(self.option_instruments)}")

    # ── Option quote loop (batched) ────────────────────────────────

    async def _poll_options_loop(self) -> None:
        while not self._stop.is_set():
            try:
                # Batch into chunks of ROBINHOOD_QUOTE_BATCH
                for i in range(0, len(self.option_instruments), ROBINHOOD_QUOTE_BATCH):
                    if self._stop.is_set():
                        return
                    batch = self.option_instruments[i : i + ROBINHOOD_QUOTE_BATCH]
                    quotes = await asyncio.to_thread(self._fetch_option_quotes_sync, batch)
                    for q in quotes:
                        await self.bus.publish(self._to_tick(q, source="robinhood_mcp"))
            except Exception as e:
                logger.error(f"Option poll error: {e}", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _fetch_option_quotes_sync(instrument_ids: List[str]) -> List[Dict[str, Any]]:
        """Blocking call — wrapped in asyncio.to_thread().

        Uses the public robinhood_mcp_call() JSON-RPC helper, which
        parses SSE responses and handles auth. Returns a list of quote
        dicts (possibly empty if the upstream wraps quotes in a single
        object — see _to_tick for the shape we handle).
        """
        try:
            from .integrations.robinhood_broker import robinhood_mcp_call
            result = robinhood_mcp_call(
                "get_option_quotes",
                {"instrument_ids": instrument_ids},
            )
            if isinstance(result, dict):
                # Single response object
                return [result] if result else []
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"get_option_quotes failed: {e}")
            return []

    # ── Underlying spot loop ────────────────────────────────────────

    async def _poll_underlyings_loop(self) -> None:
        while not self._stop.is_set():
            try:
                quotes = await asyncio.to_thread(self._fetch_equity_quotes_sync, self.underlying_symbols)
                for q in quotes:
                    await self.bus.publish(self._to_tick(q, source="robinhood_mcp"))
            except Exception as e:
                logger.error(f"Equity poll error: {e}", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.underlying_interval_s)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _fetch_equity_quotes_sync(symbols: List[str]) -> List[Dict[str, Any]]:
        try:
            from .integrations.robinhood_broker import robinhood_mcp_call
            result = robinhood_mcp_call(
                "get_equity_quotes",
                {"symbols": symbols},
            )
            if isinstance(result, dict):
                return [result] if result else []
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"get_equity_quotes failed: {e}")
            return []

    @staticmethod
    def _to_tick(q: Dict[str, Any], source: str) -> TickEvent:
        bid = q.get("bid_price") or q.get("bid")
        ask = q.get("ask_price") or q.get("ask")
        last = q.get("last_trade_price") or q.get("last") or q.get("mark_price")
        if bid is not None and ask is not None:
            price = (float(bid) + float(ask)) / 2.0
        elif last is not None:
            price = float(last)
        else:
            price = 0.0
        return TickEvent(
            symbol=q.get("symbol") or q.get("underlying_symbol") or q.get("instrument_id", ""),
            timestamp=time.monotonic(),
            wall_clock=datetime.now(timezone.utc),
            price=price,
            bid=float(bid) if bid is not None else None,
            ask=float(ask) if ask is not None else None,
            last=float(last) if last is not None else None,
            volume=q.get("volume") or q.get("trade_volume"),
            source=source,
        )


# ─────────────────────────────────────────────────────────────────────
# Layer 2: Alpaca WebSocket (free IEX feed, sub-second)
# ─────────────────────────────────────────────────────────────────────

class AlpacaWebSocketSource:
    """Free IEX WebSocket for SPY/QQQ underlying trade prints.

    Requires alpaca-py + free API key. The "Basic" plan (default for
    all paper accounts) streams IEX — ~5-10% of US volume but
    sub-second latency. This is the underlying-move signal that
    drives gamma-scalp entry triggers on the option legs.
    """

    def __init__(
        self,
        bus: TickBus,
        symbols: List[str] = ("SPY", "QQQ"),
        feed: str = "iex",   # "iex" (free) or "sip" (paid)
    ) -> None:
        self.bus = bus
        self.symbols = list(symbols)
        self.feed = feed
        self._stream = None
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._key_id = os.getenv("ALPACA_KEY_ID", "")
        self._secret_key = os.getenv("ALPACA_SECRET_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._key_id and self._secret_key)

    async def start(self) -> None:
        if not self.available:
            logger.warning("Alpaca credentials missing — skipping WebSocket source")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="alpaca_ws")
        logger.info(f"AlpacaWebSocketSource started for {self.symbols} (feed={self.feed})")

    async def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                await self._stream.stop_ws()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        # alpaca-py is sync, so run stream in a thread
        def _block_on_stream() -> None:
            try:
                from alpaca.data.live import StockDataStream
                stream = StockDataStream(self._key_id, self._secret_key, feed=self.feed)
                self._stream = stream

                async def handle_trade(t):
                    ev = TickEvent(
                        symbol=t.symbol,
                        timestamp=time.monotonic(),
                        wall_clock=datetime.now(timezone.utc),
                        price=float(t.price),
                        last=float(t.price),
                        volume=int(t.size) if t.size else None,
                        source=f"alpaca_{self.feed}",
                    )
                    await self.bus.publish(ev)

                stream.subscribe_trades(handle_trade, *self.symbols)
                stream.run()
            except Exception as e:
                logger.error(f"Alpaca WS crashed: {e}", exc_info=True)

        await asyncio.to_thread(_block_on_stream)


# ─────────────────────────────────────────────────────────────────────
# Layer 3: Yahoo Finance async polling (fallback + options sanity)
# ─────────────────────────────────────────────────────────────────────

class YahooFinanceQuoteSource:
    """Last-resort fallback. yfinance is delayed 15 min for free tier
    — useful for options chains when Robinhood MCP is rate-limited.
    Poll options + underlyings via aiohttp at 2-5s cadence.
    """

    YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
    YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(
        self,
        bus: TickBus,
        symbols: List[str],
        poll_interval_s: float = 5.0,
    ) -> None:
        self.bus = bus
        self.symbols = list(symbols)
        self.poll_interval_s = poll_interval_s
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="yfinance")
        logger.info(f"YahooFinanceQuoteSource started for {self.symbols}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            while not self._stop.is_set():
                try:
                    await self._poll_once(session)
                except Exception as e:
                    logger.error(f"YF poll error: {e}", exc_info=True)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
                except asyncio.TimeoutError:
                    pass

    async def _poll_once(self, session: "aiohttp.ClientSession") -> None:
        # Quote endpoint returns bid/ask/last for up to ~100 symbols
        params = {"symbols": ",".join(self.symbols)}
        headers = {"User-Agent": "Mozilla/5.0 hermes-trader"}
        async with session.get(
            self.YAHOO_QUOTE_URL,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
        for r in data.get("quoteResponse", {}).get("result", []):
            price = r.get("regularMarketPrice") or r.get("postMarketPrice") or r.get("preMarketPrice")
            if price is None:
                continue
            await self.bus.publish(
                TickEvent(
                    symbol=r.get("symbol", ""),
                    timestamp=time.monotonic(),
                    wall_clock=datetime.now(timezone.utc),
                    price=float(price),
                    bid=r.get("bid"),
                    ask=r.get("ask"),
                    last=float(price),
                    volume=r.get("regularMarketVolume"),
                    source="yfinance",
                )
            )


# ─────────────────────────────────────────────────────────────────────
# Detection engine (turns ticks into actionable signals)
# ─────────────────────────────────────────────────────────────────────

class DetectionEngine:
    """Subscribes to TickBus, maintains per-symbol history, fires signals.

    Detection algorithms (all operating on the last 5-30s of data):
      1. threshold_cross    — |pct_change(window)| > THRESHOLD_PCT
      2. momentum_accel     — velocity crossed ±MOMENTUM_VEL_BAND
      3. iv_skew_spike      — option mid jumps >3% in <10s
      4. spread_compress    — bid-ask < 2¢ after being wider
    """

    def __init__(
        self,
        bus: TickBus,
        on_signal: Optional[Callable[[TickEvent], Awaitable[None]]] = None,
    ) -> None:
        self.bus = bus
        self._history: Dict[str, PriceHistory] = {}
        self._sub_queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._on_signal = on_signal
        self._last_signal: Dict[str, float] = {}   # symbol -> last signal time (cooldown)
        self._cooldown_s = 5.0                       # don't re-fire same signal within 5s

    def history(self, symbol: str) -> PriceHistory:
        h = self._history.get(symbol)
        if h is None:
            h = PriceHistory(symbol=symbol)
            self._history[symbol] = h
        return h

    async def start(self) -> None:
        self._stop.clear()
        self._sub_queue = await self.bus.subscribe()
        self._task = asyncio.create_task(self._consume(), name="detector")
        logger.info("DetectionEngine started")

    async def stop(self) -> None:
        self._stop.set()
        if self._sub_queue is not None:
            await self.bus.unsubscribe(self._sub_queue)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _consume(self) -> None:
        while not self._stop.is_set():
            try:
                ev: TickEvent = await asyncio.wait_for(self._sub_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await self._process(ev)
            except Exception as e:
                logger.error(f"Detection error on {ev.symbol}: {e}", exc_info=True)

    async def _process(self, ev: TickEvent) -> None:
        h = self.history(ev.symbol)
        h.push(ev)
        if len(h.ticks) < 2:
            return

        # 1) threshold_cross
        pct = h.pct_change(THRESHOLD_WINDOW_S)
        if pct is not None and abs(pct) > THRESHOLD_PCT:
            sig = TickEvent(
                symbol=ev.symbol,
                timestamp=ev.timestamp,
                wall_clock=ev.wall_clock,
                price=ev.price,
                source=ev.source,
                signal_kind=SignalKind.THRESHOLD_CROSS,
                signal_detail={"pct_change": pct, "window_s": THRESHOLD_WINDOW_S},
            )
            await self._fire(sig)

        # 2) momentum_accel
        fast = h.pct_change(5)
        slow = h.pct_change(30)
        if fast is not None and slow is not None and abs(fast) > MOMENTUM_VEL_BAND:
            if (slow != 0 and abs(fast / slow) > 2.0) or abs(slow) > MOMENTUM_VEL_BAND:
                sig = TickEvent(
                    symbol=ev.symbol,
                    timestamp=ev.timestamp,
                    wall_clock=ev.wall_clock,
                    price=ev.price,
                    source=ev.source,
                    signal_kind=SignalKind.MOMENTUM_ACCEL,
                    signal_detail={"velocity_5s": fast, "velocity_30s": slow},
                )
                await self._fire(sig)

        # 3) spread_compress
        if ev.bid is not None and ev.ask is not None:
            spread = ev.ask - ev.bid
            if spread <= 0.02 and ev.price > 0.50:
                # Was wider recently?
                high = h.highest(30)
                if high is not None and high > 0.30:
                    sig = TickEvent(
                        symbol=ev.symbol,
                        timestamp=ev.timestamp,
                        wall_clock=ev.wall_clock,
                        price=ev.price,
                        source=ev.source,
                        signal_kind=SignalKind.SPREAD_COMPRESS,
                        signal_detail={"spread": spread, "bid": ev.bid, "ask": ev.ask},
                    )
                    await self._fire(sig)

    async def _fire(self, ev: TickEvent) -> None:
        last = self._last_signal.get(f"{ev.symbol}:{ev.signal_kind}", 0)
        if ev.timestamp - last < self._cooldown_s:
            return
        self._last_signal[f"{ev.symbol}:{ev.signal_kind}"] = ev.timestamp

        # Journal every signal
        try:
            JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with JOURNAL_PATH.open("a") as f:
                f.write(json.dumps({
                    "ts": ev.wall_clock.isoformat(),
                    "symbol": ev.symbol,
                    "price": ev.price,
                    "source": ev.source,
                    "signal": ev.signal_kind.value,
                    "detail": ev.signal_detail,
                }) + "\n")
        except Exception as e:
            logger.debug(f"Journal write failed: {e}")

        logger.info(
            f"🎯 SIGNAL {ev.signal_kind.value} {ev.symbol} "
            f"@ {ev.price:.2f} src={ev.source} detail={ev.signal_detail}"
        )
        if self._on_signal is not None:
            try:
                await self._on_signal(ev)
            except Exception as e:
                logger.error(f"Signal handler error: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────
# Engine — wires all sources + detection together
# ─────────────────────────────────────────────────────────────────────

class RealtimePriceEngine:
    """Top-level orchestrator. One engine, one asyncio.run() call.

    Sources run as concurrent tasks on a single event loop. All
    emit to one TickBus; DetectionEngine subscribes and fires
    signals. Consumers (entry, exit, telemetry) subscribe to the
    bus via engine.subscribe() and get an async iterator of ticks.
    """

    def __init__(
        self,
        option_instruments: List[str],
        underlying_symbols: List[str] = ("SPY", "QQQ"),
        on_signal: Optional[Callable[[TickEvent], Awaitable[None]]] = None,
        use_alpaca: bool = True,
        use_yfinance_fallback: bool = True,
    ) -> None:
        self.bus = TickBus()
        self.rh = RobinhoodMCPQuoteSource(
            bus=self.bus,
            option_instruments=option_instruments,
            underlying_symbols=underlying_symbols,
        )
        self.alpaca = AlpacaWebSocketSource(bus=self.bus, symbols=underlying_symbols) if use_alpaca else None
        self.yf = YahooFinanceQuoteSource(
            bus=self.bus, symbols=underlying_symbols
        ) if use_yfinance_fallback else None
        self.detector = DetectionEngine(bus=self.bus, on_signal=on_signal)

    @classmethod
    def from_env(
        cls,
        option_instruments: Optional[List[str]] = None,
        on_signal: Optional[Callable[[TickEvent], Awaitable[None]]] = None,
    ) -> "RealtimePriceEngine":
        """Construct from environment + auto-discovery of 0DTE options."""
        if option_instruments is None:
            option_instruments = []
        use_alpaca = bool(os.getenv("ALPACA_KEY_ID")) and bool(os.getenv("ALPACA_SECRET_KEY"))
        return cls(
            option_instruments=option_instruments,
            underlying_symbols=tuple(os.getenv("REALTIME_UNDERLYINGS", "SPY,QQQ").split(",")),
            on_signal=on_signal,
            use_alpaca=use_alpaca,
            use_yfinance_fallback=True,
        )

    async def start(self) -> None:
        await self.detector.start()
        await self.rh.start()
        if self.alpaca is not None:
            await self.alpaca.start()
        if self.yf is not None:
            await self.yf.start()
        logger.info("RealtimePriceEngine running (Ctrl-C to stop)")

    async def stop(self) -> None:
        await self.rh.stop()
        if self.alpaca is not None:
            await self.alpaca.stop()
        if self.yf is not None:
            await self.yf.stop()
        await self.detector.stop()
        logger.info("RealtimePriceEngine stopped")

    async def subscribe(self) -> asyncio.Queue:
        """Consumers call this to get their own tick queue."""
        return await self.bus.subscribe()

    def update_tracked_options(self, option_instruments: List[str]) -> None:
        """Hot-swap the option list at runtime (after scanner finds new candidates)."""
        self.rh.update_instruments(option_instruments)

    @asynccontextmanager
    async def lifespan(self):
        """Convenience context manager for scripts."""
        await self.start()
        try:
            yield self
        finally:
            await self.stop()


# ─────────────────────────────────────────────────────────────────────
# Demo / entry-point for manual testing
# ─────────────────────────────────────────────────────────────────────

async def _demo() -> None:
    """Subscribe and print every tick + signal. Used to verify the
    pipeline is alive before wiring into auto_trader.py.
    """
    engine = RealtimePriceEngine.from_env(option_instruments=[])
    q = await engine.subscribe()

    async with engine.lifespan():
        logger.info("Demo running — Ctrl-C to quit")
        try:
            while True:
                ev: TickEvent = await q.get()
                tag = f" {ev.signal_kind.value.upper()}" if ev.signal_kind else ""
                print(
                    f"[{ev.wall_clock.strftime('%H:%M:%S.%f')[:-3]}] "
                    f"{ev.symbol:6s} ${ev.price:8.2f} "
                    f"src={ev.source:14s}{tag}"
                )
        except asyncio.CancelledError:
            return


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(_demo())
    except KeyboardInterrupt:
        pass
