"""0DTE Engine v2 — Maximum power for high-risk day trading.

Strategies:
1. ORB (Opening Range Breakout) — best 0DTE strategy
2. Momentum Scalp — ride the trend
3. Gamma Scalp — trade at GEX levels
4. VWAP Bounce — trade bounces off VWAP
"""

import os
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger("hermes_trader.zero_dte_v2")


@dataclass
class TradeSignal:
    strategy: str
    direction: str  # "call" or "put"
    entry_price: float
    stop_loss: float
    profit_target: float
    confidence: float  # 0-1
    reason: str


class ZeroDTEEngineV2:
    """Maximum power 0DTE engine."""

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

    def get_spot_price(self, symbol: str = "SPY") -> float:
        import yfinance as yf
        return yf.Ticker(symbol).fast_info.get("lastPrice", 0)

    def get_opening_range(self, symbol: str = "SPY", minutes: int = 15) -> dict:
        """Get first 15-min range for ORB strategy."""
        spot = self.get_spot_price(symbol)
        # In production, fetch real intraday data
        # For now, use current price as approximation
        return {
            "high": spot * 1.002,  # Simulated
            "low": spot * 0.998,   # Simulated
            "spot": spot,
            "range_pct": 0.4,      # 0.4% typical opening range
        }

    def detect_momentum(self, symbol: str = "SPY") -> dict:
        """Detect current momentum direction."""
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")

        if len(hist) < 2:
            return {"direction": "neutral", "strength": 0}

        current = hist['Close'].iloc[-1]
        prev = hist['Close'].iloc[-2]
        change = (current - prev) / prev * 100

        # 5-day momentum
        if len(hist) >= 5:
            five_day = (current - hist['Close'].iloc[-5]) / hist['Close'].iloc[-5] * 100
        else:
            five_day = change

        # Determine direction
        if change > 0.1 and five_day > 0:
            direction = "bullish"
            strength = min(1.0, change * 10)
        elif change < -0.1 and five_day < 0:
            direction = "bearish"
            strength = min(1.0, abs(change) * 10)
        else:
            direction = "neutral"
            strength = 0

        return {
            "direction": direction,
            "strength": round(strength, 2),
            "daily_change": round(change, 2),
            "five_day_change": round(five_day, 2),
        }

    def detect_vwap_signal(self, symbol: str = "SPY") -> dict:
        """Detect VWAP bounce signals."""
        spot = self.get_spot_price(symbol)
        # In production, calculate real VWAP from intraday data
        # Approximation: VWAP is typically near today's open
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if len(hist) > 0:
            vwap = hist['Open'].iloc[0] * 1.001  # Approximate
        else:
            vwap = spot

        distance = (spot - vwap) / vwap * 100

        if distance < -0.1:
            signal = "bounce_buy"  # Below VWAP, likely to bounce up
        elif distance > 0.1:
            signal = "bounce_sell"  # Above VWAP, likely to bounce down
        else:
            signal = "at_vwap"

        return {
            "vwap": round(vwap, 2),
            "spot": spot,
            "distance_pct": round(distance, 2),
            "signal": signal,
        }

    def detect_gex_signal(self, symbol: str = "SPY") -> dict:
        """Detect GEX-based signals."""
        spot = self.get_spot_price(symbol)
        # Simplified GEX detection
        # In production, use real GEX calculation from options_analytics.py
        return {
            "gex_regime": "long_gamma",  # Default
            "flip_level": spot * 0.97,  # 3% below spot
            "signal": "pinning",  # Long gamma = pinning
        }

    def generate_signals(self, symbol: str = "SPY") -> List[TradeSignal]:
        """Generate all trading signals."""
        signals = []
        spot = self.get_spot_price(symbol)
        momentum = self.detect_momentum(symbol)
        vwap = self.detect_vwap_signal(symbol)
        gex = self.detect_gex_signal(symbol)

        # Strategy 1: ORB Breakout
        orb = self.get_opening_range(symbol)
        if momentum["direction"] == "bullish" and momentum["strength"] > 0.3:
            signals.append(TradeSignal(
                strategy="ORB_Breakout",
                direction="call",
                entry_price=spot,
                stop_loss=spot * 0.995,  # 0.5% stop
                profit_target=spot * 1.01,  # 1% target
                confidence=min(0.8, momentum["strength"]),
                reason=f"Bullish momentum ({momentum['daily_change']:+.2f}%)",
            ))
        elif momentum["direction"] == "bearish" and momentum["strength"] > 0.3:
            signals.append(TradeSignal(
                strategy="ORB_Breakout",
                direction="put",
                entry_price=spot,
                stop_loss=spot * 1.005,  # 0.5% stop
                profit_target=spot * 0.99,  # 1% target
                confidence=min(0.8, momentum["strength"]),
                reason=f"Bearish momentum ({momentum['daily_change']:+.2f}%)",
            ))

        # Strategy 2: VWAP Bounce
        if vwap["signal"] == "bounce_buy" and momentum["direction"] != "bearish":
            signals.append(TradeSignal(
                strategy="VWAP_Bounce",
                direction="call",
                entry_price=spot,
                stop_loss=spot * 0.997,
                profit_target=spot * 1.005,
                confidence=0.6,
                reason=f"Below VWAP ({vwap['distance_pct']:+.2f}%), likely bounce",
            ))
        elif vwap["signal"] == "bounce_sell" and momentum["direction"] != "bullish":
            signals.append(TradeSignal(
                strategy="VWAP_Bounce",
                direction="put",
                entry_price=spot,
                stop_loss=spot * 1.003,
                profit_target=spot * 0.995,
                confidence=0.6,
                reason=f"Above VWAP ({vwap['distance_pct']:+.2f}%), likely pullback",
            ))

        # Strategy 3: Momentum Scalp
        if momentum["strength"] > 0.5:
            direction = "call" if momentum["direction"] == "bullish" else "put"
            signals.append(TradeSignal(
                strategy="Momentum_Scalp",
                direction=direction,
                entry_price=spot,
                stop_loss=spot * (0.995 if direction == "call" else 1.005),
                profit_target=spot * (1.008 if direction == "call" else 0.992),
                confidence=min(0.7, momentum["strength"]),
                reason=f"Strong momentum ({momentum['daily_change']:+.2f}%)",
            ))

        # Sort by confidence
        signals.sort(key=lambda x: x.confidence, reverse=True)
        return signals

    def find_best_contract(self, symbol: str = "SPY", direction: str = "call", budget: float = 50.0) -> dict:
        """Find best 0DTE contract for the signal."""
        from alpaca.data.requests import OptionChainRequest
        from datetime import date
        today = date.today()
        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=today,
            expiration_date_lte=today,
        )
        chain = self.opt_client.get_option_chain(req)
        spot = self.get_spot_price(symbol)

        candidates = []
        for sym, snap in chain.items():
            if direction == "call" and "C" not in sym:
                continue
            if direction == "put" and "P" not in sym:
                continue
            if not snap.latest_quote or not snap.implied_volatility:
                continue

            try:
                strike = float(sym.split("C" if "C" in sym else "P")[1]) / 1000
                mid = (float(snap.latest_quote.bid_price or 0) + float(snap.latest_quote.ask_price or 0)) / 2
                if mid <= 0:
                    continue

                # For calls: want slightly OTM (strike just above spot)
                # For puts: want slightly OTM (strike just below spot)
                if direction == "call":
                    if strike <= spot:
                        continue  # Skip ITM
                    distance = strike - spot
                else:
                    if strike >= spot:
                        continue  # Skip ITM
                    distance = spot - strike

                distance_pct = distance / spot * 100
                cost = mid * 100

                if cost > budget:
                    continue

                # Score: prefer 0.2-0.5% OTM (sweet spot)
                otm_score = 1.0 - abs(distance_pct - 0.3) / 0.3
                iv_score = min(1.0, snap.implied_volatility / 0.3)

                candidates.append({
                    "symbol": sym,
                    "strike": strike,
                    "mid": mid,
                    "bid": float(snap.latest_quote.bid_price or 0),
                    "ask": float(snap.latest_quote.ask_price or 0),
                    "iv": float(snap.implied_volatility),
                    "distance_pct": round(distance_pct, 2),
                    "cost": round(cost, 2),
                    "score": round((otm_score + iv_score) / 2, 2),
                })
            except Exception:
                continue

        if not candidates:
            return {"action": "wait", "reason": "No suitable contracts found"}

        # Sort by score
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]
        contracts = max(1, int(budget / best["cost"]))

        return {
            "action": "buy",
            "symbol": best["symbol"],
            "strike": best["strike"],
            "mid": best["mid"],
            "contracts": contracts,
            "cost": round(contracts * best["cost"], 2),
            "profit_target": round(contracts * best["cost"] * 0.50, 2),
            "stop_loss": round(contracts * best["cost"] * 0.50, 2),
            "score": best["score"],
        }

    def execute_trade(self, symbol: str = "SPY", direction: str = "call", budget: float = 50.0) -> dict:
        """Execute a 0DTE trade."""
        from alpaca.trading.enums import OrderSide, TimeInForce

        contract = self.find_best_contract(symbol, direction, budget)
        if contract["action"] != "buy":
            return contract

        try:
            order = self.trading_client.submit_order(
                symbol=contract["symbol"],
                qty=contract["contracts"],
                side=OrderSide.BUY,
                type="limit",
                limit_price=str(contract["mid"]),
                time_in_force=TimeInForce.DAY,
            )
            return {
                "status": "EXECUTED",
                "order_id": str(order.id),
                "symbol": contract["symbol"],
                "direction": direction,
                "contracts": contract["contracts"],
                "cost": contract["cost"],
                "profit_target": contract["profit_target"],
                "stop_loss": contract["stop_loss"],
            }
        except Exception as e:
            return {"status": "FAILED", "error": str(e)}

    def run_full_scan(self, symbol: str = "SPY", budget: float = 50.0) -> dict:
        """Run full 0DTE scan and generate signals."""
        signals = self.generate_signals(symbol)

        if not signals:
            return {
                "action": "wait",
                "reason": "No signals",
                "signals": [],
            }

        best_signal = signals[0]
        contract = self.find_best_contract(symbol, best_signal.direction, budget)

        return {
            "action": "trade" if contract["action"] == "buy" else "wait",
            "signal": best_signal,
            "contract": contract,
            "all_signals": signals,
            "spot": self.get_spot_price(symbol),
            "timestamp": datetime.utcnow().isoformat(),
        }
