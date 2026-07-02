"""Technical analysis scanner for generating trading signals based on price indicators.

Computes simple moving averages, exponential moving averages, RSI, and MACD
to generate a bullish/bearish/neutral signal and a score (0-15).
"""

import logging
from typing import Dict, Tuple

import pandas as pd
import yfinance as yf

logger = logging.getLogger("hermes_trader.research.technical_scan")


class TechnicalScanner:
    """Technical analysis scanner for generating trading signals."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def scan(self, symbol: str) -> Tuple[int, dict]:
        """Scan a symbol for technical signals.
        
        Returns:
            Tuple of (score_0_to_15, details_dict)
        """
        try:
            # Download 3 months of daily data
            data = yf.download(symbol, period="3mo", interval="1d")
            if data.empty or len(data) < 20:
                self.logger.warning(f"Insufficient data for {symbol}")
                return 0, {"error": "insufficient_data"}

            # yfinance returns multi-index columns when downloading a single ticker
            # Extract the Close series for the given ticker
            close = data['Close'][symbol]
            # Ensure we have a Series
            if isinstance(close, pd.DataFrame):
                # If still DataFrame (shouldn't happen), take first column
                close = close.iloc[:, 0]

            # Calculate indicators
            sma_20 = close.rolling(window=20).mean()
            sma_50 = close.rolling(window=50).mean()
            ema_12 = close.ewm(span=12, adjust=False).mean()
            ema_26 = close.ewm(span=26, adjust=False).mean()
            
            # MACD
            macd = ema_12 - ema_26
            signal_line = macd.ewm(span=9, adjust=False).mean()
            macd_histogram = macd - signal_line
            
            # RSI (14)
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = (-delta.where(delta < 0, 0))
            # Avoid division by zero
            rs = gain / loss.replace(0, 1e-10)
            rsi = 100 - (100 / (1 + rs))
            
            # Latest values - extract as Python scalars
            latest_close = float(close.iloc[-1])
            latest_sma20 = float(sma_20.iloc[-1])
            latest_sma50 = float(sma_50.iloc[-1])
            latest_macd = float(macd.iloc[-1])
            latest_signal = float(signal_line.iloc[-1])
            latest_hist = float(macd_histogram.iloc[-1])
            latest_rsi = float(rsi.iloc[-1])
            
            score = 0
            details = {
                "close": round(latest_close, 2),
                "sma20": round(latest_sma20, 2),
                "sma50": round(latest_sma50, 2),
                "macd": round(latest_macd, 4),
                "signal": round(latest_signal, 4),
                "histogram": round(latest_hist, 4),
                "rsi": round(latest_rsi, 2),
                "signals": []
            }
            
            # Trend signals
            if latest_close > latest_sma20 > latest_sma50:
                score += 3
                details["signals"].append("price_above_ma20_ma50")
            elif latest_close < latest_sma20 < latest_sma50:
                score -= 3
                details["signals"].append("price_below_ma20_ma50")
            
            # MACD signals
            if latest_macd > latest_signal and latest_hist > 0:
                score += 2
                details["signals"].append("macd_bullish")
            elif latest_macd < latest_signal and latest_hist < 0:
                score -= 2
                details["signals"].append("macd_bearish")
            
            # RSI signals
            if latest_rsi > 70:
                score -= 2
                details["signals"].append("rsi_overbought")
            elif latest_rsi < 30:
                score += 2
                details["signals"].append("rsi_oversold")
            elif 40 < latest_rsi < 60:
                score += 1
                details["signals"].append("rsi_neutral")
            
            # Price momentum (5-day change)
            if len(close) >= 5:
                change_5d = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]
                if change_5d > 0.05:
                    score += 2
                    details["signals"].append("strong_5d_momentum")
                elif change_5d < -0.05:
                    score -= 2
                    details["signals"].append("weak_5d_momentum")
            
            # Normalize score to 0-15 range
            # Raw score can be negative, shift and scale
            normalized = max(0, min(15, int((score + 10) * 15 / 20)))  # maps -10..10 to 0..15
            return normalized, details
            
        except Exception as e:
            self.logger.error(f"Technical scan failed for {symbol}: {e}", exc_info=True)
            return 0, {"error": str(e)}