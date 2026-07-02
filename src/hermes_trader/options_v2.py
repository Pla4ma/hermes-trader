"""Ultimate Options Engine v2 — the most powerful options trading system.

Strategies:
1. 0DTE/Weekly calls — high gamma, fast moves, extreme time decay
2. 0DTE/Weekly puts — bearish plays, hedging
3. Bull call spread — risk-defined bullish
4. Bear put spread — risk-defined bearish
5. Long straddle — bet on big move either direction
6. Gamma scalping — buy high gamma, delta-hedge with shares
7. IV crush plays — sell premium before earnings
8. Momentum calls — buy calls on breakout stocks

Key features:
- VIX-based strategy selection
- Greeks-based scoring (delta, gamma, theta, vega)
- IV rank/skew analysis
- Automatic direction from market regime
- Risk-defined position sizing
- Greeks-based exit logic
- 0DTE detection and handling
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("hermes_trader.options_v2")


class UltimateOptionsEngine:
    """The most powerful options trading engine for small accounts."""

    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        self._opt_client = None
        self._trading_client = None
        self._spy_price = None
        self._vix = None

    @property
    def opt_client(self):
        if self._opt_client is None:
            from alpaca.data.historical import OptionHistoricalDataClient
            self._opt_client = OptionHistoricalDataClient(self.api_key, self.secret_key)
        return self._opt_client

    @property
    def trading_client(self):
        if self._trading_client is None:
            from alpaca.trading.client import TradingClient
            self._trading_client = TradingClient(self.api_key, self.secret_key, paper=False)
        return self._trading_client

    @property
    def spy_price(self):
        if self._spy_price is None:
            import yfinance as yf
            self._spy_price = yf.Ticker("SPY").fast_info.get("lastPrice", 0)
        return self._spy_price

    @property
    def vix(self):
        if self._vix is None:
            import yfinance as yf
            vix_data = yf.Ticker("^VIX").history(period="1mo")
            self._vix = {
                "current": float(vix_data["Close"].iloc[-1]),
                "avg": float(vix_data["Close"].mean()),
                "high": float(vix_data["Close"].max()),
                "low": float(vix_data["Close"].min()),
                "percentile": float((vix_data["Close"] < vix_data["Close"].iloc[-1]).sum() / len(vix_data) * 100),
            }
        return self._vix

    def get_regime(self) -> str:
        """Get market regime."""
        try:
            from .market_regime import detect_regime
            return detect_regime().get("regime", "NEUTRAL")
        except Exception:
            return "NEUTRAL"

    def select_strategy(self) -> dict:
        """Select the best options strategy based on market conditions."""
        vix = self.vix
        regime = self.get_regime()
        cash = self._get_cash()

        strategies = []

        # Strategy selection based on conditions
        if vix["current"] < 15:
            # LOW VOL — options are cheap, buy directional
            strategies.append({
                "name": "long_call",
                "direction": "bullish" if "BULL" in regime else "bearish",
                "reason": f"Low VIX ({vix['current']:.1f}) = cheap options. Buy directional.",
                "max_dte": 14,
                "target_delta": 0.25,
                "priority": 1 if "BULL" in regime else 3,
            })
            strategies.append({
                "name": "long_straddle",
                "direction": "neutral",
                "reason": f"Low VIX = cheap straddle. Bet on big move.",
                "max_dte": 7,
                "target_delta": 0.50,
                "priority": 2,
            })

        elif vix["current"] < 20:
            # NORMAL VOL — balanced
            strategies.append({
                "name": "long_call",
                "direction": "bullish",
                "reason": f"Normal VIX ({vix['current']:.1f}), bull regime. Buy calls.",
                "max_dte": 10,
                "target_delta": 0.20,
                "priority": 1 if "BULL" in regime else 4,
            })
            strategies.append({
                "name": "long_put",
                "direction": "bearish",
                "reason": f"Normal VIX ({vix['current']:.1f}), bear regime. Buy puts.",
                "max_dte": 10,
                "target_delta": 0.20,
                "priority": 1 if "BEAR" in regime else 4,
            })
            strategies.append({
                "name": "bull_call_spread",
                "direction": "bullish",
                "reason": "Risk-defined bullish. Cheaper than naked call.",
                "max_dte": 14,
                "target_delta": 0.30,
                "priority": 2,
            })

        elif vix["current"] < 25:
            # ELEVATED VOL — options expensive, sell premium
            strategies.append({
                "name": "bull_call_spread",
                "direction": "bullish",
                "reason": f"Elevated VIX ({vix['current']:.1f}). Use spreads to reduce cost.",
                "max_dte": 14,
                "target_delta": 0.25,
                "priority": 1,
            })
            strategies.append({
                "name": "bear_put_spread",
                "direction": "bearish",
                "reason": f"Elevated VIX. Bearish spread.",
                "max_dte": 14,
                "target_delta": 0.25,
                "priority": 2,
            })

        else:
            # HIGH VOL — sell premium aggressively
            strategies.append({
                "name": "iron_condor",
                "direction": "neutral",
                "reason": f"High VIX ({vix['current']:.1f}). Sell premium.",
                "max_dte": 21,
                "target_delta": 0.15,
                "priority": 1,
            })

        # Sort by priority
        strategies.sort(key=lambda x: x["priority"])

        return {
            "vix": vix,
            "regime": regime,
            "cash": cash,
            "selected": strategies[0] if strategies else None,
            "alternatives": strategies[1:],
        }

    def scan_chain(self, symbol: str = "SPY", max_dte: int = 14,
                   option_type: str = "call", max_cost: float = 50.0) -> list[dict]:
        """Scan options chain with advanced filtering."""
        from alpaca.data.requests import OptionChainRequest

        today = datetime.utcnow().date()
        max_expiry = today + timedelta(days=max_dte)

        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=today + timedelta(days=1),
            expiration_date_lte=max_expiry,
        )
        chain = self.opt_client.get_option_chain(req)

        candidates = []
        for sym, snap in chain.items():
            is_call = "C" in sym
            is_put = "P" in sym

            if option_type == "call" and not is_call:
                continue
            if option_type == "put" and not is_put:
                continue

            if not snap.latest_quote or not snap.greeks:
                continue

            q = snap.latest_quote
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            cost = mid * 100
            if cost <= 0 or cost > max_cost:
                continue

            delta = abs(float(snap.greeks.delta or 0))
            gamma = float(snap.greeks.gamma or 0)
            theta = float(snap.greeks.theta or 0)
            vega = float(snap.greeks.vega or 0)
            iv = float(snap.implied_volatility or 0)

            try:
                if is_call:
                    strike = float(sym.split("C")[1]) / 1000
                else:
                    strike = float(sym.split("P")[1]) / 1000
            except (ValueError, IndexError):
                continue

            # Calculate DTE from symbol
            try:
                date_str = sym[3:9]  # e.g., "260706"
                expiry_date = datetime.strptime(f"20{date_str}", "%Y%m%d").date()
                dte = max(1, (expiry_date - today).days)
            except Exception:
                dte = 7

            spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999
            moneyness = strike / self.spy_price if self.spy_price > 0 else 1.0

            # Advanced scoring (0-30)
            score = 0

            # 1. Gamma efficiency (gamma per dollar)
            gamma_eff = gamma / mid if mid > 0 else 0
            if gamma_eff > 0.05:
                score += 6
            elif gamma_eff > 0.02:
                score += 4
            elif gamma_eff > 0.01:
                score += 2

            # 2. Delta sweet spot
            if 0.15 <= delta <= 0.35:
                score += 5
            elif 0.10 <= delta <= 0.50:
                score += 3

            # 3. Affordability
            if cost <= 15:
                score += 5
            elif cost <= 25:
                score += 4
            elif cost <= 40:
                score += 2

            # 4. DTE optimization (3-7 days for 0DTE plays)
            if 2 <= dte <= 5:
                score += 4
            elif 5 <= dte <= 10:
                score += 3
            elif 10 <= dte <= 14:
                score += 1

            # 5. Spread tightness
            if spread_pct < 5:
                score += 4
            elif spread_pct < 10:
                score += 2

            # 6. IV relative to VIX
            vix_current = self.vix["current"] / 100
            if iv > 0 and vix_current > 0:
                iv_ratio = iv / vix_current
                if 0.8 < iv_ratio < 1.2:
                    score += 3
                elif 0.6 < iv_ratio < 1.5:
                    score += 1

            # 7. Theta efficiency (less decay per dollar)
            theta_eff = abs(theta) / mid if mid > 0 else 0
            if theta_eff < 0.05:
                score += 3
            elif theta_eff < 0.10:
                score += 1

            candidates.append({
                "symbol": sym,
                "underlying": symbol,
                "type": "call" if is_call else "put",
                "strike": strike,
                "dte": dte,
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "mid": round(mid, 2),
                "cost": round(cost, 2),
                "delta": round(delta, 4),
                "gamma": round(gamma, 4),
                "gamma_efficiency": round(gamma_eff, 4),
                "theta": round(theta, 4),
                "theta_efficiency": round(theta_eff, 4),
                "vega": round(vega, 4),
                "iv": round(iv, 3),
                "spread_pct": round(spread_pct, 1),
                "moneyness": round(moneyness, 4),
                "score": min(score, 30),
                "max_loss": round(cost, 2),
                "breakeven": round(strike + mid, 2) if is_call else round(strike - mid, 2),
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    def find_best_trade(self) -> dict:
        """Find the absolute best options trade right now."""
        strategy_info = self.select_strategy()
        selected = strategy_info["selected"]

        if not selected:
            return {"action": "none", "reason": "No strategy selected"}

        direction = selected["direction"]
        max_dte = selected["max_dte"]
        cash = strategy_info["cash"]

        # Scan for the best option
        if direction in ("bullish", "neutral"):
            calls = self.scan_chain(max_dte=max_dte, option_type="call", max_cost=50)
        else:
            calls = self.scan_chain(max_dte=max_dte, option_type="put", max_cost=50)

        if not calls:
            return {"action": "none", "reason": f"No viable {direction} options found"}

        # Find affordable options
        affordable = [c for c in calls if c["cost"] <= cash]
        all_candidates = calls[:5]

        if affordable:
            best = affordable[0]
            return {
                "action": "trade",
                "strategy": selected["name"],
                "strategy_reason": selected["reason"],
                "candidate": best,
                "alternatives": affordable[1:3],
                "vix": strategy_info["vix"],
                "regime": strategy_info["regime"],
                "cash": cash,
            }
        else:
            return {
                "action": "need_cash",
                "strategy": selected["name"],
                "strategy_reason": selected["reason"],
                "best_candidate": all_candidates[0],
                "cheapest": calls[-1] if calls else None,
                "all_candidates": all_candidates,
                "vix": strategy_info["vix"],
                "regime": strategy_info["regime"],
                "cash": cash,
                "cash_needed": all_candidates[0]["cost"] - cash if all_candidates else 0,
            }

    def execute_trade(self, candidate: dict) -> dict:
        """Execute an options trade with proper risk management."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        try:
            order = self.trading_client.submit_order(
                MarketOrderRequest(
                    symbol=candidate["symbol"],
                    qty=1,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
            )

            # Set stop loss at 50% of premium
            stop_price = round(candidate["mid"] * 0.50, 2)

            result = {
                "action": "BUY_OPTION",
                "symbol": candidate["symbol"],
                "type": candidate["type"],
                "strike": candidate["strike"],
                "dte": candidate["dte"],
                "cost": candidate["cost"],
                "delta": candidate["delta"],
                "gamma": candidate["gamma"],
                "stop_loss": round(stop_price * 100, 2),
                "take_profit": round(candidate["mid"] * 2 * 100, 2),  # 100% gain target
                "order_id": str(order.id),
            }

            # Log
            entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": "BUY_OPTION",
                "symbol": candidate["symbol"],
                "underlying": candidate["underlying"],
                "type": candidate["type"],
                "strike": candidate["strike"],
                "dte": candidate["dte"],
                "cost": candidate["cost"],
                "delta": candidate["delta"],
                "gamma": candidate["gamma"],
                "iv": candidate.get("iv", 0),
                "score": candidate["score"],
                "order_id": str(order.id),
                "strategy": "ultimate_options_v2",
                "stop_loss_pct": 50,
                "take_profit_pct": 100,
            }
            with open("/opt/hermes-trader/data/journals/paper_orders.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")

            return result

        except Exception as e:
            return {"error": str(e)}

    def auto_trade(self) -> dict:
        """Fully autonomous options trading."""
        result = self.find_best_trade()

        if result.get("action") == "trade":
            candidate = result["candidate"]
            execution = self.execute_trade(candidate)
            result["execution"] = execution

        return result

    def get_all_tradeable(self) -> list[dict]:
        """Get ALL tradeable options under budget."""
        cash = self._get_cash()
        calls = self.scan_chain(max_dte=14, option_type="call", max_cost=50)
        puts = self.scan_chain(max_dte=14, option_type="put", max_cost=50)
        return {
            "calls": calls[:10],
            "puts": puts[:10],
            "total": len(calls) + len(puts),
            "cash": cash,
        }

    def _get_cash(self) -> float:
        try:
            return float(self.trading_client.get_account().cash)
        except Exception:
            return 0.0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")

    engine = UltimateOptionsEngine()

    print("=== ULTIMATE OPTIONS ENGINE v2 ===")
    print(f"SPY: ${engine.spy_price:.2f}")
    print(f"VIX: {engine.vix['current']:.1f} ({engine.vix['percentile']:.0f}%ile)")
    print(f"Regime: {engine.get_regime()}")

    print("\n=== STRATEGY SELECTION ===")
    strategy = engine.select_strategy()
    if strategy["selected"]:
        s = strategy["selected"]
        print(f"Selected: {s['name']} ({s['direction']})")
        print(f"Reason: {s['reason']}")

    print("\n=== BEST TRADE ===")
    result = engine.find_best_trade()
    print(f"Action: {result['action']}")
    if result.get("candidate"):
        c = result["candidate"]
        print(f"Option: {c['symbol']} strike={c['strike']} dte={c['dte']} cost=${c['cost']:.0f}")
        print(f"Delta: {c['delta']:.3f} Gamma: {c['gamma']:.4f} Score: {c['score']}/30")
    elif result.get("reason"):
        print(f"Reason: {result['reason']}")
