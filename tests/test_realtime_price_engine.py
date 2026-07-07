"""Tests for the real-time price detection engine.

Covers the pure-logic pieces (PriceHistory, DetectionEngine) without
hitting the network. The MCP/WS sources are excluded — they need
real credentials and live market data.
"""
import asyncio
import time
from datetime import datetime, timezone

import pytest

from hermes_trader.realtime_price_engine import (
    DetectionEngine,
    PriceHistory,
    SignalKind,
    TickBus,
    TickEvent,
)


def _tick(symbol: str, price: float, t: float) -> TickEvent:
    return TickEvent(
        symbol=symbol,
        timestamp=t,
        wall_clock=datetime.now(timezone.utc),
        price=price,
        source="test",
    )


class TestPriceHistory:
    def test_velocity_returns_none_with_one_tick(self):
        h = PriceHistory("SPY")
        h.push(_tick("SPY", 100.0, 0.0))
        assert h.velocity(30) is None

    def test_velocity_positive_move(self):
        h = PriceHistory("SPY")
        for i, p in enumerate([100.0, 100.1, 100.2, 100.3]):
            h.push(_tick("SPY", p, float(i)))
        # Last 30s covers all 4 ticks; velocity ≈ 0.3% over 3s
        v = h.velocity(30)
        assert v is not None
        assert v > 0.002

    def test_pct_change_signs(self):
        h = PriceHistory("SPY")
        # 100 → 99.5 over 3 ticks
        for i, p in enumerate([100.0, 99.8, 99.5]):
            h.push(_tick("SPY", p, float(i)))
        v = h.velocity(30)
        assert v is not None and v < 0  # negative

    def test_highest_lowest(self):
        h = PriceHistory("SPY")
        for i, p in enumerate([100, 102, 101, 99, 103]):
            h.push(_tick("SPY", p, float(i)))
        assert h.highest(30) == 103
        assert h.lowest(30) == 99

    def test_window_excludes_old_ticks(self):
        h = PriceHistory("SPY")
        h.push(_tick("SPY", 200.0, 0.0))  # way back
        h.push(_tick("SPY", 100.0, 100.0))  # now
        # 5s window: only the most recent
        assert h.highest(5) == 100.0
        # 200s window: includes the 200 baseline
        assert h.highest(200) == 200.0


class TestTickBus:
    def test_subscribe_and_publish(self):
        async def run():
            bus = TickBus()
            q1 = await bus.subscribe()
            q2 = await bus.subscribe()
            ev = _tick("SPY", 100.0, 0.0)
            await bus.publish(ev)
            assert (await q1.get()).symbol == "SPY"
            assert (await q2.get()).symbol == "SPY"
        asyncio.run(run())

    def test_unsubscribe(self):
        async def run():
            bus = TickBus()
            q = await bus.subscribe()
            await bus.unsubscribe(q)
            await bus.publish(_tick("SPY", 1.0, 0.0))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(q.get(), timeout=0.1)
        asyncio.run(run())

    def test_slow_consumer_drops_oldest(self):
        async def run():
            bus = TickBus(maxsize=2)
            q = await bus.subscribe()
            # Fill queue to capacity
            await bus.publish(_tick("SPY", 1.0, 0.0))
            await bus.publish(_tick("SPY", 2.0, 0.0))
            # Next publish should drop oldest
            await bus.publish(_tick("SPY", 3.0, 0.0))
            # Drain — should get 2.0 then 3.0
            e2 = await asyncio.wait_for(q.get(), timeout=0.5)
            e3 = await asyncio.wait_for(q.get(), timeout=0.5)
            assert e2.price == 2.0
            assert e3.price == 3.0
        asyncio.run(run())


class TestDetectionEngine:
    def test_threshold_cross_fires(self):
        captured: list[TickEvent] = []

        async def handler(ev: TickEvent) -> None:
            captured.append(ev)

        async def run():
            bus = TickBus()
            det = DetectionEngine(bus=bus, on_signal=handler)
            await det.start()
            q = await bus.subscribe()
            try:
                # Inject baseline + ramp up 0.1% over 4s (>0.05% threshold)
                base = time.monotonic()
                await bus.publish(_tick("SPY", 100.00, base))
                for i, p in enumerate([100.01, 100.03, 100.05, 100.10, 100.12]):
                    await bus.publish(_tick("SPY", p, base + i + 1))
                # Give the detector a moment to process
                await asyncio.sleep(0.2)
            finally:
                await det.stop()
            # We expect at least one threshold_cross signal
            kinds = [c.signal_kind for c in captured]
            assert SignalKind.THRESHOLD_CROSS in kinds, f"got {kinds}"
        asyncio.run(run())

    def test_cooldown_prevents_repeat_fires(self):
        """Same signal kind on same symbol should not fire twice in <5s."""
        captured: list[TickEvent] = []

        async def handler(ev: TickEvent) -> None:
            captured.append(ev)

        async def run():
            bus = TickBus()
            det = DetectionEngine(bus=bus, on_signal=handler)  # type: ignore
            det._cooldown_s = 5.0
            await det.start()
            try:
                base = time.monotonic()
                # Big move up
                await bus.publish(_tick("SPY", 100.00, base))
                await bus.publish(_tick("SPY", 100.20, base + 1))
                # Small additional move — should not re-fire (cooldown)
                await bus.publish(_tick("SPY", 100.22, base + 2))
                await asyncio.sleep(0.3)
            finally:
                await det.stop()
            thr = [c for c in captured if c.signal_kind == SignalKind.THRESHOLD_CROSS]
            assert len(thr) <= 1, f"expected ≤1 threshold_cross, got {len(thr)}"
        asyncio.run(run())

    def test_history_isolated_per_symbol(self):
        h = PriceHistory("SPY")
        h2 = PriceHistory("QQQ")
        h.push(_tick("SPY", 100.0, 0.0))
        h.push(_tick("SPY", 101.0, 1.0))
        h2.push(_tick("QQQ", 400.0, 0.0))
        # QQQ only has 1 tick → velocity None
        assert h2.velocity(30) is None
        # SPY has 2 → velocity positive
        v = h.velocity(30)
        assert v is not None and v > 0
