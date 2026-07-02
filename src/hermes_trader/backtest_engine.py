"""Backtest engine — validate strategies before deploying.

Simple vectorized backtester using yfinance data.
Tests confluence scoring strategy on historical data.
"""

import json
import os
from datetime import datetime
from typing import Optional


def backtest_confluence(
    symbol: str = "SPY",
    period: str = "6mo",
    entry_threshold: int = 12,
    stop_pct: float = 0.015,
    tp_pct: float = 0.04,
    max_hold_days: int = 10,
) -> dict:
    """Backtest the confluence scoring strategy on historical data.

    Entry: when confluence score >= entry_threshold
    Exit: stop loss, take profit, or max hold days
    """
    try:
        import yfinance as yf
        import numpy as np

        data = yf.Ticker(symbol).history(period=period)
        if len(data) < 50:
            return {"error": "Insufficient data", "symbol": symbol}

        close = data["Close"].values
        high = data["High"].values
        low = data["Low"].values
        dates = data.index.tolist()

        # Calculate indicators
        ma20 = np.convolve(close, np.ones(20)/20, mode='valid')
        ma50 = np.convolve(close, np.ones(min(50, len(close)))/min(50, len(close)), mode='valid')

        # RSI
        delta = np.diff(close)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = np.convolve(gain, np.ones(14)/14, mode='valid')
        avg_loss = np.convolve(loss, np.ones(14)/14, mode='valid')
        rs = avg_gain / np.where(avg_loss == 0, 1, avg_loss)
        rsi = 100 - (100 / (1 + rs))

        # MACD
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        macd = ema12 - ema26
        macd_signal = _ema(macd, 9)
        macd_hist = macd - macd_signal

        # Simulate trades
        trades = []
        in_trade = False
        entry_price = 0
        entry_idx = 0

        start_idx = max(50, len(close) - len(ma20))

        for i in range(start_idx, len(close)):
            if in_trade:
                # Check exit conditions
                pnl_pct = (close[i] / entry_price - 1) * 100
                days_held = i - entry_idx

                if pnl_pct <= -stop_pct * 100:
                    # Stop loss
                    trades.append({
                        "entry_date": str(dates[entry_idx]),
                        "exit_date": str(dates[i]),
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(close[i], 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "days_held": days_held,
                        "exit_reason": "STOP_LOSS",
                    })
                    in_trade = False
                elif pnl_pct >= tp_pct * 100:
                    # Take profit
                    trades.append({
                        "entry_date": str(dates[entry_idx]),
                        "exit_date": str(dates[i]),
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(close[i], 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "days_held": days_held,
                        "exit_reason": "TAKE_PROFIT",
                    })
                    in_trade = False
                elif days_held >= max_hold_days:
                    # Time exit
                    trades.append({
                        "entry_date": str(dates[entry_idx]),
                        "exit_date": str(dates[i]),
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(close[i], 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "days_held": days_held,
                        "exit_reason": "TIME_EXIT",
                    })
                    in_trade = False
            else:
                # Check entry conditions (confluence score)
                idx20 = i - (len(close) - len(ma20))
                idx50 = i - (len(close) - len(ma50))
                idx_rsi = i - (len(close) - len(rsi))
                idx_macd = i - (len(close) - len(macd_hist))

                if idx20 < 0 or idx50 < 0 or idx_rsi < 0 or idx_macd < 0:
                    continue

                score = 0
                if close[i] > ma20[idx20]:
                    score += 3
                if ma20[idx20] > ma50[idx50]:
                    score += 2
                ret5 = (close[i] / close[i-5] - 1) * 100 if i >= 5 else 0
                if ret5 > 5:
                    score += 5
                elif ret5 > 2:
                    score += 3
                elif ret5 > 0:
                    score += 1
                if 40 < rsi[idx_rsi] < 60:
                    score += 3
                elif rsi[idx_rsi] < 35:
                    score += 4
                if macd_hist[idx_macd] > 0:
                    score += 3
                elif macd_hist[idx_macd] > macd_hist[idx_macd-1]:
                    score += 2

                if score >= entry_threshold:
                    in_trade = True
                    entry_price = close[i]
                    entry_idx = i

        # Calculate statistics
        if not trades:
            return {"symbol": symbol, "trades": 0, "error": "No trades generated"}

        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]
        win_rate = len(wins) / len(trades) * 100
        avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
        total_return = sum(t["pnl_pct"] for t in trades)
        max_drawdown = min(t["pnl_pct"] for t in trades)

        # Sharpe ratio (simplified)
        returns = [t["pnl_pct"] for t in trades]
        sharpe = np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0

        return {
            "symbol": symbol,
            "period": period,
            "entry_threshold": entry_threshold,
            "stop_pct": stop_pct,
            "tp_pct": tp_pct,
            "max_hold_days": max_hold_days,
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_win": round(float(avg_win), 2),
            "avg_loss": round(float(avg_loss), 2),
            "total_return": round(total_return, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(float(sharpe), 2),
            "expectancy": round(win_rate/100 * float(avg_win) + (1-win_rate/100) * float(avg_loss), 2),
            "recent_trades": trades[-5:],
        }

    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _ema(data, span):
    """Exponential moving average."""
    import numpy as np
    alpha = 2 / (span + 1)
    ema = np.zeros_like(data, dtype=float)
    ema[0] = data[0]
    for i in range(1, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]
    return ema


if __name__ == "__main__":
    for sym in ["SPY", "AAPL", "MSFT", "META"]:
        result = backtest_confluence(sym)
        if "error" not in result or result.get("trades"):
            print(f"{sym}: {result.get('trades',0)} trades, WR={result.get('win_rate',0)}%, Return={result.get('total_return',0)}%, Sharpe={result.get('sharpe_ratio',0)}")
