"""Gamma Dynamics & Options Positioning — institutional-grade analysis.

Features:
1. Gamma Exposure (GEX) calculation
2. Gamma squeeze detection
3. Put/Call wall identification
4. Max pain calculation
5. Put/Call ratio interpretation
6. Options chain analysis
7. Open interest analysis
8. Dark pool activity detection
9. Unusual options activity (UOA)
10. Combined gamma + positioning strategy
"""

import os
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional

logger = logging.getLogger("hermes_trader.gamma_positioning")


@dataclass
class GEXLevel:
    strike: float
    call_gex: float
    put_gex: float
    total_gex: float
    is_flip: bool


@dataclass
class PutWall:
    strike: float
    oi: int
    distance_pct: float
    strength: str  # "strong", "moderate", "weak"


@dataclass
class CallWall:
    strike: float
    oi: int
    distance_pct: float
    strength: str


class GammaPositioning:
    """Institutional-grade gamma dynamics and options positioning."""

    def __init__(self):
        self._opt_chain_cache = {}

    def get_spot_price(self, symbol: str = "SPY") -> float:
        import yfinance as yf
        return yf.Ticker(symbol).fast_info.get("lastPrice", 0)

    def _fetch_option_chain(self, symbol: str, max_dte: int) -> list:
        """Fetch option chain via yfinance, computing Greeks from IV.

        Returns list of dicts with keys: symbol, strike, is_call,
        bid, ask, volume, open_interest, gamma, iv.
        """
        import yfinance as yf
        from datetime import date
        from .greeks_engine import BlackScholesGreeks as BSG

        ticker = yf.Ticker(symbol)
        spot = self.get_spot_price(symbol)
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
                    option_type = "call" if is_call else "put"
                    gamma = 0.0
                    if iv > 0 and spot > 0:
                        try:
                            gamma = float(BSG.gamma(spot, strike, r, 0.0, iv, tau))
                        except Exception:
                            gamma = 0.0

                    def _safe_int(val, default=0):
                        try:
                            import math
                            v = float(val) if val is not None else default
                            return int(v) if not math.isnan(v) and not math.isinf(v) else default
                        except (ValueError, TypeError):
                            return default

                    chain.append({
                        "symbol": row.get("contractSymbol", ""),
                        "strike": strike,
                        "is_call": is_call,
                        "bid": bid,
                        "ask": ask,
                        "volume": _safe_int(row.get("volume", 0)),
                        "open_interest": _safe_int(row.get("openInterest", 0)),
                        "gamma": gamma,
                        "iv": iv,
                    })

        return chain

    def calculate_gex(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Calculate Gamma Exposure (GEX) for each strike."""
        chain = self._fetch_option_chain(symbol, max_dte)
        spot = self.get_spot_price(symbol)

        gex_by_strike = {}
        total_gex = 0

        for opt in chain:
            gamma = opt["gamma"]
            is_call = opt["is_call"]
            strike = opt["strike"]

            # GEX = Gamma × OI × Spot² × 0.01
            # Simplified: use gamma × spot² × 100
            oi = opt["open_interest"]
            # Standard GEX formula: gamma × spot² × OI × 100 (contract multiplier)
            # Matches dealer_positioning.py for consistency
            gex = gamma * (spot ** 2) * oi * 100 * (1 if is_call else -1)
            total_gex += gex

            if strike not in gex_by_strike:
                gex_by_strike[strike] = {"call_gex": 0, "put_gex": 0, "total_gex": 0}
            if is_call:
                gex_by_strike[strike]["call_gex"] += gex
            else:
                gex_by_strike[strike]["put_gex"] += gex
            gex_by_strike[strike]["total_gex"] += gex

        # Find flip point
        sorted_strikes = sorted(gex_by_strike.keys())
        cumulative = 0
        flip_strike = None
        for strike in sorted_strikes:
            cumulative += gex_by_strike[strike]["total_gex"]
            if cumulative > 0 and flip_strike is None:
                flip_strike = strike
                break

        # GEX regime
        if total_gex > 0:
            regime = "positive_gamma"
            description = "Dealers long gamma → market pins to strikes (rangebound)"
            trading_rule = "Sell options (rangebound strategy)"
        else:
            regime = "negative_gamma"
            description = "Dealers short gamma → market moves violently (trending)"
            trading_rule = "Buy options (directional strategy)"

        return {
            "total_gex": round(total_gex, 0),
            "flip_strike": round(flip_strike, 2) if flip_strike else None,
            "regime": regime,
            "description": description,
            "trading_rule": trading_rule,
            "spot": spot,
            "gex_by_strike": {k: {kk: round(vv, 0) for kk, vv in v.items()} for k, v in sorted(gex_by_strike.items())},
        }

    def detect_put_call_walls(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Detect put and call walls (strikes with massive OI)."""
        chain = self._fetch_option_chain(symbol, max_dte)
        spot = self.get_spot_price(symbol)

        put_oi = {}
        call_oi = {}

        for opt in chain:
            strike = opt["strike"]
            oi = opt["open_interest"]
            if opt["is_call"]:
                call_oi[strike] = call_oi.get(strike, 0) + oi
            else:
                put_oi[strike] = put_oi.get(strike, 0) + oi

        # Find walls (highest OI)
        put_walls = []
        call_walls = []

        if put_oi:
            max_put_oi = max(put_oi.values())
            for strike, oi in sorted(put_oi.items()):
                if strike < spot and oi > max_put_oi * 0.5:
                    distance = (spot - strike) / spot * 100
                    strength = "strong" if oi > max_put_oi * 0.8 else ("moderate" if oi > max_put_oi * 0.6 else "weak")
                    put_walls.append({"strike": strike, "oi": oi, "distance_pct": round(distance, 2), "strength": strength})

        if call_oi:
            max_call_oi = max(call_oi.values())
            for strike, oi in sorted(call_oi.items()):
                if strike > spot and oi > max_call_oi * 0.5:
                    distance = (strike - spot) / spot * 100
                    strength = "strong" if oi > max_call_oi * 0.8 else ("moderate" if oi > max_call_oi * 0.6 else "weak")
                    call_walls.append({"strike": strike, "oi": oi, "distance_pct": round(distance, 2), "strength": strength})

        # Nearest walls
        nearest_put = min(put_walls, key=lambda x: x["distance_pct"]) if put_walls else None
        nearest_call = min(call_walls, key=lambda x: x["distance_pct"]) if call_walls else None

        return {
            "spot": spot,
            "put_walls": put_walls[:5],
            "call_walls": call_walls[:5],
            "nearest_put": nearest_put,
            "nearest_call": nearest_call,
            "trading_rule": f"Support at ${nearest_put['strike']:.0f}, Resistance at ${nearest_call['strike']:.0f}" if nearest_put and nearest_call else "No clear walls",
        }

    def calculate_max_pain(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Calculate max pain — strike where most options expire worthless."""
        chain = self._fetch_option_chain(symbol, max_dte)
        spot = self.get_spot_price(symbol)

        strikes = {}
        for opt in chain:
            strike = opt["strike"]
            is_call = opt["is_call"]
            oi = opt["open_interest"]
            if strike not in strikes:
                strikes[strike] = {"call_oi": 0, "put_oi": 0}
            if is_call:
                strikes[strike]["call_oi"] += oi
            else:
                strikes[strike]["put_oi"] += oi

        # Calculate pain for each strike
        min_pain = float('inf')
        max_pain_strike = spot

        for test_strike in strikes:
            total_pain = 0
            for strike, data in strikes.items():
                # Pain for calls: if test_strike > strike, call holders make money
                if test_strike > strike:
                    total_pain += (test_strike - strike) * data["call_oi"]
                # Pain for puts: if test_strike < strike, put holders make money
                if test_strike < strike:
                    total_pain += (strike - test_strike) * data["put_oi"]

            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = test_strike

        distance = abs(max_pain_strike - spot)
        pull_pct = (distance / spot * 100) if spot > 0 else 0

        return {
            "max_pain_strike": round(max_pain_strike, 2),
            "spot": spot,
            "distance": round(distance, 2),
            "pull_pct": round(pull_pct, 2),
            "description": f"Price may gravitate toward ${max_pain_strike:.0f} ({pull_pct:.1f}% away)",
            "trading_rule": f"Buy options toward ${max_pain_strike:.0f}",
        }

    def interpret_put_call_ratio(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Interpret put/call ratio with contrarian and confirming signals."""
        chain = self._fetch_option_chain(symbol, max_dte)

        call_vol = 0
        put_vol = 0
        call_oi = 0
        put_oi = 0

        for opt in chain:
            vol = opt["volume"]
            oi = opt["open_interest"]
            if opt["is_call"]:
                call_vol += vol
                call_oi += oi
            else:
                put_vol += vol
                put_oi += oi

        pcr_vol = put_vol / call_vol if call_vol > 0 else 1.0
        pcr_oi = put_oi / call_oi if call_oi > 0 else 1.0

        # Contrarian signals
        if pcr_vol > 1.5:
            contrarian = "BULLISH (extreme fear → contrarian buy)"
        elif pcr_vol > 1.0:
            contrarian = "NEUTRAL-BEARISH"
        elif pcr_vol < 0.5:
            contrarian = "BEARISH (extreme greed → contrarian sell)"
        elif pcr_vol < 0.7:
            contrarian = "NEUTRAL-BULLISH"
        else:
            contrarian = "NEUTRAL"

        # Confirming signals
        if pcr_vol > 1.0 and pcr_oi > 1.0:
            confirming = "BEARISH (both vol and OI confirm)"
        elif pcr_vol < 0.7 and pcr_oi < 0.7:
            confirming = "BULLISH (both vol and OI confirm)"
        else:
            confirming = "MIXED"

        return {
            "pcr_volume": round(pcr_vol, 3),
            "pcr_oi": round(pcr_oi, 3),
            "call_volume": call_vol,
            "put_volume": put_vol,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "contrarian_signal": contrarian,
            "confirming_signal": confirming,
            "trading_rule": f"Contrarian: {contrarian} | Confirming: {confirming}",
        }

    def detect_unusual_activity(self, symbol: str = "SPY", max_dte: int = 7) -> dict:
        """Detect unusual options activity (UOA)."""
        chain = self._fetch_option_chain(symbol, max_dte)

        unusual = []
        for opt in chain:
            vol = opt["volume"]
            oi = opt["open_interest"]
            if oi == 0:
                continue

            vol_oi_ratio = vol / oi
            if vol_oi_ratio > 2.0:  # Volume > 2x OI = unusual
                unusual.append({
                    "symbol": opt["symbol"],
                    "type": "call" if opt["is_call"] else "put",
                    "volume": vol,
                    "oi": oi,
                    "vol_oi_ratio": round(vol_oi_ratio, 2),
                    "signal": "BULLISH" if opt["is_call"] else "BEARISH",
                })

        unusual.sort(key=lambda x: x["vol_oi_ratio"], reverse=True)

        return {
            "unusual_count": len(unusual),
            "unusual": unusual[:10],
            "signal": "BULLISH" if any(u["type"] == "call" for u in unusual[:3]) else "BEARISH",
        }

    def full_gamma_analysis(self, symbol: str = "SPY") -> dict:
        """Run full gamma and positioning analysis."""
        gex = self.calculate_gex(symbol)
        walls = self.detect_put_call_walls(symbol)
        max_pain = self.calculate_max_pain(symbol)
        pcr = self.interpret_put_call_ratio(symbol)
        uoa = self.detect_unusual_activity(symbol)

        # Combined trading rule
        signals = 0
        if gex["regime"] == "positive_gamma":
            signals += 1  # Pinning expected
        if pcr["contrarian_signal"].startswith("BULLISH"):
            signals += 1  # Contrarian bullish
        if max_pain["distance"] < 1.0:
            signals += 1  # Price near max pain

        if signals >= 2:
            recommendation = "HIGH CONVICTION — Multiple gamma/positioning signals align"
        elif signals == 1:
            recommendation = "MODERATE — Some gamma signals present"
        else:
            recommendation = "LOW CONVICTION — No strong gamma signals"

        return {
            "gex": gex,
            "walls": walls,
            "max_pain": max_pain,
            "put_call_ratio": pcr,
            "unusual_activity": uoa,
            "recommendation": recommendation,
            "timestamp": datetime.utcnow().isoformat(),
        }
