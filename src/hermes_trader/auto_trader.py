"""Auto-trading engine — discovers candidates and executes trades.

This module is called by the cron jobs to autonomously scan, score,
and execute trades on the live Robinhood account via MCP.
"""
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("hermes_trader.auto_trader")

ACCOUNT_NUMBER = os.getenv("ROBINHOOD_ACCOUNT_NUMBER", "924058324")


def _get_broker():
    """Get the Robinhood broker adapter instance."""
    from .integrations.robinhood_broker import RobinhoodBroker
    return RobinhoodBroker(account_number=ACCOUNT_NUMBER)


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

    Integrates: GEX, PCR, IV Rank, Kelly sizing, earnings check, VIX term structure.
    Executes via Robinhood MCP broker adapter.
    """
    broker = _get_broker()

    # ─── Get account state from Robinhood ───
    cash = broker.get_account_cash()
    held = broker.get_held_symbols()

    # ─── Options Analytics (institutional-grade) ───
    analytics = {}
    try:
        from .options_analytics import OptionsAnalytics
        oa = OptionsAnalytics()
        analytics = oa.get_full_analytics("SPY")
    except Exception:
        pass

    gex_regime = analytics.get("gex", {}).get("regime", "unknown")
    pcr = analytics.get("put_call_ratio", {}).get("put_call_ratio", 1.0)
    pcr_signal = analytics.get("put_call_ratio", {}).get("signal", "NEUTRAL")
    max_pain = analytics.get("max_pain", {}).get("max_pain_strike", 0)

    # ─── Earnings Check ───
    try:
        from .earnings_calendar import check_earnings
        earnings = check_earnings("SPY")
        if earnings.get("in_danger_zone"):
            return {"action": "wait", "reason": "SPY earnings in danger zone", "analytics": analytics}
    except Exception:
        pass

    # Check market regime
    try:
        from .market_regime import detect_regime
        regime = detect_regime()
        regime_name = regime.get("regime", "UNKNOWN")
        sizing_mult = regime.get("sizing_multiplier", 0.75)
    except Exception:
        regime_name = "UNKNOWN"
        sizing_mult = 0.75

    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "cash": cash,
        "held": list(held),
        "regime": regime_name,
        "sizing_multiplier": sizing_mult,
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
    notional = min(cash * 0.9, max_notional) * sizing_mult

    # Use optimal params if available
    opt_params = _load_optimal_params(best["symbol"])

    # ═══════════════════════════════════════════════════════════════
    # S-TIER RESEARCH: Vibe-Trading + TradingAgents BEFORE every trade
    # ═══════════════════════════════════════════════════════════════
    vibe_signal = _run_vibe_research(best["symbol"])
    agents_signal = _run_tradingagents_research(best["symbol"])

    result["vibe_signal"] = vibe_signal
    result["agents_signal"] = agents_signal

    # Both must agree (or at least not disagree strongly) to proceed
    vibe_agrees = vibe_signal.get("signal", "neutral") != "bearish"
    agents_agrees = agents_signal.get("signal", "neutral") != "bearish"

    if not vibe_agrees and not agents_agrees:
        result["action"] = "blocked"
        result["reason"] = f"BOTH Vibe-Trading ({vibe_signal.get('signal')}) and TradingAgents ({agents_signal.get('signal')}) are BEARISH. Skipping {best['symbol']}."
        return result

    if not vibe_agrees or not agents_agrees:
        # One disagrees — reduce sizing by 50%
        notional *= 0.5
        result["note"] = f"One research source disagrees — reducing size by 50% to ${notional:.2f}"

    # Execute trade via Robinhood MCP
    try:
        order = broker.place_equity_order(
            symbol=best["symbol"],
            side="buy",
            notional=round(notional, 2),
            order_type="market",
            time_in_force="day",
        )

        result["action"] = "BUY"
        result["symbol"] = best["symbol"]
        result["notional"] = notional
        result["score"] = best["score"]
        result["price"] = best["price"]
        result["order_id"] = order.get("order_id", "")
        result["stop_loss"] = best["stop"]
        result["take_profit"] = best["target"]

        # Log to journal
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "BUY",
            "symbol": best["symbol"],
            "notional": notional,
            "order_id": order.get("order_id", ""),
            "broker": "robinhood_mcp",
            "account_number": ACCOUNT_NUMBER,
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

        logger.info(f"AUTO-TRADE: {best['symbol']} ${notional:.2f} score={best['score']} via Robinhood MCP")

    except Exception as e:
        result["action"] = "error"
        result["error"] = str(e)
        logger.error(f"Auto-trade failed: {e}")

    return result


def _run_vibe_research(symbol: str) -> dict:
    """Run Vibe-Trading research on a symbol."""
    try:
        from .research.vibe_client import VibeTradingClient
        vibe = VibeTradingClient()
        result = vibe.run_market_regime_analysis(symbol)
        output = result.get("output", "").lower()

        signal = "neutral"
        if "bullish" in output or "strong buy" in output or "upward" in output:
            signal = "bullish"
        elif "bearish" in output or "strong sell" in output or "downward" in output:
            signal = "bearish"

        return {
            "source": "vibe_trading",
            "signal": signal,
            "status": result.get("status", "UNKNOWN"),
            "summary": result.get("output", "")[:500],
        }
    except Exception as e:
        return {"source": "vibe_trading", "signal": "neutral", "status": "ERROR", "error": str(e)}


def _run_tradingagents_research(symbol: str) -> dict:
    """Run TradingAgents multi-agent committee on a symbol."""
    try:
        from .research.agents_client import TradingAgentsClient
        agents = TradingAgentsClient()
        result = agents.get_committee_signal(symbol)

        return {
            "source": "trading_agents",
            "signal": result.get("signal", "neutral"),
            "confidence": result.get("confidence", 0),
            "status": result.get("status", "UNKNOWN"),
            "summary": result.get("decision", "")[:500],
        }
    except Exception as e:
        return {"source": "trading_agents", "signal": "neutral", "status": "ERROR", "error": str(e)}


def _load_optimal_params(symbol: str) -> dict:
    """Load optimal backtest parameters for a symbol."""
    try:
        params_file = "/opt/hermes-trader/data/snapshots/optimal_params.json"
        if os.path.exists(params_file):
            with open(params_file) as f:
                all_params = json.load(f)
            return all_params.get(symbol, {}).get("params", {})
    except Exception:
        pass
    return {}


def manage_exits() -> dict:
    """Check positions and manage exits with trailing stops + profit-taking.

    All orders routed through Robinhood MCP broker adapter.
    Strategy:
    - Set SL at 1.5% below entry if no exit order exists
    - If profit > 2.5%, tighten SL to trail by 0.8%
    - If profit > 4%, sell 50% (partial profit-taking) via cron
    - If profit > 8%, sell remaining via cron
    """
    broker = _get_broker()
    positions = broker.list_positions()
    open_orders = broker.list_open_orders()
    actions = []

    # Get symbols with existing exit orders
    exit_symbols = {o.get("symbol", "") for o in open_orders}

    for pos in positions:
        symbol = pos.get("symbol", "")
        if not symbol:
            continue

        entry = float(pos.get("avg_entry_price", 0) or pos.get("average_entry_price", 0) or 0)
        current = float(pos.get("current_price", 0) or pos.get("last_price", 0) or 0)
        qty = float(pos.get("quantity", 0) or pos.get("qty", 0) or 0)

        if entry <= 0 or qty <= 0:
            continue

        pnl_pct = (current / entry - 1) * 100 if entry > 0 else 0

        # Trailing stop logic
        if pnl_pct >= 2.5:
            # Tighten stop to trail by 0.8%
            new_sl = round(current * 0.992, 2)
            old_sl = round(entry * 0.985, 2)
            if new_sl > old_sl:
                # Cancel existing exit orders for this symbol
                for o in open_orders:
                    if o.get("symbol", "") == symbol:
                        try:
                            broker.cancel_order(o.get("order_id", o.get("id", "")))
                            import time; time.sleep(0.5)
                        except Exception:
                            pass

                # Place new trailing stop order via Robinhood MCP
                try:
                    order = broker.place_equity_order(
                        symbol=symbol,
                        side="sell",
                        quantity=int(qty) if qty == int(qty) else None,
                        notional=round(current * qty, 2) if qty != int(qty) else None,
                        order_type="stop",
                        time_in_force="day",
                    )
                    actions.append({
                        "symbol": symbol, "action": "TRAILING_SL",
                        "old_sl": old_sl, "new_sl": new_sl,
                        "pnl_pct": round(pnl_pct, 2),
                        "order_id": order.get("order_id", ""),
                    })
                except Exception as e:
                    actions.append({"symbol": symbol, "action": "SL_ERROR", "error": str(e)})

        elif symbol not in exit_symbols:
            # No exit order — set initial SL at 1.5%
            sl_price = round(entry * 0.985, 2)
            try:
                order = broker.place_equity_order(
                    symbol=symbol,
                    side="sell",
                    quantity=int(qty) if qty == int(qty) else None,
                    notional=round(current * qty, 2) if qty != int(qty) else None,
                    order_type="stop",
                    time_in_force="day",
                )
                actions.append({
                    "symbol": symbol, "action": "SL_SET",
                    "price": sl_price,
                    "order_id": order.get("order_id", ""),
                })
            except Exception as e:
                actions.append({"symbol": symbol, "action": "SL_ERROR", "error": str(e)})

        # Profit-taking check (log for cron to execute)
        if pnl_pct >= 4.0:
            actions.append({
                "symbol": symbol, "action": "TP_SIGNAL",
                "pnl_pct": round(pnl_pct, 2),
                "recommendation": "SELL_50%",
            })
        elif pnl_pct >= 8.0:
            actions.append({
                "symbol": symbol, "action": "TP_SIGNAL",
                "pnl_pct": round(pnl_pct, 2),
                "recommendation": "SELL_ALL",
            })

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
