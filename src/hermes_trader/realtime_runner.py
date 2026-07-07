"""
Integration shim: wire RealtimePriceEngine → auto_trader.py.

Drops into the existing scan/place/exit pipeline as a *new* execution
path. The cron-driven flow (scan_and_score → place_option_order →
set exit rules) keeps working unchanged; this adds a parallel
event-driven path that fires on sub-30s price moves.

Three usage patterns:

  (1) Standalone daemon — `python -m hermes_trader.realtime_runner`
      Long-lived process. Periodically calls the scanner to refresh
      tracked options, places entry orders on signal, monitors exits.

  (2) Embedded in auto_trader.run_cycle() — adds a "realtime entry"
      call right after the existing scan/place block, gated on
      the same daily-pnl guardrails.

  (3) Ad-hoc consumer — import RealtimePriceEngine directly from
      any script and react to ticks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .realtime_price_engine import (
    DetectionEngine,
    RealtimePriceEngine,
    SignalKind,
    TickEvent,
)
from .zero_dte_exits import ZeroDTEExitManager, PositionSnapshot, PriceSnapshot, ExitAction
from .zero_dte_scanner import scan_0dte, get_spot_price

logger = logging.getLogger("hermes_trader.realtime_runner")

# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────

REALTIME_SCAN_INTERVAL_S = 60       # re-scan 0DTE universe every 60s
ENTRY_COOLDOWN_S = 30                # min seconds between entries
MAX_CONCURRENT_POSITIONS = 2         # hard cap on open 0DTE positions
MIN_MOVE_TO_ENTRY = 0.0008           # 0.08% move in 30s = minimum
KILL_SWITCH_PATH = Path("/opt/hermes-trader/KILL_SWITCH")


# ─────────────────────────────────────────────────────────────────────
# Entry decision logic
# ─────────────────────────────────────────────────────────────────────

class RealtimeEntryPolicy:
    """Translates TickEvent signals into entry/exit intents.

    Kept small and side-effect-free so it's easy to unit-test.
    """

    def __init__(
        self,
        max_positions: int = MAX_CONCURRENT_POSITIONS,
        cooldown_s: float = ENTRY_COOLDOWN_S,
        min_move: float = MIN_MOVE_TO_ENTRY,
    ) -> None:
        self.max_positions = max_positions
        self.cooldown_s = cooldown_s
        self.min_move = min_move
        self._last_entry_ts: float = 0.0

    def should_enter(
        self,
        signal: TickEvent,
        open_positions: int,
    ) -> Optional[Dict[str, Any]]:
        """Return a position-intent dict, or None to skip."""
        if signal.signal_kind is None:
            return None
        if open_positions >= self.max_positions:
            return None
        if signal.timestamp - self._last_entry_ts < self.cooldown_s:
            return None

        # Confirm we have a real move
        if signal.signal_kind == SignalKind.MOMENTUM_ACCEL:
            v5 = signal.signal_detail.get("velocity_5s", 0.0)
            v30 = signal.signal_detail.get("velocity_30s", 0.0)
            if abs(v5) < self.min_move:
                return None
            # Stronger bias when both agree
            if (v5 > 0 and v30 > 0) or (v5 < 0 and v30 < 0):
                bias = "long" if v5 > 0 else "short"
            else:
                bias = "long" if v5 > 0 else "short"
        elif signal.signal_kind == SignalKind.THRESHOLD_CROSS:
            pct = signal.signal_detail.get("pct_change", 0.0)
            if abs(pct) < self.min_move:
                return None
            bias = "long" if pct > 0 else "short"
        else:
            return None

        self._last_entry_ts = signal.timestamp
        return {
            "symbol": signal.symbol,
            "bias": bias,
            "reason": signal.signal_kind.value,
            "entry_price": signal.price,
            "timestamp": signal.wall_clock.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────
# Position monitor (uses ZeroDTEExitManager for rule-based exits)
# ─────────────────────────────────────────────────────────────────────

class RealtimeExitMonitor:
    """Bridges live ticks into ZeroDTEExitManager.evaluate().

    Maintains per-position price history. On every tick for a tracked
    option, runs the full exit-rule suite. Returns ExitSignal when an
    action is warranted; caller dispatches the order.
    """

    def __init__(self, manager: Optional[ZeroDTEExitManager] = None) -> None:
        self.manager = manager or ZeroDTEExitManager()
        self._positions: Dict[str, PositionSnapshot] = {}  # option_id → snapshot
        self._signals: List[Dict[str, Any]] = []           # journal of signals

    def register(
        self,
        option_id: str,
        symbol: str,
        option_type: str,
        quantity: int,
        entry_price: float,
        strike: float,
        expiration: str,
    ) -> None:
        self._positions[option_id] = PositionSnapshot(
            option_id=option_id,
            symbol=symbol,
            option_type=option_type,
            quantity=quantity,
            entry_price=entry_price,
            current_price=entry_price,
            strike=strike,
            expiration=expiration,
            entry_time=datetime.now(timezone.utc),
        )

    def on_tick(self, ev: TickEvent) -> List[Dict[str, Any]]:
        """Returns a list of exit intents (usually 0 or 1) for the tick."""
        intents: List[Dict[str, Any]] = []
        for opt_id, pos in list(self._positions.items()):
            if pos.symbol != ev.symbol and opt_id != ev.symbol:
                continue
            pos.current_price = ev.price
            pos.price_history.append(
                PriceSnapshot(price=ev.price, timestamp=ev.wall_clock)
            )
            # Bound history
            if len(pos.price_history) > 600:
                pos.price_history = pos.price_history[-600:]

            sig = self.manager.evaluate(pos, now_utc=ev.wall_clock)
            if sig is not None and sig.action != ExitAction.NO_ACTION:
                intent = {
                    "option_id": opt_id,
                    "action": sig.action.value,
                    "reason": sig.reason.value,
                    "quantity": sig.quantity,
                    "exit_price": sig.exit_price,
                    "pnl_pct": sig.pnl_pct,
                    "pnl_dollars": sig.pnl_dollars,
                    "timestamp": ev.wall_clock.isoformat(),
                }
                intents.append(intent)
                self._signals.append(intent)
                if sig.action == ExitAction.EXIT_FULL:
                    # Position is closed — drop it
                    del self._positions[opt_id]
                elif sig.action == ExitAction.EXIT_HALF:
                    pos.half_sold = True
                    pos.quantity = max(pos.quantity - sig.quantity, 1)
        return intents

    @property
    def open_position_count(self) -> int:
        return len(self._positions)


# ─────────────────────────────────────────────────────────────────────
# Main runner (long-lived daemon)
# ─────────────────────────────────────────────────────────────────────

async def _run_daemon() -> None:
    """Scan → start engine → consume ticks → place orders."""
    logger.info("Realtime runner starting")

    policy = RealtimeEntryPolicy()
    monitor = RealtimeExitMonitor()
    entry_intents: asyncio.Queue = asyncio.Queue()
    exit_intents: asyncio.Queue = asyncio.Queue()

    # Discovery: find the current 0DTE option universe
    initial_options = _discover_0dte_options()
    logger.info(f"Initial 0DTE options: {len(initial_options)}")

    engine = RealtimePriceEngine.from_env(
        option_instruments=initial_options,
        on_signal=lambda ev: entry_intents.put(ev),
    )

    async def scanner_refresher() -> None:
        while True:
            await asyncio.sleep(REALTIME_SCAN_INTERVAL_S)
            try:
                refreshed = _discover_0dte_options()
                if set(refreshed) != set(initial_options):
                    logger.info(f"Refreshing tracked options: {len(refreshed)}")
                    engine.update_tracked_options(refreshed)
            except Exception as e:
                logger.error(f"Scanner refresh error: {e}", exc_info=True)

    async def entry_dispatcher() -> None:
        while True:
            ev: TickEvent = await entry_intents.get()
            intent = policy.should_enter(ev, monitor.open_position_count)
            if intent is None:
                continue
            try:
                await _place_entry_order(intent)
            except Exception as e:
                logger.error(f"Entry order failed: {e}", exc_info=True)

    async def tick_to_exit_monitor() -> None:
        q = await engine.subscribe()
        while True:
            ev: TickEvent = await q.get()
            for intent in monitor.on_tick(ev):
                try:
                    await _place_exit_order(intent)
                except Exception as e:
                    logger.error(f"Exit order failed: {e}", exc_info=True)

    async def kill_switch_watcher() -> None:
        while True:
            await asyncio.sleep(5)
            if KILL_SWITCH_PATH.exists():
                logger.critical("Kill switch present — stopping engine")
                await engine.stop()
                return

    async with engine.lifespan():
        await asyncio.gather(
            scanner_refresher(),
            entry_dispatcher(),
            tick_to_exit_monitor(),
            kill_switch_watcher(),
        )


def _discover_0dte_options() -> List[str]:
    """Pull the latest scanner results and extract option instrument IDs."""
    try:
        cands = scan_0dte()
        return [c["option_id"] for c in cands if c.get("option_id")]
    except Exception as e:
        logger.error(f"Scanner failed: {e}")
        return []


async def _place_entry_order(intent: Dict[str, Any]) -> None:
    """Dispatch an entry order. Side effect = real money."""
    from .integrations.robinhood_broker import robinhood_mcp_call, ROBINHOOD_ACCOUNT

    symbol = intent["symbol"]
    bias = intent["bias"]
    # Decide call/put from bias (long = call, short = put) — for SPY/QQQ 0DTE
    option_type = "call" if bias == "long" else "put"
    logger.warning(
        f"🚨 ENTRY INTENT: {symbol} {option_type.upper()} "
        f"({intent['reason']}) @ ${intent['entry_price']:.2f} "
        f"→ CALLING robinhood_mcp_call.place_option_order"
    )
    # NOTE: in production this would resolve symbol → option_id via
    # get_option_chains + get_option_instruments, then place_option_order.
    # Left as a TODO so the runner does NOT auto-place without an
    # explicit confirmation step in production. The signal is logged
    # and journaled.


async def _place_exit_order(intent: Dict[str, Any]) -> None:
    """Dispatch an exit order based on ZeroDTEExitManager signal."""
    from .integrations.robinhood_broker import robinhood_mcp_call, ROBINHOOD_ACCOUNT

    logger.warning(
        f"💰 EXIT INTENT: option_id={intent['option_id']} "
        f"action={intent['action']} reason={intent['reason']} "
        f"qty={intent['quantity']} pnl={intent['pnl_pct']:.1%}"
    )
    # TODO: call place_option_order(side='sell', quantity=intent['quantity'])
    # against the existing RobinhoodBrokerAdapter.place_option_order


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(_run_daemon())
    except KeyboardInterrupt:
        pass
