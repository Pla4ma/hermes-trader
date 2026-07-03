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
        """Return market data snapshot from yfinance."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = info.get("lastPrice", 0.0) or 0.0
            bid = info.get("bid", 0.0) or 0.0
            ask = info.get("ask", 0.0) or 0.0
            volume = int(info.get("lastVolume", 0) or 0)
            return MarketSnapshot(
                timestamp=datetime.utcnow().isoformat(),
                symbol=symbol,
                last_price=round(price, 2),
                bid=round(bid, 2),
                ask=round(ask, 2),
                volume=volume,
                market_open=self._is_market_open(),
            )
        except Exception as e:
            logger.warning(f"yfinance snapshot failed for {symbol}: {e}")
            return MarketSnapshot(
                timestamp=datetime.utcnow().isoformat(),
                symbol=symbol,
                last_price=0.0, bid=0.0, ask=0.0,
                volume=0, market_open=self._is_market_open(),
            )

    # ── Orders ────────────────────────────────────────────────

    def submit_order(self, order: OrderRequest) -> dict:
        """Journal the order. If live-unlocked, also execute on Alpaca."""
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
            "legs": [leg.model_dump() for leg in order.legs] if order.legs else None,
            "required_maintenance_margin": order.required_maintenance_margin,
        }

        if config.is_live_unlocked:
            entry["mode"] = "LIVE"
            # Actually execute on Alpaca
            try:
                if order.order_class == "mleg":
                    alpaca_result = self._submit_mleg_to_alpaca(order)
                else:
                    alpaca_result = self._submit_to_alpaca(order)
                entry["status"] = "filled"
                entry["alpaca_order_id"] = alpaca_result.get("id", "")
                entry["alpaca_status"] = alpaca_result.get("status", "")
                entry["filled_avg_price"] = alpaca_result.get("filled_avg_price", "")
                entry["filled_qty"] = alpaca_result.get("filled_qty", "")
            except Exception as e:
                entry["status"] = "alpaca_error"
                entry["error"] = str(e)
                logger.error(f"Alpaca execution failed: {e}")
        else:
            entry["mode"] = "PAPER"

        self._append_journal(entry)
        logger.info(f"Order journaled: {entry['order_id']} ({entry['mode']})")
        return entry
    
    def _submit_to_alpaca(self, order: OrderRequest) -> dict:
        """Submit order to Alpaca API."""
        import os
        import alpaca_trade_api as tradeapi
        from alpaca.trading.enums import OrderSide, TimeInForce
        
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        
        api = tradeapi.REST(api_key, secret_key, base_url)
        
        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
        
        kwargs = {
            "symbol": order.symbol,
            "side": side,
            "type": order.order_type or "market",
            "time_in_force": TimeInForce.DAY,  # Required for fractional
        }
        
        if order.notional and float(order.notional) > 0:
            kwargs["notional"] = str(round(float(order.notional), 2))
        elif order.qty and float(order.qty) > 0:
            kwargs["qty"] = str(order.qty)
        
        if order.limit_price and float(order.limit_price) > 0:
            kwargs["limit_price"] = str(order.limit_price)
        
        result = api.submit_order(**kwargs)
        
        return {
            "id": str(result.id),
            "status": str(result.status),
            "filled_avg_price": str(result.filled_avg_price or ""),
            "filled_qty": str(result.filled_qty or "0"),
        }

    def _submit_mleg_to_alpaca(self, order: OrderRequest) -> dict:
        """Submit a multi-leg (mleg) order to Alpaca API.

        Alpaca mleg orders use order_class='mleg' with a legs array.
        Each leg has: symbol, qty, side, positionIntent, and optional limit_price.
        """
        import os
        from alpaca.trading.requests import OrderRequest as AlpacaMlegRequest, Leg
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, PositionIntent
        from alpaca.trading.client import TradingClient

        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")

        client = TradingClient(api_key, secret_key, paper=config.alpaca_paper)

        pi_map = {
            "buy_to_open": PositionIntent.BUY_TO_OPEN,
            "buy_to_close": PositionIntent.BUY_TO_CLOSE,
            "sell_to_open": PositionIntent.SELL_TO_OPEN,
            "sell_to_close": PositionIntent.SELL_TO_CLOSE,
        }

        alpaca_legs = []
        for leg in order.legs:
            leg_side = OrderSide.BUY if leg.side == "buy" else OrderSide.SELL
            leg_kwargs = {
                "symbol": leg.symbol,
                "qty": str(leg.qty),
                "side": leg_side,
                "position_intent": pi_map[leg.position_intent],
            }
            if leg.limit_price is not None:
                leg_kwargs["limit_price"] = str(leg.limit_price)
            alpaca_legs.append(Leg(**leg_kwargs))

        tif = TimeInForce.DAY
        if order.limit_price and float(order.limit_price) > 0:
            tif_order = client.submit_order(
                AlpacaMlegRequest(
                    order_class=OrderClass.MLEG,
                    time_in_force=tif,
                    legs=alpaca_legs,
                    limit_price=str(order.limit_price),
                )
            )
        else:
            tif_order = client.submit_order(
                AlpacaMlegRequest(
                    order_class=OrderClass.MLEG,
                    time_in_force=tif,
                    legs=alpaca_legs,
                )
            )

        return {
            "id": str(tif_order.id),
            "status": str(tif_order.status),
            "filled_avg_price": str(tif_order.filled_avg_price or ""),
            "filled_qty": str(tif_order.filled_qty or "0"),
        }


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
            if entry.get("status") == "filled":
                ts = datetime.fromisoformat(entry["timestamp"])
                pnl = entry.get("filled_pnl", 0.0) or 0.0

                if ts.date() == now.date():
                    trades_today += 1
                    daily_pnl += pnl
                    if pnl < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0

                # Week check (Monday=0 start)
                from datetime import timedelta
                week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                week_start = week_start - timedelta(days=week_start.weekday())
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