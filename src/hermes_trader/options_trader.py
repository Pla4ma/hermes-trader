"""Options trading engine — scans, scores, and executes options trades.

Targets cheap options with high delta for maximum leverage on a small account.
"""
import json
import logging
from datetime import datetime, timedelta

import yfinance as yf

from .integrations.robinhood_broker import robinhood_mcp_call, ROBINHOOD_ACCOUNT

logger = logging.getLogger("hermes_trader.options_trader")

# Config for $50 account
MAX_OPTION_PREMIUM = 50.0      # Max premium per contract
MAX_OPTION_RISK = 20.0         # Max loss per trade
MIN_DELTA = 0.15               # Minimum delta for meaningful exposure
MAX_DAYS_TO_EXPIRY = 21        # Max DTE
MIN_DAYS_TO_EXPIRY = 2         # Min DTE (avoid 0DTE)
MIN_VOLUME = 100               # Minimum volume
MAX_SPREAD_PCT = 20            # Max bid-ask spread %


def scan_options(symbol: str = "SPY", direction: str = "bullish") -> list[dict]:
    """Scan options chain for tradeable contracts."""
    try:
        ticker = yf.Ticker(symbol)
        today = datetime.utcnow().date()
        max_expiry = today + timedelta(days=MAX_DAYS_TO_EXPIRY)

        try:
            expirations = ticker.options
        except Exception:
            return []

        # Filter expirations within our DTE window
        valid_exps = []
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if MIN_DAYS_TO_EXPIRY <= dte <= MAX_DAYS_TO_EXPIRY:
                    valid_exps.append((exp_str, exp_date, dte))
            except Exception:
                continue

        if not valid_exps:
            return []

        candidates = []
        chain_df_type = "calls" if direction == "bullish" else "puts"

        for exp_str, exp_date, dte in valid_exps:
            try:
                chain = ticker.option_chain(exp_str)
                df = getattr(chain, chain_df_type, None)
                if df is None or df.empty:
                    continue
            except Exception:
                continue

            for _, row in df.iterrows():
                try:
                    sym = row.get("contractSymbol", "")
                    bid = float(row.get("bid", 0) or 0)
                    ask = float(row.get("ask", 0) or 0)
                    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
                    spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999
                    iv = float(row.get("impliedVolatility", 0) or 0)
                    volume = int(row.get("volume", 0) or 0)
                    strike = float(row.get("strike", 0))

                    # yfinance doesn't provide Greeks; approximate from IV + moneyness
                    delta = 0.30  # placeholder
                    gamma = 0.02
                    theta = -0.03

                    # Filters
                    if bid <= 0 or ask <= 0:
                        continue
                    if mid * 100 > MAX_OPTION_PREMIUM:
                        continue
                    if delta < MIN_DELTA:
                        continue
                    if spread_pct > MAX_SPREAD_PCT:
                        continue

                    # Score the contract (0-30)
                    score = 0

                    if delta > 0.40:
                        score += 5
                    elif delta > 0.25:
                        score += 4
                    elif delta > 0.15:
                        score += 3

                    if spread_pct < 5:
                        score += 5
                    elif spread_pct < 10:
                        score += 3
                    elif spread_pct < 15:
                        score += 1

                    if iv and 0.15 < iv < 0.35:
                        score += 4
                    elif iv and 0.10 < iv < 0.50:
                        score += 2

                    if 5 <= dte <= 14:
                        score += 4
                    elif 3 <= dte <= 21:
                        score += 2

                    if gamma > 0.01:
                        score += 3
                    elif gamma > 0.005:
                        score += 2

                    cost_efficiency = delta / mid if mid > 0 else 0
                    if cost_efficiency > 5:
                        score += 4
                    elif cost_efficiency > 2:
                        score += 2

                    daily_decay = abs(theta) / mid if mid > 0 else 0
                    if daily_decay < 0.05:
                        score += 3
                    elif daily_decay < 0.10:
                        score += 1

                    candidates.append({
                        "symbol": sym,
                        "underlying": symbol,
                        "type": direction,
                        "strike": strike,
                        "expiry": exp_str,
                        "dte": dte,
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "mid": round(mid, 2),
                        "spread_pct": round(spread_pct, 1),
                        "delta": round(delta, 4),
                        "gamma": round(gamma, 4),
                        "theta": round(theta, 4),
                        "vega": 0.15,
                        "iv": round(iv, 3),
                        "cost_efficiency": round(cost_efficiency, 2),
                        "score": min(score, 30),
                        "max_loss": round(mid * 100, 2),
                        "max_loss_per_share": round(mid, 2),
                    })
                except Exception as e:
                    continue

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:10]

    except Exception as e:
        logger.error(f"Options scan failed: {e}")
        return []


def execute_option_trade(candidate: dict) -> dict:
    """Execute an options trade via Robinhood MCP."""
    try:
        # Check if we can afford it
        max_loss = candidate["max_loss"]
        if max_loss > MAX_OPTION_RISK:
            return {"error": f"Max loss ${max_loss} exceeds ${MAX_OPTION_RISK} limit"}

        account = robinhood_mcp_call("get_accounts", {})
        cash = 0.0
        if isinstance(account, dict):
            for key in ("cash", "cash_balance", "available_cash", "buying_power"):
                if key in account:
                    cash = float(account[key])
                    break
        if max_loss > cash:
            return {"error": f"Max loss ${max_loss} exceeds cash ${cash}"}

        # Submit order
        order = robinhood_mcp_call("place_option_order", {
            "account_number": ROBINHOOD_ACCOUNT,
            "instrument_symbol": candidate["symbol"],
            "side": "buy",
            "type": "market",
            "quantity": "1",
        })

        result = {
            "action": "BUY_OPTION",
            "symbol": candidate["symbol"],
            "underlying": candidate["underlying"],
            "strike": candidate["strike"],
            "expiry": candidate["expiry"],
            "max_loss": max_loss,
            "score": candidate["score"],
            "order_id": str(order.id),
            "status": str(order.status),
        }

        # Log to journal
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "BUY_OPTION",
            "symbol": candidate["symbol"],
            "underlying": candidate["underlying"],
            "strike": candidate["strike"],
            "expiry": candidate["expiry"],
            "dte": candidate["dte"],
            "delta": candidate["delta"],
            "mid": candidate["mid"],
            "max_loss": max_loss,
            "score": candidate["score"],
            "order_id": str(order.id),
            "strategy": "options_confluence",
        }
        journal_path = "/opt/hermes-trader/data/journals/paper_orders.jsonl"
        with open(journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        return result

    except Exception as e:
        return {"error": str(e)}


def auto_trade_options(direction: str = "bullish") -> dict:
    """Scan and execute the best options trade."""
    # Determine direction from market regime
    try:
        from .market_regime import detect_regime
        regime = detect_regime()
        regime_name = regime.get("regime", "UNKNOWN")
        if "BEAR" in regime_name:
            direction = "bearish"
        else:
            direction = "bullish"
    except Exception:
        pass

    # Scan options
    candidates = scan_options("SPY", direction)
    if not candidates:
        candidates = scan_options("QQQ", direction)

    if not candidates:
        return {"action": "none", "reason": "No viable options contracts found"}

    best = candidates[0]

    # Check if we can afford it
    acct_cash = 2.01  # Will be read from API
    try:
        account = robinhood_mcp_call("get_accounts", {})
        if isinstance(account, dict):
            for key in ("cash", "cash_balance", "available_cash", "buying_power"):
                if key in account:
                    acct_cash = float(account[key])
                    break
    except Exception:
        pass

    if best["max_loss"] > acct_cash:
        return {
            "action": "none",
            "reason": f"Best option costs ${best['max_loss']} but only ${acct_cash} cash",
            "best_candidate": best,
            "top_3": candidates[:3],
        }

    # Execute
    result = execute_option_trade(best)
    result["candidates_scanned"] = len(candidates)
    result["top_3"] = candidates[:3]
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")

    print("=== OPTIONS SCAN ===")
    candidates = scan_options("SPY", "bullish")
    for c in candidates[:5]:
        print(f"  {c['symbol']}: score={c['score']}/30 strike={c['strike']} dte={c['dte']} mid=${c['mid']} delta={c['delta']} max_loss=${c['max_loss']}")

    if not candidates:
        print("  No viable contracts found")
