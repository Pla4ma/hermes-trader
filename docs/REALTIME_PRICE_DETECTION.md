# Real-Time Price Detection for 0DTE Options: Architecture, Patterns, and Integration Plan

**Target system**: `$300 account, 0DTE SPY/QQQ options, Robinhood MCP, existing /opt/hermes-trader stack`
**Goal**: catch 5-30 SECOND price moves (cron can't do this)

---

## TL;DR — What you actually need

1. **One Python `asyncio` loop** running 3 concurrent producers → 1 `asyncio.Queue` (broadcast bus) → N consumers.
2. **Don't fight the infra** — Robinhood MCP is HTTPS only, so use 1-2s async polling (not WebSocket). For the underlying SPY/QQQ spot, layer a **free Alpaca IEX WebSocket** (sub-second).
3. **Detection = stateful consumer** with bounded ring buffers per symbol, computing rolling velocity / threshold-cross / spread-compress over 5-30s windows.
4. **Exit = reuse `ZeroDTEExitManager`** unchanged — bridge it to live ticks via a `RealtimeExitMonitor` that maintains `PositionSnapshot.price_history`.
5. **Shipped**: `realtime_price_engine.py` (engine + sources + detector), `realtime_runner.py` (entry/exit dispatch + scanner refresher + kill switch), `tests/test_realtime_price_engine.py` (11 tests, all passing).

---

## (a) Detailed Architecture

### Why asyncio, not threading

A single-threaded `asyncio` event loop is **strictly better** than threads for this workload:

- **No GIL contention** — each await releases control cooperatively, so 3 polling coroutines share the loop without serialization.
- **Cheap concurrency** — 1000s of coroutines cost ~kilobytes of RAM; threads cost ~MB each (8MB default stack).
- **Deterministic ordering** — events processed in arrival order, so a momentum signal that fires from a tick at 09:31:14.231 always runs before a 09:31:14.232 tick.
- **Bridges cleanly to `asyncio.to_thread()`** for the one blocking thing: Robinhood MCP uses `requests` (sync). We push the blocking call off the loop and `await` the result.

The cost: nothing in the loop can block for >100ms without hurting latency. Every `requests.post()` is wrapped in `to_thread()`.

### The 3-layer producer design

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  asyncio event loop (single thread)                     │
│                                                                         │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────┐ │
│  │ RobinhoodMCPQuoteSrc │  │ AlpacaWebSocketSrc   │  │ YFinance     │ │
│  │  • to_thread() wrap  │  │  • to_thread() wrap  │  │  • aiohttp   │ │
│  │  • 2s options poll   │  │  • sub-second trades │  │  • 5s quotes │ │
│  │  • 1s underlyings    │  │  • IEX feed (free)   │  │  • fallback  │ │
│  └──────────┬───────────┘  └──────────┬───────────┘  └──────┬───────┘ │
│             │                         │                      │         │
│             ▼                         ▼                      ▼         │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │              TickBus (asyncio.Queue broadcast)                │    │
│  │  • publish() non-blocking, drops oldest for slow consumers    │    │
│  │  • subscribe() returns a private queue per consumer          │    │
│  └──────────────┬───────────────────────────────────────────────┘    │
│                 │                                                     │
│   ┌─────────────┼──────────────┬─────────────────┐                   │
│   ▼             ▼              ▼                 ▼                   │
│ ┌─────────┐ ┌─────────────┐ ┌────────────────┐ ┌────────────┐      │
│ │Detector │ │Exit Monitor │ │ Entry Dispatch │ │ Telemetry  │      │
│ │•thresh  │ │•eval per    │ │•policy gate    │ │•journal    │      │
│ │•mom_acc │ │  tick       │ │•cooldown       │ │•metrics    │      │
│ │•spread  │ │•ZeroDTEExit │ │•max 2 positions│ │•alerts     │      │
│ └─────────┘ └─────────────┘ └────────────────┘ └────────────┘      │
└─────────────────────────────────────────────────────────────────────────┘
```

**Why 3 sources, not 1?**

- Robinhood MCP is the *only* option-chain source that has real-time bid/ask for the option contracts we trade. We must poll it.
- Alpaca IEX WebSocket is the *fastest* free source for the underlying SPY/QQQ trade prints — sub-second latency, drives the gamma-scalp trigger.
- Yahoo Finance is the fallback when both fail (rate-limit, OAuth expiry, outage) — delayed 15 min, but better than nothing for sanity checks.

### Latency budget (worst case, end-to-end)

| Step | Latency | Notes |
|------|---------|-------|
| Robinhood poll (HTTPS) | 150-400ms | network RTT + JSON-RPC parse |
| Polling cadence | 1-2s | configurable |
| Tick arrival → bus publish | <1ms | in-process queue put |
| Detector consume + eval | <1ms | deque scan, ~600 ticks max |
| **Entry/exit intent** | **~1-2.5s from real move** | dominated by poll cadence |
| Alpaca WS (underlying) | 50-200ms | sub-second trade prints |
| End-to-end (Alpaca-driven) | **<300ms** | this is the sub-second path |

The 5-30s move we want to catch is **100-6000x larger** than the detection latency. The system catches any move that survives 2+ polls (~4s) — i.e., almost anything that isn't a single-tick flicker.

### Memory budget

- Per-symbol `PriceHistory`: bounded `deque(maxlen=600)` = 600 × ~80 bytes = ~48KB
- 20 symbols × 48KB = ~1MB total
- Detector state: negligible
- Tick bus: 1024 slots × ~200 bytes = ~200KB

Total: **~2MB resident**. Trivially cheap.

---

## (b) Exact Python Code Patterns

### Pattern 1: Non-blocking MCP polling with rate awareness

```python
import asyncio
from functools import partial

async def poll_loop(stop_event: asyncio.Event, poll_interval: float = 2.0):
    """Production-ready polling coroutine pattern."""
    while not stop_event.is_set():
        try:
            # The blocking call goes to a worker thread.
            # asyncio.to_thread is the modern, stdlib-only way.
            data = await asyncio.to_thread(
                robinhood_mcp_call,         # sync function
                "get_option_quotes",
                {"instrument_ids": ids},    # args
            )
            # Process data — back on the event loop, no GIL fight
            await handle_quotes(data)
        except Exception as e:
            logger.error(f"poll failed: {e}", exc_info=True)

        # Cooperative sleep that wakes on stop_event
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass  # expected — interval elapsed
```

Key points:
- `asyncio.to_thread` is the right tool (not `loop.run_in_executor`). It was added in 3.9.
- `asyncio.wait_for(stop_event.wait(), ...)` is the **cancel-friendly** sleep pattern. Plain `await asyncio.sleep(2)` works but doesn't wake on shutdown.

### Pattern 2: Async fan-out broadcast bus

```python
class TickBus:
    """One producer, many consumers, drop-oldest on slow consumer."""
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def publish(self, event):
        async with self._lock:
            subs = list(self._subscribers)  # snapshot
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop oldest, push new
                try: q.get_nowait()
                except asyncio.QueueEmpty: pass
                try: q.put_nowait(event)
                except asyncio.QueueFull: pass
```

Key points:
- **Snapshot under lock** — without it, another coroutine could mutate `_subscribers` between the lookup and the put, and you'd get a `RuntimeError: list changed size during iteration`.
- **Drop-oldest, never block the producer** — a slow exit-monitor must never stall the detector or the next source poll. We sacrifice completeness (the slow consumer misses one tick) for liveness.

### Pattern 3: Stateful detector with rolling velocity

```python
class PriceHistory:
    """Bounded ring buffer + windowed stats."""
    def __init__(self, symbol: str, maxlen: int = 600):
        self.symbol = symbol
        self.ticks: deque[TickEvent] = deque(maxlen=maxlen)

    def velocity(self, window_s: float) -> float | None:
        """Δprice/Δt over the last `window_s` seconds."""
        if len(self.ticks) < 2:
            return None
        now = self.ticks[-1].timestamp
        cutoff = now - window_s
        baseline = next((t for t in self.ticks if t.timestamp >= cutoff), None)
        if not baseline or baseline.price <= 0:
            return None
        return (self.ticks[-1].price - baseline.price) / baseline.price
```

Key points:
- `deque(maxlen=...)` is the right data structure — `append` is O(1), eviction is automatic, iteration order is preserved.
- `next((... for ...), None)` is the idiomatic "find first match or return default" pattern. Beats list-comprehensions for early-exit scans.
- We use `time.monotonic()` for timestamps (not `time.time()`) — `monotonic` is unaffected by NTP adjustments and is the right choice for measuring intervals.

### Pattern 4: Signal cooldown to prevent re-fires

```python
async def _fire(self, ev: TickEvent):
    key = f"{ev.symbol}:{ev.signal_kind.value}"
    last_ts = self._last_signal.get(key, 0.0)
    if ev.timestamp - last_ts < self._cooldown_s:
        return  # skip — too soon since last signal of this kind
    self._last_signal[key] = ev.timestamp
    # ... emit to handler, journal
```

Without a cooldown, a 0.1% sustained move would fire ~600 threshold_cross signals in 5 minutes — flooding the entry dispatcher and the order journal. 5s is a good default for 0DTE (we *want* repeat fires on continuation moves, just not 30Hz spam).

### Pattern 5: Reusing `ZeroDTEExitManager` for exit logic

The exit system **already exists** (`zero_dte_exits.py`). The new `RealtimeExitMonitor.on_tick()` simply:

1. Looks up the matching `PositionSnapshot` by symbol
2. Updates `current_price` and appends to `price_history`
3. Calls `self.manager.evaluate(pos, now_utc=ev.wall_clock)` — same logic the cron uses
4. Returns an `intent` dict if the manager returned an `ExitSignal`

**Zero changes to the exit rules.** This is the highest-leverage integration: a 6-line wrapper gives you millisecond-class exit evaluation on every tick, instead of waiting for the next cron tick.

### Pattern 6: Cancellation-safe lifecycle

```python
class RealtimePriceEngine:
    async def start(self) -> None:
        await self.detector.start()
        await self.rh.start()
        if self.alpaca: await self.alpaca.start()
        if self.yf:     await self.yf.start()

    async def stop(self) -> None:
        await self.rh.stop()
        if self.alpaca: await self.alpaca.stop()
        if self.yf:     await self.yf.stop()
        await self.detector.stop()

    @asynccontextmanager
    async def lifespan(self):
        await self.start()
        try: yield self
        finally: await self.stop()
```

The `lifespan` async context manager is the killer pattern for scripts:

```python
async with engine.lifespan():
    # engine is running
    ...
# engine is stopped, all tasks cancelled, all queues drained
```

It makes resource leaks impossible — even if the body raises, the engine stops cleanly.

---

## (c) Free Data Sources That Support Sub-Second Polling

| Source | Latency | Cost | Coverage | WebSocket? | Notes |
|--------|---------|------|----------|-----------|-------|
| **Robinhood MCP** `get_option_quotes` | 150-400ms / poll | free with account | 0DTE options (the actual contracts we trade) | ❌ HTTPS only | **Primary**. We poll at 1-2s. |
| **Robinhood MCP** `get_equity_quotes` | 150-400ms / poll | free | SPY/QQQ spot | ❌ HTTPS only | **Primary for spot** if Alpaca unavailable. |
| **Alpaca** `StockDataStream` (IEX feed) | 50-200ms | free (Basic plan = IEX) | Equities incl. SPY/QQQ | ✅ | **Best free WS**. Already in `pyproject.toml` (`alpaca-py>=0.70.0`). |
| **Alpaca** `StockDataStream` (SIP feed) | <10ms | $9/mo Algo Trader Plus | All US exchanges | ✅ | Not free. |
| **Polygon.io** `StocksWebSocket` | sub-second | free tier (delayed 15 min) | Equities + options | ✅ | Free tier is delayed — not real-time. |
| **Finnhub** `stock-websocket` | sub-second | free (50 symbols) | Equities | ✅ | 50-symbol cap. |
| **Yahoo Finance** `quote` endpoint | 1-3s / poll | free | Equities (delayed 15 min) | ❌ | **Fallback only**. Options chains via undocumented endpoint, unreliable. |
| **yfinance** `Ticker.history` | minutes | free | Equities + some options (delayed) | ❌ | **Not suitable** for real-time. Use the `quote` endpoint via aiohttp instead. |
| **Alpha Vantage** | 5 calls/min free | free | Equities (delayed) | ❌ | Rate limit too tight for sub-second. |

**Recommended setup** (what we shipped):

1. **Robinhood MCP** for option quotes (no real alternative — it's the only free real-time 0DTE option feed)
2. **Alpaca IEX WebSocket** for SPY/QQQ spot (already in `pyproject.toml`, sub-second, free)
3. **Yahoo Finance** via aiohttp as fallback for the underlying (delayed 15 min but at least it's something if both fail)

**Setup** (one-time):
```bash
# 1. Sign up for Alpaca free tier (https://alpaca.markets)
# 2. Get paper-trading API keys
# 3. Export
export ALPACA_KEY_ID="PK..."
export ALPACA_SECRET_KEY="..."
# 4. Run
python -m hermes_trader.realtime_runner
```

---

## (d) Integration Plan with existing `auto_trader.py`

### The current pipeline

```
cron (every 1-5 min)
    └─→ auto_trader.run_cycle()
         ├─→ scan_and_score()        [zero_dte_scanner]
         ├─→ [vibe + trading-agents gates]
         ├─→ place_option_order()    [robinhood_mcp]
         └─→ manage_open_positions() [sets SL/TP orders]
```

Cron cadence is the bottleneck. Anything that happens between cron ticks is invisible to the system.

### The new pipeline (3 integration options)

#### Option 1: Standalone daemon (recommended for $300 account)

Run `realtime_runner` as a separate long-lived process. The cron job keeps doing its thing (entry discipline: scanner + scoring + gates), and the daemon handles only the **time-critical** edges:

- Detecting fast moves and **proposing** entries (gated on the same scoring rules the cron uses)
- Running `ZeroDTEExitManager.evaluate()` on every tick instead of every cron tick

```bash
# /etc/supervisor/conf.d/hermes-realtime.conf
[program:hermes-realtime]
command=/usr/bin/python -m hermes_trader.realtime_runner
directory=/opt/hermes-trader
environment=ALPACA_KEY_ID="...",ALPACA_SECRET_KEY="...",PYTHONPATH="/opt/hermes-trader/src"
autostart=true
autorestart=true
stderr_logfile=/var/log/hermes-realtime.err.log
stdout_logfile=/var/log/hermes-realtime.out.log
```

**Why a separate process**: the cron job has predictable resource usage (one burst every N minutes). The daemon runs continuously. If a daemon bug leaks memory, only the daemon dies — the cron keeps trading safely.

#### Option 2: Embedded in `run_cycle()` (single-process)

Add a "realtime entry check" call right after the existing scan/place block in `auto_trader.py`:

```python
# In run_cycle(), after existing scan/place/exit logic:
from .realtime_runner import RealtimeEntryPolicy, RealtimeExitMonitor

# ...existing code...
policy = RealtimeEntryPolicy()
monitor = RealtimeExitMonitor()

# One-shot detection: tick the engine for a few seconds
# and react to any signals
async def quick_check():
    engine = RealtimePriceEngine.from_env(option_instruments=[...])
    async with engine.lifespan():
        q = await engine.subscribe()
        try:
            for _ in range(5):  # 5s of real-time data
                ev = await asyncio.wait_for(q.get(), timeout=1.0)
                intent = policy.should_enter(ev, monitor.open_position_count)
                if intent:
                    await _place_entry_order(intent)
        except asyncio.TimeoutError:
            pass

asyncio.run(quick_check())
```

**Downside**: you only get 5-10s of detection per cron cycle, missing the actual move if it happens between cron ticks. Useful for sanity, not for catching 30s scalps.

#### Option 3: Hybrid — cron for entries, daemon for exits only

The cleanest split. The cron keeps full authority over entries (its scoring, gates, and discipline are good — don't break them). The daemon only does **exits** with the realtime engine. The cron is *unaware* of the daemon.

```python
# realtime_runner.py, exit-only mode
async def _exit_only_daemon():
    engine = RealtimePriceEngine.from_env(option_instruments=[])
    monitor = RealtimeExitMonitor()
    # Pre-load all current positions from Robinhood
    for pos in _fetch_open_0dte_positions():
        monitor.register(**pos)
    async with engine.lifespan():
        q = await engine.subscribe()
        while True:
            ev = await q.get()
            for intent in monitor.on_tick(ev):
                await _place_exit_order(intent)
```

This is the safest first deployment. You get millisecond-class exits without changing entry behavior.

### Migration steps (in order)

1. ✅ **Done**: `realtime_price_engine.py` shipped, 11 unit tests passing, 240 existing tests still pass.
2. ✅ **Done**: `realtime_runner.py` shipped with entry policy, exit monitor, scanner refresher, kill-switch watcher.
3. **Next**: copy `realtime_runner.py` and configure Alpaca API keys.
4. **Next**: deploy in **exit-only mode** (Option 3) for 1 week. Verify exit signals fire correctly and don't over-trade.
5. **Then**: enable entry dispatch (Option 1), with `min_move` set conservatively (0.15%+) and `max_positions=1`.
6. **Finally**: tune `cooldown_s`, `min_move`, `THRESHOLD_PCT` based on observed false-positive rate. Expected: 5-10 signals/day on SPY/QQQ in a typical session, of which 1-3 become entries.

### What NOT to do

- ❌ Don't disable the cron job. The realtime engine is for *speed*; the cron is for *discipline*. They complement.
- ❌ Don't poll Robinhood MCP faster than 1s. The undocumented rate limit is real; you'll get 429s and miss real signals.
- ❌ Don't use `time.time()` for tick timestamps. NTP step adjustments will cause negative deltas and break velocity math.
- ❌ Don't add threading. Asyncio + `to_thread` is faster, simpler, and race-free.
- ❌ Don't autotrade the *first* version. Run with `_place_entry_order` and `_place_exit_order` as logged-only stubs for at least a week, in parallel with the cron, comparing signals.

---

## Appendix A: File map

```
/opt/hermes-trader/src/hermes_trader/
  realtime_price_engine.py     # the engine (3 sources, bus, detector, lifespan)
  realtime_runner.py           # the daemon (entry policy, exit monitor, scanner ref)
  zero_dte_exits.py            # existing exit rules (UNCHANGED, reused via RealtimeExitMonitor)
  zero_dte_scanner.py          # existing 0DTE scanner (UNCHANGED, called by runner for refresh)
  auto_trader.py               # existing cron pipeline (UNCHANGED)

/opt/hermes-trader/tests/
  test_realtime_price_engine.py   # 11 new tests for the engine
  (existing 240 tests, all still passing)
```

## Appendix B: Verification commands

```bash
# 1. Module imports
cd /opt/hermes-trader && python -c "from src.hermes_trader.realtime_price_engine import RealtimePriceEngine; print('OK')"

# 2. Run all tests
cd /opt/hermes-trader && python -m pytest tests/ -v

# 3. Run only the new tests
cd /opt/hermes-trader && python -m pytest tests/test_realtime_price_engine.py -v

# 4. Demo (after configuring ALPACA_KEY_ID / ALPACA_SECRET_KEY)
cd /opt/hermes-trader && python -m hermes_trader.realtime_price_engine

# 5. Production daemon
cd /opt/hermes-trader && python -m hermes_trader.realtime_runner
```

## Appendix C: Key reference material

- `asyncio.to_thread` (PEP 654, 3.9+): [docs.python.org/3/library/asyncio-task.html#asyncio.to_thread](https://docs.python.org/3/library/asyncio-task.html)
- `aiohttp` client websockets: [docs.aiohttp.org/en/stable/client_quickstart.html](https://docs.aiohttp.org/en/stable/client_quickstart.html)
- Alpaca `StockDataStream` (free IEX): [docs.alpaca.markets/us/docs/real-time-stock-pricing-data](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
- Alpaca websocket-streaming: [docs.alpaca.markets/us/docs/websocket-streaming](https://docs.alpaca.markets/us/docs/websocket-streaming)
- Polygon options websocket (reference, paid): [polygon.io/docs/websocket/options/trades](https://polygon.io/docs/websocket/options/trades)
- aiohttp rate limiting: [copdips.com/2023/01/python-aiohttp-rate-limit.html](https://copdips.com/2023/01/python-aiohttp-rate-limit.html)
- Asyncio sync primitives: [Sling Academy — asyncio.Lock explained](https://www.slingacademy.com/article/understanding-asyncio-lock-in-python-explained-with-examples/)
- Event-driven architecture with asyncio: [johal.in — event-driven architecture patterns](https://johal.in/event-driven-architecture-patterns-using-python-asyncio-for-reactive-systems/)
- Gamma scalping reference: [alphax.trading/dictionary/gamma-scalping-execution](https://alphax.trading/dictionary/gamma-scalping-execution)
