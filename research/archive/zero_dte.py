"""0DTE Options Engine — high-risk, high-reward trading.

Strategy: Buy 0DTE SPY/QQQ options based on momentum signals.
Target: 50-100% profit per trade, cut at 50% loss.
"""

import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("hermes_trader.zero_dte")


class ZeroDTEEngine:
    """0DTE options trading engine for max profit."""

    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        self._trading_client = None
        self._opt_client = None

    @property
    def trading_client(self):
        if self._trading_client is None:
            from alpaca.trading.client import TradingClient
            self._trading_client = TradingClient(self.api_key, self.secret_key)
        return self._trading_client

    @property
    def opt_client(self):
        if self._opt_client is None:
            from alpaca.data.historical import OptionHistoricalDataClient
            self._opt_client = OptionHistoricalDataClient(self.api_key, self.secret_key)
        return self._opt_client

    def scan_0dte_options(self, symbol: str = "SPY", option_type: str = "call") -> list:
        """Scan for 0DTE options."""
        from alpaca.data.requests import OptionChainRequest
        from datetime import date
        today = date.today()
        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=today,
            expiration_date_lte=today,
        )
        chain = self.opt_client.get_option_chain(req)
        spot = self._get_spot_price(symbol)

        options = []
        for sym, snap in chain.items():
            if option_type == "call" and "C" not in sym:
                continue
            if option_type == "put" and "P" not in sym:
                continue
            if not snap.latest_quote or not snap.implied_volatility:
                continue

            try:
                strike = float(sym.split("C" if "C" in sym else "P")[1]) / 1000
                mid = (float(snap.latest_quote.bid_price or 0) + float(snap.latest_quote.ask_price or 0)) / 2
                if mid <= 0:
                    continue
                options.append({
                    "symbol": sym,
                    "strike": strike,
                    "mid": mid,
                    "bid": float(snap.latest_quote.bid_price or 0),
                    "ask": float(snap.latest_quote.ask_price or 0),
                    "iv": float(snap.implied_volatility),
                    "spot": spot,
                    "distance_pct": abs(strike - spot) / spot * 100,
                })
            except Exception:
                continue

        return sorted(options, key=lambda x: x["mid"])

    def _get_spot_price(self, symbol: str) -> float:
        import yfinance as yf
        return yf.Ticker(symbol).fast_info.get("lastPrice", 0)

    def find_best_0dte_trade(self, symbol: str = "SPY", direction: str = "call", budget: float = 50.0) -> dict:
        """Find best 0DTE trade within budget."""
        options = self.scan_0dte_options(symbol, direction)
        if not options:
            return {"action": "wait", "reason": "No 0DTE options found"}

        affordable = [o for o in options if o["mid"] * 100 <= budget]
        if not affordable:
            return {"action": "wait", "reason": f"No options under ${budget:.0f}"}

        # Best: slightly OTM, affordable, good IV
        best = None
        for o in affordable:
            if o["distance_pct"] < 1.0:  # Within 1% of spot
                if best is None or o["mid"] < best["mid"]:
                    best = o

        if best is None:
            best = affordable[0]

        contracts = max(1, int(budget / (best["mid"] * 100)))
        cost = contracts * best["mid"] * 100

        return {
            "action": "buy",
            "symbol": best["symbol"],
            "type": direction,
            "strike": best["strike"],
            "mid": best["mid"],
            "contracts": contracts,
            "cost": round(cost, 2),
            "budget": budget,
            "profit_target": round(cost * 0.50, 2),  # 50% profit
            "stop_loss": round(cost * 0.50, 2),  # 50% loss
        }

    def execute_0dte_trade(self, symbol: str = "SPY", direction: str = "call", budget: float = 50.0) -> dict:
        """Execute a 0DTE trade."""
        from alpaca.trading.enums import OrderSide, TimeInForce

        trade = self.find_best_0dte_trade(symbol, direction, budget)
        if trade["action"] != "buy":
            return trade

        try:
            order = self.trading_client.submit_order(
                symbol=trade["symbol"],
                qty=trade["contracts"],
                side=OrderSide.BUY,
                type="limit",
                limit_price=str(trade["mid"]),
                time_in_force=TimeInForce.DAY,
            )
            return {
                "status": "EXECUTED",
                "order_id": str(order.id),
                "symbol": trade["symbol"],
                "type": direction,
                "contracts": trade["contracts"],
                "cost": trade["cost"],
                "profit_target": trade["profit_target"],
                "stop_loss": trade["stop_loss"],
            }
        except Exception as e:
            return {"status": "FAILED", "error": str(e)}
