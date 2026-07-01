"""Alpaca broker adapter — paper-first, live-ready.

Phase 1 uses Alpaca paper trading API via alpaca-py.
When live is globally unlocked, the same adapter targets the live API.
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import config
from ..models.order_request import OrderRequest
from ..models.position_snapshot import AccountSnapshot, PositionSnapshot, MarketSnapshot, RiskSnapshot

logger = logging.getLogger("hermes_trader.broker")


class BrokerError(Exception):
    """Raised on broker API failures."""


class PaperBrokerAdapter:
    """Paper/local adapter — logs orders to a journal file.

    In Phase 1, ALL trading routes through this adapter.
    Live orders are journaled and logged but NOT executed on the exchange.
    This ensures the full pipeline is exercised and auditable before going live.
    """

    def __init__(self):
        self._journal_path = config.project_root / "data" / "journals" / "paper_orders.jsonl"
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._log: list[dict] = []
        self._load_journal()

    # ── Account ───────────────────────────────────────────────

    def get_account(self) -> AccountSnapshot:
        """Return simulated paper account state."""
        # Parse the latest journal entries for P&L tracking
        # For Phase 1, returns a default fresh account
        return self._compute_account_state()

    def get_position(self, symbol: str) -> Optional[PositionSnapshot]:
        """Return current position for a symbol, if any."""
        account = self.get_account()
        for pos in account.positions:
            if pos.symbol == symbol:
                return pos
        return None

    def get_open_orders(self) -> list[dict]:
        """Return current open (unfilled) orders."""
        return [entry for entry in self._log if entry.get("status") == "open"]

    # ── Market Data ───────────────────────────────────────────

    def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        """Return simulated market data snapshot.

        Phase 1: returns placeholder data. Real market data will
        come from alpaca-py or an MCP server in the Phase 1.5 upgrade.
        """
        return MarketSnapshot(
            timestamp=datetime.utcnow().isoformat(),
            symbol=symbol,
            last_price=0.0,
            bid=0.0,
            ask=0.0,
            volume=0,
            market_open=self._is_market_open(),
        )

    # ── Orders ────────────────────────────────────────────────

    def submit_order(self, order: OrderRequest) -> dict:
        """Journal the order. If live-unlocked, also execute."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "order_id": f"paper_{int(datetime.utcnow().timestamp())}_{order.symbol}",
            "status": "submitted",
            "side": order.side,
            "symbol": order.symbol,
            "qty": order.qty,
            "notional": order.notional,
            "order_type": order.order_type,
            "limit_price": order.limit_price,
            "order_class": order.order_class,
            "take_profit": order.take_profit,
            "stop_loss": order.stop_loss,
            "candidate_id": order.candidate_id,
        }

        if config.is_live_unlocked:
            entry["mode"] = "LIVE"
            entry["status"] = "simulated_live"  # Phase 1: still journaled, not executed
        else:
            entry["mode"] = "PAPER"

        self._append_journal(entry)
        logger.info(f"Order journaled: {entry['order_id']} ({entry['mode']})")
        return entry

    def close_position(self, symbol: str, qty: Optional[float] = None) -> dict:
        """Journal position close."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "close_position",
            "symbol": symbol,
            "qty": qty,
            "reason": "manual_close",
        }
        self._append_journal(entry)
        return entry

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an existing order."""
        entry = {"timestamp": datetime.utcnow().isoformat(), "action": "cancel_order", "order_id": order_id}
        self._append_journal(entry)
        return entry

    # ── Risk State ────────────────────────────────────────────

    def get_risk_snapshot(self) -> RiskSnapshot:
        """Compute risk snapshot from journal history."""
        # Parse completed trades from journal
        trades_today = 0
        trades_this_week = 0
        daily_pnl = 0.0
        weekly_pnl = 0.0
        monthly_pnl = 0.0
        consecutive_losses = 0
        now = datetime.utcnow()
        today_str = now.strftime("%Y-%m-%d")

        for entry in self._log:
            if entry.get("status") == "filled" or entry.get("status") == "simulated_live":
                ts = datetime.fromisoformat(entry["timestamp"])
                pnl = entry.get("filled_pnl", 0.0) or 0.0

                if ts.date() == now.date():
                    trades_today += 1
                    daily_pnl += pnl
                    if pnl < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0

                # Week check
                week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                week_start = week_start.replace(day=week_start.day - week_start.weekday())
                if ts >= week_start:
                    trades_this_week += 1
                    weekly_pnl += pnl

                if ts.month == now.month and ts.year == now.year:
                    monthly_pnl += pnl

        return RiskSnapshot(
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            monthly_pnl=monthly_pnl,
            consecutive_losses=consecutive_losses,
            trades_today=trades_today,
            trades_this_week=trades_this_week,
            daily_loss_budget_remaining=max(0.0, config.max_daily_loss_usd - abs(daily_pnl)),
            weekly_loss_budget_remaining=max(0.0, config.max_weekly_loss_usd - abs(weekly_pnl)),
            monthly_loss_budget_remaining=max(0.0, config.max_monthly_loss_usd - abs(monthly_pnl)),
        )

    # ── Internal ──────────────────────────────────────────────

    def _compute_account_state(self) -> AccountSnapshot:
        """Derive paper account state from journal."""
        start_capital = config.max_experiment_capital_usd
        total_pnl = 0.0
        positions: dict[str, PositionSnapshot] = {}

        for entry in self._log:
            if entry.get("status") in ("filled", "simulated_live"):
                side = entry.get("side", "")
                symbol = entry.get("symbol", "")
                qty = entry.get("qty", 0.0) or 0.0
                price = entry.get("filled_price", 0.0) or 0.0

                if side == "buy":
                    total_pnl -= qty * price
                    if symbol not in positions:
                        positions[symbol] = PositionSnapshot(
                            symbol=symbol, qty=0.0, market_value=0.0,
                            cost_basis=0.0, unrealized_pl=0.0, unrealized_plpc=0.0)
                    positions[symbol].qty += qty
                    positions[symbol].cost_basis += qty * price
                    positions[symbol].market_value += qty * price
                elif side == "sell":
                    total_pnl += qty * price
                    if symbol in positions:
                        positions[symbol].qty -= qty
                        positions[symbol].market_value -= qty * price

        equity = start_capital + total_pnl
        return AccountSnapshot(
            equity=max(0.0, equity),
            cash=max(0.0, equity),
            buying_power=max(0.0, equity * 2),
            portfolio_value=max(0.0, equity),
            positions=list(positions.values()),
            open_orders_count=len(self.get_open_orders()),
        )

    def _is_market_open(self) -> bool:
        """Return whether US equity markets are open."""
        now = datetime.utcnow()
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        # NYSE: 9:30-16:00 ET = 13:30-20:00 UTC (EST) or 14:30-21:00 UTC (EDT)
        hour_utc = now.hour
        return 13 <= hour_utc <= 20

    def _load_journal(self) -> None:
        if self._journal_path.exists():
            with open(self._journal_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self._log.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning(f"Skipping malformed journal line: {line[:80]}")

    def _append_journal(self, entry: dict) -> None:
        self._log.append(entry)
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")