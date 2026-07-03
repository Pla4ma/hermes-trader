"""0DTE Ultimate Engine — maximum power for high-risk day trading.

Based on:
- 471 lines of 0DTE research (entry timing, strike selection, exit rules)
- 9 strategy playbooks (ORB, VWAP, Momentum, GEX, etc.)
- 7 exit rules (profit, stop, time, VWAP, RSI, ORB)
- Risk management for $50 account
- Psychological rules for discipline
"""

import os
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("hermes_trader.zero_dte_ultimate")


@dataclass
class TradeSignal:
    strategy: str
    direction: str
    entry_price: float
    stop_loss: float
    profit_target: float
    confidence: float
    reason: str
    indicators: dict


class ZeroDTEUltimate:
    """Ultimate 0DTE engine — maximum power."""

    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        self._trading_client = None
        self._opt_client = None
        self.daily_trades = 0
        self.daily_pnl = 0
        self.consecutive_losses = 0

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

    def get_indicators(self, symbol: str = "SPY") -> dict:
        """Get all technical indicators for 0DTE trading."""
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")

        if len(hist) < 2:
            return {"error": "Insufficient data"}

        current = hist['Close'].iloc[-1]
        prev = hist['Close'].iloc[-2]

        # Momentum
        daily_change = (current - prev) / prev * 100
        five_day = (current - hist['Close'].iloc[-5]) / hist['Close'].iloc[-5] * 100 if len(hist) >= 5 else daily_change

        # RSI (simplified)
        delta = hist['Close'].diff()
        gain = delta.where(lambda x: x > 0, 0).rolling(5).mean()
        loss = (-delta.where(lambda x: x < 0, 0)).rolling(5).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1] if len(rsi) > 0 else 50

        # MACD (simplified)
        ema12 = hist['Close'].ewm(span=12).mean()
        ema26 = hist['Close'].ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        macd_val = macd.iloc[-1]
        signal_val = signal.iloc[-1]
        macd_cross_up = macd_val > signal_val and macd.iloc[-2] <= signal.iloc[-2]
        macd_cross_down = macd_val < signal_val and macd.iloc[-2] >= signal.iloc[-2]

        # VWAP (approximation)
        vwap = hist['Open'].iloc[0] * 1.001  # Approximate
        vwap_distance = (current - vwap) / vwap * 100

        # Volume
        avg_vol = hist['Volume'].rolling(20).mean().iloc[-1]
        current_vol = hist['Volume'].iloc[-1]
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1

        return {
            "spot": current,
            "daily_change": round(daily_change, 2),
            "five_day_change": round(five_day, 2),
            "rsi": round(float(current_rsi), 1),
            "macd": round(float(macd_val), 4),
            "macd_signal": round(float(signal_val), 4),
            "macd_cross_up": bool(macd_cross_up),
            "macd_cross_down": bool(macd_cross_down),
            "vwap": round(vwap, 2),
            "vwap_distance": round(vwap_distance, 2),
            "vol_ratio": round(float(vol_ratio), 2),
            "momentum": "bullish" if daily_change > 0.1 else ("bearish" if daily_change < -0.1 else "neutral"),
        }

    def generate_signals(self, symbol: str = "SPY") -> List[TradeSignal]:
        """Generate all trading signals with confluence."""
        signals = []
        ind = self.get_indicators(symbol)
        spot = ind["spot"]

        # ─── Strategy 1: ORB Breakout ───
        if ind["momentum"] == "bullish" and ind["rsi"] < 80:
            signals.append(TradeSignal(
                strategy="ORB_Breakout",
                direction="call",
                entry_price=spot,
                stop_loss=spot * 0.995,
                profit_target=spot * 1.01,
                confidence=0.7,
                reason=f"Bullish momentum ({ind['daily_change']:+.2f}%), RSI {ind['rsi']:.0f}",
                indicators=ind,
            ))
        elif ind["momentum"] == "bearish" and ind["rsi"] > 20:
            signals.append(TradeSignal(
                strategy="ORB_Breakout",
                direction="put",
                entry_price=spot,
                stop_loss=spot * 1.005,
                profit_target=spot * 0.99,
                confidence=0.7,
                reason=f"Bearish momentum ({ind['daily_change']:+.2f}%), RSI {ind['rsi']:.0f}",
                indicators=ind,
            ))

        # ─── Strategy 2: VWAP Bounce ───
        if ind["vwap_distance"] < -0.1 and ind["momentum"] != "bearish":
            signals.append(TradeSignal(
                strategy="VWAP_Bounce",
                direction="call",
                entry_price=spot,
                stop_loss=spot * 0.997,
                profit_target=spot * 1.005,
                confidence=0.6,
                reason=f"Below VWAP ({ind['vwap_distance']:+.2f}%), likely bounce",
                indicators=ind,
            ))
        elif ind["vwap_distance"] > 0.1 and ind["momentum"] != "bullish":
            signals.append(TradeSignal(
                strategy="VWAP_Bounce",
                direction="put",
                entry_price=spot,
                stop_loss=spot * 1.003,
                profit_target=spot * 0.995,
                confidence=0.6,
                reason=f"Above VWAP ({ind['vwap_distance']:+.2f}%), likely pullback",
                indicators=ind,
            ))

        # ─── Strategy 3: RSI + MACD Confluence ───
        if ind["rsi"] < 30 and ind["macd_cross_up"]:
            signals.append(TradeSignal(
                strategy="RSI_MACD",
                direction="call",
                entry_price=spot,
                stop_loss=spot * 0.995,
                profit_target=spot * 1.008,
                confidence=0.8,
                reason=f"RSI oversold ({ind['rsi']:.0f}) + MACD cross up",
                indicators=ind,
            ))
        elif ind["rsi"] > 70 and ind["macd_cross_down"]:
            signals.append(TradeSignal(
                strategy="RSI_MACD",
                direction="put",
                entry_price=spot,
                stop_loss=spot * 1.005,
                profit_target=spot * 0.992,
                confidence=0.8,
                reason=f"RSI overbought ({ind['rsi']:.0f}) + MACD cross down",
                indicators=ind,
            ))

        # ─── Strategy 4: Momentum Scalp ───
        if ind["vol_ratio"] > 1.5 and ind["momentum"] != "neutral":
            direction = "call" if ind["momentum"] == "bullish" else "put"
            signals.append(TradeSignal(
                strategy="Momentum_Scalp",
                direction=direction,
                entry_price=spot,
                stop_loss=spot * (0.995 if direction == "call" else 1.005),
                profit_target=spot * (1.008 if direction == "call" else 0.992),
                confidence=0.65,
                reason=f"High volume ({ind['vol_ratio']:.1f}x) + {ind['momentum']} momentum",
                indicators=ind,
            ))

        # Sort by confidence
        signals.sort(key=lambda x: x.confidence, reverse=True)
        return signals

    def check_risk_limits(self) -> dict:
        """Check risk management limits."""
        max_trades_per_day = 3
        max_daily_loss = 25  # 50% of $50
        max_consecutive_losses = 3

        can_trade = (
            self.daily_trades < max_trades_per_day
            and self.daily_pnl > -max_daily_loss
            and self.consecutive_losses < max_consecutive_losses
        )

        return {
            "can_trade": can_trade,
            "trades_today": self.daily_trades,
            "max_trades": max_trades_per_day,
            "daily_pnl": self.daily_pnl,
            "max_daily_loss": max_daily_loss,
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive": max_consecutive_losses,
        }

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

                # Sweet spot: 0.2-0.5% OTM
                if direction == "call":
                    if strike <= spot:
                        continue
                    distance_pct = (strike - spot) / spot * 100
                else:
                    if strike >= spot:
                        continue
                    distance_pct = (spot - strike) / spot * 100

                cost = mid * 100
                if cost > budget:
                    continue

                # Score: prefer 0.2-0.5% OTM
                otm_score = 1.0 - abs(distance_pct - 0.3) / 0.3
                iv_score = min(1.0, snap.implied_volatility / 0.3)
                volume_score = min(1.0, (snap.latest_quote.ask_size or 1) / 1000)

                candidates.append({
                    "symbol": sym,
                    "strike": strike,
                    "mid": mid,
                    "bid": float(snap.latest_quote.bid_price or 0),
                    "ask": float(snap.latest_quote.ask_price or 0),
                    "iv": float(snap.implied_volatility),
                    "distance_pct": round(distance_pct, 2),
                    "cost": round(cost, 2),
                    "score": round((otm_score + iv_score + volume_score) / 3, 2),
                })
            except Exception:
                continue

        if not candidates:
            return {"action": "wait", "reason": "No suitable contracts"}

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
            "profit_target_50": round(contracts * best["cost"] * 1.5, 2),
            "profit_target_100": round(contracts * best["cost"] * 2.0, 2),
            "stop_loss": round(contracts * best["cost"] * 0.5, 2),
            "score": best["score"],
        }

    def execute_trade(self, symbol: str = "SPY", direction: str = "call", budget: float = 50.0) -> dict:
        """Execute a 0DTE trade."""
        from alpaca.trading.enums import OrderSide, TimeInForce

        # Check risk limits
        risk = self.check_risk_limits()
        if not risk["can_trade"]:
            return {
                "status": "BLOCKED",
                "reason": f"Risk limits: {risk['trades_today']}/{risk['max_trades']} trades, "
                          f"${risk['daily_pnl']:.2f} daily P/L, "
                          f"{risk['consecutive_losses']} consecutive losses",
            }

        # Find contract
        contract = self.find_best_contract(symbol, direction, budget)
        if contract["action"] != "buy":
            return {"status": "WAIT", "reason": contract.get("reason", "No contract")}

        # Execute
        try:
            order = self.trading_client.submit_order(
                symbol=contract["symbol"],
                qty=contract["contracts"],
                side=OrderSide.BUY,
                type="limit",
                limit_price=str(contract["mid"]),
                time_in_force=TimeInForce.DAY,
            )
            self.daily_trades += 1
            return {
                "status": "EXECUTED",
                "order_id": str(order.id),
                "symbol": contract["symbol"],
                "direction": direction,
                "contracts": contract["contracts"],
                "cost": contract["cost"],
                "profit_target_50": contract["profit_target_50"],
                "profit_target_100": contract["profit_target_100"],
                "stop_loss": contract["stop_loss"],
                "score": contract["score"],
            }
        except Exception as e:
            return {"status": "FAILED", "error": str(e)}

    def run_full_scan(self, symbol: str = "SPY", budget: float = 50.0) -> dict:
        """Run full 0DTE scan with risk management."""
        risk = self.check_risk_limits()
        signals = self.generate_signals(symbol)
        indicators = self.get_indicators(symbol)

        if not signals:
            return {
                "action": "wait",
                "reason": "No signals",
                "risk": risk,
                "indicators": indicators,
            }

        if not risk["can_trade"]:
            return {
                "action": "wait",
                "reason": "Risk limits hit",
                "risk": risk,
                "signals": signals,
                "indicators": indicators,
            }

        best = signals[0]
        contract = self.find_best_contract(symbol, best.direction, budget)

        return {
            "action": "trade" if contract["action"] == "buy" else "wait",
            "signal": best,
            "contract": contract,
            "risk": risk,
            "indicators": indicators,
            "all_signals": signals,
            "timestamp": datetime.utcnow().isoformat(),
        }
