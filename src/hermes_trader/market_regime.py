"""Market regime detector — classifies market conditions.

Detects 4 regimes: BULL_LOW_VOL, BULL_HIGH_VOL, BEAR_LOW_VOL, BEAR_HIGH_VOL
Used to adjust position sizing, stop distances, and strategy selection.
"""

import json
import os
from datetime import datetime


def detect_regime(symbol: str = "SPY") -> dict:
    """Detect current market regime using multiple indicators."""
    try:
        import yfinance as yf
        import numpy as np

        data = yf.Ticker(symbol).history(period="3mo")
        if len(data) < 21:
            return {"regime": "UNKNOWN", "reason": "Insufficient data"}

        close = data["Close"]
        high = data["High"]
        low = data["Low"]
        vol = data["Volume"]

        # Trend indicators
        ma20 = close.rolling(20).mean().iloc[-1]
        ma50 = close.rolling(min(50, len(close))).mean().iloc[-1]
        price = close.iloc[-1]

        # Momentum
        ret5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100
        ret20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100

        # Volatility (ATR-based)
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean().iloc[-1]
        atr_pct = (atr / price) * 100

        # VIX equivalent (using ATR as proxy)
        vol_avg = vol.rolling(20).mean().iloc[-1]
        vol_ratio = vol.iloc[-1] / vol_avg if vol_avg > 0 else 1

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = (100 - (100 / (1 + gain / loss))).iloc[-1]

        # Regime classification
        trend_bull = price > ma20 and ma20 > ma50
        trend_bear = price < ma20 and ma20 < ma50
        high_vol = atr_pct > 2.0  # ATR > 2% of price
        momentum_strong = ret5 > 2 or ret20 > 5
        momentum_weak = ret5 < -2 or ret20 < -5

        if trend_bull and not high_vol:
            regime = "BULL_LOW_VOL"
            aggression = "HIGH"
            sizing_mult = 1.0
        elif trend_bull and high_vol:
            regime = "BULL_HIGH_VOL"
            aggression = "MEDIUM"
            sizing_mult = 0.75
        elif trend_bear and not high_vol:
            regime = "BEAR_LOW_VOL"
            aggression = "LOW"
            sizing_mult = 0.5
        elif trend_bear and high_vol:
            regime = "BEAR_HIGH_VOL"
            aggression = "MINIMAL"
            sizing_mult = 0.25
        else:
            regime = "NEUTRAL"
            aggression = "MEDIUM"
            sizing_mult = 0.75

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "regime": regime,
            "aggression": aggression,
            "sizing_multiplier": sizing_mult,
            "indicators": {
                "price": round(price, 2),
                "ma20": round(ma20, 2),
                "ma50": round(ma50, 2),
                "rsi": round(rsi, 1),
                "atr_pct": round(atr_pct, 2),
                "ret5": round(ret5, 2),
                "ret20": round(ret20, 2),
                "vol_ratio": round(vol_ratio, 2),
            },
            "signals": {
                "trend_bull": trend_bull,
                "trend_bear": trend_bear,
                "high_vol": high_vol,
                "momentum_strong": momentum_strong,
                "momentum_weak": momentum_weak,
            },
            "recommendation": _get_recommendation(regime, rsi, ret5),
        }

    except Exception as e:
        return {"regime": "ERROR", "error": str(e), "timestamp": datetime.utcnow().isoformat()}


def _get_recommendation(regime: str, rsi: float, ret5: float) -> str:
    """Get trading recommendation based on regime."""
    if regime == "BULL_LOW_VOL":
        if rsi < 40:
            return "STRONG BUY — Bull trend, oversold RSI, low vol. Full aggression."
        elif rsi > 70:
            return "CAUTION — Bull trend but overbought RSI. Reduce size."
        return "BUY — Bull trend, normal conditions. Standard aggression."
    elif regime == "BULL_HIGH_VOL":
        return "SELECTIVE BUY — Bull trend but elevated vol. Tighter stops, smaller positions."
    elif regime == "BEAR_LOW_VOL":
        if rsi < 30:
            return "SELECTIVE BUY — Bear trend but deeply oversold. Contrarian play."
        return "HOLD/AVOID — Bear trend. Reduce new positions. Tighten stops."
    elif regime == "BEAR_HIGH_VOL":
        return "DEFEND — Bear trend, high vol. Close risky positions. Minimal new trades."
    return "NEUTRAL — Mixed signals. Standard caution."


if __name__ == "__main__":
    result = detect_regime()
    print(json.dumps(result, indent=2))
