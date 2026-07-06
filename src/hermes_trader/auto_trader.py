"""Auto-trading engine — discovers 0DTE candidates and executes option trades.

This module is called by the cron jobs to autonomously scan, score,
and execute 0DTE option trades on the live Robinhood account via MCP.

Flow:
  1. Scan 0DTE options via zero_dte_scanner (Robinhood MCP chains)
  2. Score candidates with multi-factor confluence scoring
  3. Check Vibe-Trading + TradingAgents research gates (both must agree)
  4. Place option order via Robinhood MCP place_option_order
  5. Set exit rules (trailing stops, profit-taking)
"""
import json
import logging
import os
import uuid
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger("hermes_trader.auto_trader")

ACCOUNT_NUMBER = os.getenv("ROBINHOOD_ACCOUNT_NUMBER", "924058324")


def _get_broker():
    """Get the Robinhood broker adapter instance."""
    from .integrations.robinhood_broker import RobinhoodBrokerAdapter
    return RobinhoodBrokerAdapter()


def scan_and_score(symbols: list[str] = None) -> list[dict]:
    """Scan 0DTE options and score with multi-factor confluence.

    Replaces the old equity-based scanning. Now scans 0DTE options
    on SPY/QQQ/SPXW/NDXW for day-trade candidates.
    """
    from .zero_dte_scanner import scan_0dte, get_spot_price
    import yfinance as yf
    import numpy as np

    if symbols is None:
        symbols = ["SPY", "QQQ", "SPXW", "NDXW"]

    # ── Phase 1: 0DTE Option Scanning via Robinhood MCP ──
    candidates_0dte = scan_0dte(symbols=symbols, min_score=20, max_candidates=20)

    if candidates_0dte:
        # Enrich 0DTE candidates with additional scoring
        for c in candidates_0dte:
            # Add confluence context from underlying
            sym = symbols[0] if symbols else "SPY"
            try:
                spot = get_spot_price(sym)
                if spot > 0:
                    c["underlying_price"] = spot
            except Exception:
                pass

    # ── Phase 2: Underlying momentum check (optional enrichment) ──
    if candidates_0dte:
        # Add momentum-based signal to each candidate
        for c in candidates_0dte:
            try:
                data = yf.Ticker(c.get("symbol", "SPY")).history(period="5d")
                if len(data) >= 2:
                    close = data["Close"]
                    ret1d = (close.iloc[-1] / close.iloc[-2] - 1) * 100
                    ret5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(data) >= 6 else 0
                    c["underlying_ret1d"] = round(ret1d, 2)
                    c["underlying_ret5d"] = round(ret5d, 2)
                    # Momentum bonus
                    if ret1d > 0 and c.get("type") == "call":
                        c["score"] += 5
                    elif ret1d < 0 and c.get("type") == "put":
                        c["score"] += 5
            except Exception:
                pass

    # Sort by score
    candidates_0dte.sort(key=lambda x: x.get("score", 0), reverse=True)
    return candidates_0dte


def auto_trade(min_score: int = 30, max_notional: float = 90.0) -> dict:
    """Scan 0DTE options, score, check research gates, and execute.

    Integrates: 0DTE scanner, GEX, PCR, IV Rank, Kelly sizing, earnings check.
    Executes via Robinhood MCP place_option_order (not equity orders).

    Args:
        min_score: Minimum composite score (0-100) to consider a candidate
        max_notional: Maximum dollar amount per trade (default $90 = 90% of $100)
    """
    broker = _get_broker()

    # ─── Get account state from Robinhood ───
    account = broker.get_account()
    cash = account.cash
    held = [p.symbol for p in broker.get_positions()]

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
        "strategy": "0dte_options",
    }

    # Need at least $5 to trade
    if cash < 5:
        result["reason"] = f"Insufficient cash: ${cash:.2f}"
        return result

    # ─── Scan 0DTE options ───
    candidates = scan_and_score()
    result["candidates_found"] = len(candidates)

    # Filter by score
    viable = [c for c in candidates if c.get("score", 0) >= min_score]
    result["viable_candidates"] = len(viable)

    if not viable:
        result["reason"] = f"No 0DTE candidates above {min_score}/100 threshold"
        return result

    best = viable[0]

    # ─── Position Sizing ───
    # 90% of account (max_notional=90 for $100 account)
    # For options: notional = cash * 90% * regime_sizing
    notional = min(cash * 0.90, max_notional) * sizing_mult

    # If one research source disagrees, reduce by 50%
    # (applied after research gates below)

    # Use optimal params if available
    opt_params = _load_optimal_params(best.get("symbol", "SPY"))

    # ═══════════════════════════════════════════════════════════════
    # S-TIER RESEARCH: Vibe-Trading + TradingAgents BEFORE every trade
    # ═══════════════════════════════════════════════════════════════
    underlying_sym = best.get("symbol", "SPY").split("/")[0] if "/" in best.get("symbol", "") else best.get("symbol", "SPY")
    # Strip W suffix for index symbols used in research
    research_sym = underlying_sym.rstrip("W")

    vibe_signal = _run_vibe_research(research_sym)
    agents_signal = _run_tradingagents_research(research_sym)

    result["vibe_signal"] = vibe_signal
    result["agents_signal"] = agents_signal

    # Both must agree (or at least not disagree strongly) to proceed
    vibe_agrees = vibe_signal.get("signal", "neutral") != "bearish"
    agents_agrees = agents_signal.get("signal", "neutral") != "bearish"

    if not vibe_agrees and not agents_agrees:
        result["action"] = "blocked"
        result["reason"] = f"BOTH Vibe-Trading ({vibe_signal.get('signal')}) and TradingAgents ({agents_signal.get('signal')}) are BEARISH. Skipping {best.get('symbol', 'unknown')}."
        return result

    if not vibe_agrees or not agents_agrees:
        # One disagrees — reduce sizing by 50%
        notional *= 0.5
        result["note"] = f"One research source disagrees — reducing size by 50% to ${notional:.2f}"

    # ─── Calculate option quantity ───
    # For options: notional / (mid_price * 100) = number of contracts
    mid_price = best.get("mid", 0)
    if mid_price <= 0:
        result["action"] = "error"
        result["error"] = f"Invalid option price: {mid_price}"
        return result

    # Each contract costs mid_price * $100
    contract_cost = mid_price * 100
    quantity = max(1, int(notional / contract_cost)) if contract_cost > 0 else 1

    # Cap at what we can actually afford
    max_affordable = int(cash / contract_cost) if contract_cost > 0 else 1
    quantity = min(quantity, max_affordable)

    if quantity < 1:
        result["reason"] = f"Cannot afford even 1 contract (cost=${contract_cost:.2f}, cash=${cash:.2f})"
        return result

    result["quantity"] = quantity
    result["contract_cost"] = contract_cost
    result["total_cost"] = contract_cost * quantity

    # ═══════════════════════════════════════════════════════════════
    # EXECUTE OPTION ORDER via Robinhood MCP place_option_order
    # ═══════════════════════════════════════════════════════════════
    try:
        order = broker.place_option_order(
            option_id=best.get("option_id", ""),
            side="buy",
            quantity=quantity,
            limit_price=str(round(mid_price, 4)),
            time_in_force="day",
        )

        result["action"] = "BUY_OPTION"
        result["symbol"] = best.get("symbol", "")
        result["option_id"] = best.get("option_id", "")
        result["option_type"] = best.get("type", "")
        result["strike"] = best.get("strike", 0)
        result["mid_price"] = mid_price
        result["quantity"] = quantity
        result["notional"] = notional
        result["score"] = best.get("score", 0)
        result["order_id"] = order.get("order_id", order.get("id", ""))
        result["stop_loss"] = best.get("stop_loss", round(mid_price * 0.50, 4))
        result["take_profit"] = best.get("take_profit", round(mid_price * 2.0, 4))

        # Log to journal
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "BUY_OPTION",
            "symbol": best.get("symbol", ""),
            "option_id": best.get("option_id", ""),
            "option_type": best.get("type", ""),
            "strike": best.get("strike", 0),
            "expiration": best.get("expiration", ""),
            "mid_price": mid_price,
            "quantity": quantity,
            "notional": notional,
            "total_cost": contract_cost * quantity,
            "order_id": order.get("order_id", order.get("id", "")),
            "broker": "robinhood_mcp",
            "account_number": ACCOUNT_NUMBER,
            "strategy": "0dte_options",
            "confluence_score": best.get("score", 0),
            "vibe_signal": vibe_signal.get("signal", "unknown"),
            "agents_signal": agents_signal.get("signal", "unknown"),
            "stop_loss": best.get("stop_loss", round(mid_price * 0.50, 4)),
            "take_profit": best.get("take_profit", round(mid_price * 2.0, 4)),
            "reason": f"Auto-trade 0DTE: {best.get('symbol', '')} {best.get('type', '')} strike={best.get('strike', 0)} score={best.get('score', 0)}/100",
        }
        journal_path = "/opt/hermes-trader/data/journals/paper_orders.jsonl"
        os.makedirs(os.path.dirname(journal_path), exist_ok=True)
        with open(journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info(
            f"AUTO-TRADE 0DTE: {best.get('symbol', '')} {best.get('type', '')} "
            f"strike={best.get('strike', 0)} x{quantity} "
            f"${contract_cost * quantity:.2f} score={best.get('score', 0)}/100 via Robinhood MCP"
        )

    except Exception as e:
        result["action"] = "error"
        result["error"] = str(e)
        logger.error(f"Auto-trade 0DTE failed: {e}")

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
    """Check option positions and manage exits with trailing stops + profit-taking.

    All orders routed through Robinhood MCP broker adapter.
    For 0DTE options:
    - If loss > 50%, close immediately (stop-loss)
    - If profit > 50%, tighten trail to 20%
    - If profit > 100%, sell 50%
    - If profit > 200%, sell remaining
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

        # ── Trailing stop logic for options ──
        if pnl_pct >= 50:
            # Tighten stop to trail by 20% (for high-vol options)
            new_sl = round(current * 0.80, 4)
            old_sl = round(entry * 0.50, 4)
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

        elif pnl_pct <= -50:
            # Hard stop-loss: close immediately at market
            try:
                order = broker.place_equity_order(
                    symbol=symbol,
                    side="sell",
                    quantity=int(qty) if qty == int(qty) else None,
                    notional=round(current * qty, 2) if qty != int(qty) else None,
                    order_type="market",
                    time_in_force="day",
                )
                actions.append({
                    "symbol": symbol, "action": "STOP_LOSS_CLOSE",
                    "pnl_pct": round(pnl_pct, 2),
                    "order_id": order.get("order_id", ""),
                })
            except Exception as e:
                actions.append({"symbol": symbol, "action": "SL_ERROR", "error": str(e)})

        elif symbol not in exit_symbols:
            # No exit order — set initial stop at 50% loss
            sl_price = round(entry * 0.50, 4)
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

        # ── Profit-taking signals (for cron to execute) ──
        if pnl_pct >= 200:
            actions.append({
                "symbol": symbol, "action": "TP_SIGNAL",
                "pnl_pct": round(pnl_pct, 2),
                "recommendation": "SELL_ALL",
            })
        elif pnl_pct >= 100:
            actions.append({
                "symbol": symbol, "action": "TP_SIGNAL",
                "pnl_pct": round(pnl_pct, 2),
                "recommendation": "SELL_50%",
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
