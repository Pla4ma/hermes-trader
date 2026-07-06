#!/usr/bin/env python3
"""
0DTE Exit Management System
============================
Time-based, profit-target, stop-loss, and momentum exits for same-day options.

Exit rules (from /root task spec):
  1. TIME STOP:      Force-close all 0DTE positions at 3:45 PM ET
  2. PROFIT TARGET:  Sell 50% at +50% profit, sell rest at +100%
  3. STOP LOSS:      Close entire position at -50% loss
  4. MOMENTUM EXIT:  If option drops 30% within any 5-min window → close immediately

P&L is tracked via Robinhood MCP get_option_positions.

Architecture
------------
ZeroDTEExitManager is a *stateless evaluator*. It takes a position snapshot
and returns an ExitSignal (or None). The caller places the order.

Usage::

    mgr = ZeroDTEExitManager()
    signal = mgr.evaluate(position, current_price, entry_price, timestamps)
    if signal:
        # signal.action        → EXIT_FULL / EXIT_HALF / NO_ACTION
        # signal.reason        → human-readable reason
        # signal.exit_price    → suggested limit price (or None for market)
        # signal.quantity      → contracts to close
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hermes_trader.zero_dte_exits")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ET = timezone(timedelta(hours=-4))  # EDT

# Time stop: force-close at 3:45 PM ET
TIME_STOP_HOUR = 15
TIME_STOP_MINUTE = 45

# Profit targets
PROFIT_TARGET_1_PCT = 0.50   # sell 50% of position at +50%
PROFIT_TARGET_2_PCT = 1.00   # sell rest at +100%

# Stop loss
STOP_LOSS_PCT = -0.50        # close at -50%

# Momentum exit
MOMENTUM_DROP_PCT = -0.30    # 30% drop
MOMENTUM_WINDOW_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class ExitAction(str, Enum):
    NO_ACTION = "no_action"
    EXIT_FULL = "exit_full"
    EXIT_HALF = "exit_half"        # sell 50% of remaining
    EXIT_PARTIAL = "exit_partial"  # sell a specific quantity


class ExitReason(str, Enum):
    TIME_STOP = "time_stop"
    PROFIT_TARGET_1 = "profit_target_1"
    PROFIT_TARGET_2 = "profit_target_2"
    STOP_LOSS = "stop_loss"
    MOMENTUM_EXIT = "momentum_exit"


@dataclass
class PriceSnapshot:
    """A single price observation with timestamp."""
    price: float
    timestamp: datetime  # UTC


@dataclass
class PositionSnapshot:
    """Represents one 0DTE position for exit evaluation."""
    option_id: str              # Robinhood instrument UUID
    symbol: str                 # underlying ticker
    option_type: str            # "call" or "put"
    quantity: int               # total contracts held
    entry_price: float          # avg cost per contract ($)
    current_price: float        # last mid/mark price ($)
    strike: float
    expiration: str             # YYYY-MM-DD
    # Price history for momentum detection
    price_history: List[PriceSnapshot] = field(default_factory=list)
    # Exit tracking
    half_sold: bool = False     # True if profit_target_1 already triggered
    entry_time: Optional[datetime] = None


@dataclass
class ExitSignal:
    """Signal emitted by the exit manager."""
    action: ExitAction
    reason: ExitReason
    quantity: int               # contracts to close
    exit_price: Optional[float] = None  # limit price hint
    pnl_pct: float = 0.0       # current unrealized P&L %
    pnl_dollars: float = 0.0   # current unrealized P&L $
    signals: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Exit Manager
# ---------------------------------------------------------------------------

class ZeroDTEExitManager:
    """Stateless evaluator for 0DTE option exit decisions."""

    def __init__(
        self,
        profit_target_1: float = PROFIT_TARGET_1_PCT,
        profit_target_2: float = PROFIT_TARGET_2_PCT,
        stop_loss: float = STOP_LOSS_PCT,
        momentum_drop: float = MOMENTUM_DROP_PCT,
        momentum_window: int = MOMENTUM_WINDOW_SECONDS,
        time_stop_hour: int = TIME_STOP_HOUR,
        time_stop_minute: int = TIME_STOP_MINUTE,
    ):
        self.profit_target_1 = profit_target_1
        self.profit_target_2 = profit_target_2
        self.stop_loss = stop_loss
        self.momentum_drop = momentum_drop
        self.momentum_window = momentum_window
        self.time_stop_hour = time_stop_hour
        self.time_stop_minute = time_stop_minute

    # ------------------------------------------------------------------
    # P&L calculations
    # ------------------------------------------------------------------

    @staticmethod
    def unrealized_pnl_pct(entry_price: float, current_price: float) -> float:
        """Unrealized P&L as a fraction of entry price."""
        if entry_price <= 0:
            return 0.0
        return (current_price - entry_price) / entry_price

    @staticmethod
    def unrealized_pnl_dollars(
        entry_price: float, current_price: float, quantity: int,
        multiplier: int = 100
    ) -> float:
        """Unrealized P&L in dollars."""
        return (current_price - entry_price) * quantity * multiplier

    # ------------------------------------------------------------------
    # Time stop
    # ------------------------------------------------------------------

    def check_time_stop(
        self, now_utc: Optional[datetime] = None
    ) -> bool:
        """True if current time >= 3:45 PM ET (force-close zone)."""
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        et = now_utc.astimezone(_ET)
        cutoff = et.replace(
            hour=self.time_stop_hour,
            minute=self.time_stop_minute,
            second=0, microsecond=0
        )
        return et >= cutoff

    # ------------------------------------------------------------------
    # Profit targets
    # ------------------------------------------------------------------

    def check_profit_target(
        self, pos: PositionSnapshot, now_utc: Optional[datetime] = None
    ) -> Optional[ExitSignal]:
        """Check profit targets. Returns signal if triggered, else None."""
        pnl = self.unrealized_pnl_pct(pos.entry_price, pos.current_price)
        signals_log: list[str] = []

        # Target 2: +100% → exit everything remaining
        if pnl >= self.profit_target_2 and not pos.half_sold:
            qty = pos.quantity
            signals_log.append(
                f"🎯 PROFIT TARGET 2: +{pnl:.0%} (≥{self.profit_target_2:.0%}) → close all {qty} contracts"
            )
            return ExitSignal(
                action=ExitAction.EXIT_FULL,
                reason=ExitReason.PROFIT_TARGET_2,
                quantity=qty,
                exit_price=pos.current_price,
                pnl_pct=pnl,
                pnl_dollars=self.unrealized_pnl_dollars(
                    pos.entry_price, pos.current_price, qty
                ),
                signals=signals_log,
            )

        if pnl >= self.profit_target_2 and pos.half_sold:
            # Remaining half hit target 2
            qty = max(pos.quantity - (pos.quantity // 2), 1)
            signals_log.append(
                f"🎯 PROFIT TARGET 2 (rest): +{pnl:.0%} → close remaining {qty} contracts"
            )
            return ExitSignal(
                action=ExitAction.EXIT_FULL,
                reason=ExitReason.PROFIT_TARGET_2,
                quantity=qty,
                exit_price=pos.current_price,
                pnl_pct=pnl,
                pnl_dollars=self.unrealized_pnl_dollars(
                    pos.entry_price, pos.current_price, qty
                ),
                signals=signals_log,
            )

        # Target 1: +50% → sell half
        if pnl >= self.profit_target_1 and not pos.half_sold:
            qty = max(pos.quantity // 2, 1)
            signals_log.append(
                f"🎯 PROFIT TARGET 1: +{pnl:.0%} (≥{self.profit_target_1:.0%}) → sell {qty}/{pos.quantity} contracts"
            )
            return ExitSignal(
                action=ExitAction.EXIT_HALF,
                reason=ExitReason.PROFIT_TARGET_1,
                quantity=qty,
                exit_price=pos.current_price,
                pnl_pct=pnl,
                pnl_dollars=self.unrealized_pnl_dollars(
                    pos.entry_price, pos.current_price, qty
                ),
                signals=signals_log,
            )

        return None

    # ------------------------------------------------------------------
    # Stop loss
    # ------------------------------------------------------------------

    def check_stop_loss(self, pos: PositionSnapshot) -> Optional[ExitSignal]:
        """Check if position has hit the stop loss."""
        pnl = self.unrealized_pnl_pct(pos.entry_price, pos.current_price)

        if pnl <= self.stop_loss:
            signals_log = [
                f"🛑 STOP LOSS: {pnl:.0%} (≤{self.stop_loss:.0%}) → close all {pos.quantity} contracts"
            ]
            return ExitSignal(
                action=ExitAction.EXIT_FULL,
                reason=ExitReason.STOP_LOSS,
                quantity=pos.quantity,
                exit_price=pos.current_price,
                pnl_pct=pnl,
                pnl_dollars=self.unrealized_pnl_dollars(
                    pos.entry_price, pos.current_price, pos.quantity
                ),
                signals=signals_log,
            )
        return None

    # ------------------------------------------------------------------
    # Momentum exit (rapid price drop)
    # ------------------------------------------------------------------

    def check_momentum(
        self, pos: PositionSnapshot, now_utc: Optional[datetime] = None
    ) -> Optional[ExitSignal]:
        """Check if option dropped ≥30% in the last 5 minutes."""
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        history = pos.price_history
        if len(history) < 2:
            return None

        # Find the highest price within the momentum window
        cutoff = now_utc - timedelta(seconds=self.momentum_window)
        recent_prices = [
            s for s in history if s.timestamp >= cutoff
        ]
        if len(recent_prices) < 2:
            return None

        # Check drop from peak in window to current
        peak = max(s.price for s in recent_prices)
        current = pos.current_price

        if peak <= 0:
            return None

        drop_pct = (current - peak) / peak

        if drop_pct <= self.momentum_drop:
            signals_log = [
                f"⚡ MOMENTUM EXIT: dropped {drop_pct:.0%} "
                f"(from ${peak:.2f} → ${current:.2f}) in ≤{self.momentum_window // 60}min "
                f"→ close all {pos.quantity} contracts"
            ]
            return ExitSignal(
                action=ExitAction.EXIT_FULL,
                reason=ExitReason.MOMENTUM_EXIT,
                quantity=pos.quantity,
                exit_price=pos.current_price,
                pnl_pct=self.unrealized_pnl_pct(pos.entry_price, pos.current_price),
                pnl_dollars=self.unrealized_pnl_dollars(
                    pos.entry_price, pos.current_price, pos.quantity
                ),
                signals=signals_log,
            )
        return None

    # ------------------------------------------------------------------
    # Master evaluator
    # ------------------------------------------------------------------

    def evaluate(
        self,
        pos: PositionSnapshot,
        now_utc: Optional[datetime] = None,
    ) -> Optional[ExitSignal]:
        """Evaluate all exit conditions and return the highest-priority signal.

        Priority order (first match wins):
          1. Time stop       (force-close, non-negotiable)
          2. Stop loss        (protect capital)
          3. Momentum exit    (rapid adverse move)
          4. Profit targets   (take profits)

        Returns None if no exit is warranted.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        # 1. Time stop — highest priority, no escape
        if self.check_time_stop(now_utc):
            pnl = self.unrealized_pnl_pct(pos.entry_price, pos.current_price)
            signals = [
                f"⏰ TIME STOP: {pos.symbol} {pos.option_type} {pos.strike} "
                f"exp={pos.expiration} — force close at 3:45 PM ET "
                f"(current P&L: {pnl:+.0%})"
            ]
            return ExitSignal(
                action=ExitAction.EXIT_FULL,
                reason=ExitReason.TIME_STOP,
                quantity=pos.quantity,
                exit_price=pos.current_price,
                pnl_pct=pnl,
                pnl_dollars=self.unrealized_pnl_dollars(
                    pos.entry_price, pos.current_price, pos.quantity
                ),
                signals=signals,
            )

        # 2. Stop loss
        sl = self.check_stop_loss(pos)
        if sl:
            return sl

        # 3. Momentum exit
        mom = self.check_momentum(pos, now_utc)
        if mom:
            return mom

        # 4. Profit targets
        pt = self.check_profit_target(pos, now_utc)
        if pt:
            return pt

        # No action
        return None

    # ------------------------------------------------------------------
    # Robinhood MCP integration
    # ------------------------------------------------------------------

    def get_positions_from_robinhood(
        self, account_number: str
    ) -> List[Dict[str, Any]]:
        """Fetch current option positions from Robinhood MCP.

        Returns list of position dicts from get_option_positions.
        """
        try:
            import subprocess
            # Use the MCP tool via the hermes CLI or direct HTTP
            # This is a placeholder — in production, the caller should
            # use mcp_robinhood_get_option_positions directly.
            logger.info(
                f"Fetching 0DTE positions for account {account_number}"
            )
            return []
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    def build_snapshots_from_robinhood(
        self,
        account_number: str,
        get_positions_fn=None,
        get_quotes_fn=None,
    ) -> List[PositionSnapshot]:
        """Build PositionSnapshot objects from Robinhood data.

        In practice, this is called by the orchestrator that has access
        to the MCP tools. Pass callback functions that wrap the MCP calls.
        """
        # This is a framework — the orchestrator fills in the callbacks
        return []


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mgr = ZeroDTEExitManager()

    now = datetime.now(timezone.utc)

    # Simulate positions
    positions = [
        PositionSnapshot(
            option_id="test-1",
            symbol="SPY",
            option_type="call",
            quantity=2,
            entry_price=0.45,
            current_price=0.72,  # +60% → should trigger profit_target_1
            strike=560.0,
            expiration="2026-07-06",
            price_history=[
                PriceSnapshot(0.45, now - timedelta(minutes=30)),
                PriceSnapshot(0.50, now - timedelta(minutes=20)),
                PriceSnapshot(0.72, now - timedelta(minutes=1)),
            ],
            half_sold=False,
        ),
        PositionSnapshot(
            option_id="test-2",
            symbol="SPY",
            option_type="put",
            quantity=1,
            entry_price=0.30,
            current_price=0.12,  # -60% → should trigger stop loss
            strike=555.0,
            expiration="2026-07-06",
            price_history=[
                PriceSnapshot(0.30, now - timedelta(minutes=15)),
                PriceSnapshot(0.20, now - timedelta(minutes=10)),
                PriceSnapshot(0.12, now - timedelta(minutes=2)),
            ],
        ),
        PositionSnapshot(
            option_id="test-3",
            symbol="QQQ",
            option_type="call",
            quantity=3,
            entry_price=0.55,
            current_price=0.38,  # was at 0.55 five min ago → -31% momentum
            strike=500.0,
            expiration="2026-07-06",
            price_history=[
                PriceSnapshot(0.55, now - timedelta(minutes=6)),
                PriceSnapshot(0.54, now - timedelta(minutes=4)),
                PriceSnapshot(0.38, now - timedelta(minutes=1)),
            ],
        ),
    ]

    print(f"{'='*70}")
    print(f"  0DTE EXIT MANAGER — {now.astimezone(_ET).strftime('%I:%M %p ET')}")
    print(f"{'='*70}")

    for pos in positions:
        signal = mgr.evaluate(pos, now)
        print(f"\n  {pos.symbol} {pos.option_type.upper()} {pos.strike} "
              f"exp={pos.expiration}")
        print(f"  Entry: ${pos.entry_price:.2f} → Current: ${pos.current_price:.2f}")

        if signal:
            print(f"  >>> {signal.action.value.upper()}: {signal.reason.value}")
            for s in signal.signals:
                print(f"      {s}")
        else:
            print(f"  ✅ HOLD — no exit signal")
