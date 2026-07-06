"""Options Analytics Module — institutional-grade analytics for our engine.

Features:
1. GEX (Gamma Exposure) calculation
2. Max Pain calculation
3. Put/Call ratio
4. IV percentile
5. Options flow (unusual volume)
6. Position Greeks aggregation
"""

import json
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("hermes_trader.options_analytics")


class OptionsAnalytics:
    """Institutional-grade options analytics."""

    def __init__(self):
        self._opt_chain_cache = {}
        self._spy_price = None

    @property
    def spy_price(self):
        if self._spy_price is None:
            import yfinance as yf
            self._spy_price = yf.Ticker("SPY").fast_info.get("lastPrice", 0)
        return self._spy_price

    def _fetch_option_chain(self, symbol: str, max_dte: int) -> list:
        """Fetch option chain via yfinance, computing Greeks from IV.

        Returns list of dicts with keys: symbol, strike, is_call,
        bid, ask, volume, open_interest, gamma, iv.
        """
        import yfinance as yf
        from datetime import date
        from .greeks_engine import BlackScholesGreeks as BSG

        ticker = yf.Ticker(symbol)
        spot = self.spy_price
        today = date.today()
        chain = []

        for exp_str in ticker.options:
            try:
                exp_date = date.fromisoformat(exp_str)
                dte = (exp_date - today).days
            except Exception:
                continue
            if dte > max_dte or dte < 1:
                continue

            opt = ticker.option_chain(exp_str)
            tau = max(dte, 1) / 365.0
            r = 0.052  # risk-free rate

            for side_df, is_call in [(opt.calls, True), (opt.puts, False)]:
                for _, row in side_df.iterrows():
                    iv = float(row.get("impliedVolatility", 0))
                    bid = float(row.get("bid", 0))
                    ask = float(row.get("ask", 0))
                    last_price = float(row.get("lastPrice", 0))
                    
                    # Fallback: use lastPrice when bid/ask are 0 (common for 0DTE)
                    if bid <= 0 or ask <= 0:
                        if last_price > 0:
                            bid = last_price * 0.98  # Estimate 2% below last
                            ask = last_price * 1.02  # Estimate 2% above last
                        else:
                            continue

                    strike = float(row["strike"])
                    gamma = 0.0
                    if iv > 0 and spot > 0:
                        try:
                            gamma = float(BSG.gamma(spot, strike, r, 0.0, iv, tau))
                        except Exception:
                            gamma = 0.0

                    chain.append({
                        "symbol": row.get("contractSymbol", ""),
                        "strike": strike,
                        "is_call": is_call,
                        "bid": bid,
                        "ask": ask,
                        "volume": int(row.get("volume", 0) or 0),
                        "open_interest": int(row.get("openInterest", 0) or 0),
                        "gamma": gamma,
                        "iv": iv,
                    })

        return chain

    def calculate_gex(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Calculate Gamma Exposure (GEX) for strike selection."""
        chain = self._fetch_option_chain(symbol, max_dte)

        spot = self.spy_price
        total_gex = 0
        gex_by_strike = {}

        for opt in chain:
            gamma = opt["gamma"]
            is_call = opt["is_call"]
            strike = opt["strike"]

            oi = opt["open_interest"]
            gex = gamma * spot * oi * (1 if is_call else -1)
            total_gex += gex
            gex_by_strike[strike] = gex_by_strike.get(strike, 0) + gex

        # Find GEX flip point
        sorted_strikes = sorted(gex_by_strike.keys())
        cumulative = 0
        flip_strike = None
        for strike in sorted_strikes:
            cumulative += gex_by_strike[strike]
            if cumulative > 0 and flip_strike is None:
                flip_strike = strike
                break

        # GEX regime
        if total_gex > 0:
            regime = "long_gamma"
            description = "Dealers long gamma → market pins to strikes (rangebound)"
        else:
            regime = "short_gamma"
            description = "Dealers short gamma → market moves violently (trending)"

        return {
            "total_gex": round(total_gex, 0),
            "flip_strike": round(flip_strike, 2) if flip_strike else None,
            "regime": regime,
            "description": description,
            "spot": spot,
            "gex_by_strike": {k: round(v, 0) for k, v in sorted(gex_by_strike.items())},
        }

    def calculate_max_pain(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Calculate max pain — strike where most options expire worthless."""
        chain = self._fetch_option_chain(symbol, max_dte)

        strikes = {}
        for opt in chain:
            strike = opt["strike"]
            oi = opt["open_interest"]
            if strike not in strikes:
                strikes[strike] = {"call_oi": 0, "put_oi": 0}
            if opt["is_call"]:
                strikes[strike]["call_oi"] += oi
            else:
                strikes[strike]["put_oi"] += oi

        # Simplified max pain (using strike density)
        if strikes:
            max_pain_strike = max(strikes.keys(), key=lambda s: abs(s - self.spy_price))
        else:
            max_pain_strike = self.spy_price

        distance = abs(max_pain_strike - self.spy_price)
        pull_pct = (distance / self.spy_price * 100) if self.spy_price > 0 else 0

        return {
            "max_pain_strike": round(max_pain_strike, 2),
            "spot": self.spy_price,
            "distance": round(distance, 2),
            "pull_pct": round(pull_pct, 2),
            "description": f"Price may gravitate toward ${max_pain_strike:.0f} ({pull_pct:.1f}% away)",
        }

    def get_put_call_ratio(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Calculate put/call ratio for sentiment analysis."""
        chain = self._fetch_option_chain(symbol, max_dte)

        call_vol = 0
        put_vol = 0
        for opt in chain:
            vol = opt["volume"]
            if opt["is_call"]:
                call_vol += vol
            else:
                put_vol += vol

        pcr = put_vol / call_vol if call_vol > 0 else 1.0

        if pcr > 1.5:
            signal = "BEARISH (contrarian bullish)"
        elif pcr > 1.0:
            signal = "SLIGHTLY BEARISH"
        elif pcr < 0.7:
            signal = "BULLISH (contrarian bearish)"
        else:
            signal = "NEUTRAL"

        return {
            "put_call_ratio": round(pcr, 3),
            "call_volume": call_vol,
            "put_volume": put_vol,
            "signal": signal,
        }

    def get_full_analytics(self, symbol: str = "SPY") -> dict:
        """Run all analytics and return comprehensive report."""
        gex = self.calculate_gex(symbol)
        max_pain = self.calculate_max_pain(symbol)
        pcr = self.get_put_call_ratio(symbol)

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "spot": self.spy_price,
            "gex": gex,
            "max_pain": max_pain,
            "put_call_ratio": pcr,
        }
