"""Auto-trading engine — discovers candidates and executes trades.

This module is called by the cron jobs to autonomously scan, score,
and execute trades on the live Alpaca account.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("hermes_trader.auto_trader")


def get_alpaca_api():
    """Get Alpaca API client from env vars."""
    import alpaca_trade_api as tradeapi
    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    return tradeapi.REST(api_key, secret_key, base_url)


def scan_and_score(symbols: list[str] = None) -> list[dict]:
    """Scan watchlist with 6-factor confluence scoring."""
    import yfinance as yf
    import numpy as np

    if symbols is None:
        symbols = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL", "AMD", "AMZN", "NFLX"]

    results = []
    for sym in symbols:
        try:
            data = yf.Ticker(sym).history(period="3mo")
            if len(data) < 21:
                continue
            close = data["Close"]
            high = data["High"]
            low = data["Low"]
            vol = data["Volume"]
            price = close.iloc[-1]

            ma20 = close.rolling(20).mean().iloc[-1]
            ma50 = close.rolling(min(50, len(close))).mean().iloc[-1]
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi = (100 - (100 / (1 + gain / loss))).iloc[-1]
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9).mean()
            tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
            atr = tr.rolling(14).mean().iloc[-1]
            ret5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100
            ret20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100
            vol_avg = vol.rolling(20).mean().iloc[-1]
            vol_ratio = vol.iloc[-1] / vol_avg if vol_avg > 0 else 1
            h20 = high.rolling(20).max().iloc[-1]
            l20 = low.rolling(20).min().iloc[-1]
            pos = (price - l20) / (h20 - l20) if h20 != l20 else 0.5

            score = 0
            if price > ma20:
                score += 3
            if ma20 > ma50:
                score += 2
            if ret5 > 5:
                score += 5
            elif ret5 > 2:
                score += 3
            elif ret5 > 0:
                score += 1
            if 40 < rsi < 60:
                score += 3
            elif rsi < 35:
                score += 4
            if macd_hist.iloc[-1] > 0:
                score += 3
            elif macd_hist.iloc[-1] > macd_hist.iloc[-2]:
                score += 2
            if vol_ratio > 1.5:
                score += 4
            elif vol_ratio > 1.0:
                score += 2
            if pos > 0.7:
                score += 3
            elif pos > 0.4:
                score += 2
            if ret20 > 5:
                score += 2

            results.append({
                "symbol": sym, "price": round(price, 2), "score": min(score, 30),
                "rsi": round(rsi, 1), "ret5": round(ret5, 2), "ret20": round(ret20, 2),
                "macd_hist": round(float(macd_hist.iloc[-1]), 4),
                "vol_ratio": round(vol_ratio, 2), "atr": round(float(atr), 2),
                "stop": round(price - 2 * atr, 2), "target": round(price + 4 * atr, 2),
            })
        except Exception as e:
            logger.warning(f"Scan failed for {sym}: {e}")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def auto_trade(min_score: int = 12, max_notional: float = 20.0) -> dict:
    """Scan, score, and execute the best trade if cash available.

    Returns a dict with the trade result or reason for no trade.
    """
    from alpaca.trading.enums import OrderSide, TimeInForce

    api = get_alpaca_api()
    acct = api.get_account()
    cash = float(acct.cash)
    held = {p.symbol for p in api.list_positions()}
    orders = api.list_orders(status="open")

    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "cash": cash,
        "held": list(held),
        "action": "none",
    }

    # Need at least $5 to trade
    if cash < 5:
        result["reason"] = f"Insufficient cash: ${cash:.2f}"
        return result

    # Already max positions (3)
    if len(held) >= 3:
        result["reason"] = f"Max positions reached: {len(held)}"
        return result

    # Scan watchlist
    candidates = scan_and_score()
    result["candidates_found"] = len(candidates)

    # Filter out held symbols and low scores
    viable = [c for c in candidates if c["symbol"] not in held and c["score"] >= min_score]
    result["viable_candidates"] = len(viable)

    if not viable:
        result["reason"] = f"No candidates above {min_score}/30 threshold"
        return result

    best = viable[0]
    notional = min(cash * 0.9, max_notional)

    # Execute trade
    try:
        order = api.submit_order(
            symbol=best["symbol"],
            notional=round(notional, 2),
            side=OrderSide.BUY,
            type="market",
            time_in_force=TimeInForce.DAY,
        )

        result["action"] = "BUY"
        result["symbol"] = best["symbol"]
        result["notional"] = notional
        result["score"] = best["score"]
        result["price"] = best["price"]
        result["order_id"] = str(order.id)
        result["stop_loss"] = best["stop"]
        result["take_profit"] = best["target"]

        # Log to journal
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "BUY",
            "symbol": best["symbol"],
            "notional": notional,
            "order_id": str(order.id),
            "strategy": "auto_confluence",
            "confluence_score": best["score"],
            "rsi": best["rsi"],
            "ret5": best["ret5"],
            "stop_loss": best["stop"],
            "take_profit": best["target"],
            "reason": f"Auto-trade: {best['symbol']} score={best['score']}/30 RSI={best['rsi']} 5d={best['ret5']:+.1f}%",
        }
        journal_path = "/opt/hermes-trader/data/journals/paper_orders.jsonl"
        with open(journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info(f"AUTO-TRADE: {best['symbol']} ${notional:.2f} score={best['score']}")

    except Exception as e:
        result["action"] = "error"
        result["error"] = str(e)
        logger.error(f"Auto-trade failed: {e}")

    return result


def manage_exits() -> dict:
    """Check positions and set stop losses + take profits.

    For fractional positions, only ONE exit order is allowed.
    We prefer SL (risk management) and let cron handle TP monitoring.
    """
    from alpaca.trading.enums import OrderSide, TimeInForce

    api = get_alpaca_api()
    positions = api.list_positions()
    open_orders = api.list_orders(status="open")
    actions = []

    # Get symbols with existing exit orders
    exit_symbols = {o.symbol for o in open_orders}

    for pos in positions:
        if pos.symbol in exit_symbols:
            continue  # Already has exit order

        entry = float(pos.avg_entry_price)
        qty = pos.qty
        sl_price = round(entry * 0.985, 2)  # 1.5% stop

        try:
            sl = api.submit_order(
                symbol=pos.symbol, qty=qty, side=OrderSide.SELL,
                type="stop", stop_price=str(sl_price),
                time_in_force=TimeInForce.DAY,
            )
            actions.append({"symbol": pos.symbol, "action": "SL_SET", "price": sl_price, "order_id": str(sl.id)})
            logger.info(f"SL set: {pos.symbol} @ ${sl_price}")
        except Exception as e:
            actions.append({"symbol": pos.symbol, "action": "SL_ERROR", "error": str(e)})

    return {"timestamp": datetime.utcnow().isoformat(), "actions": actions}


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")

    if len(sys.argv) > 1 and sys.argv[1] == "exits":
        result = manage_exits()
    else:
        result = auto_trade()
    print(json.dumps(result, indent=2))
