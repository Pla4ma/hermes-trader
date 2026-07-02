"""Options trading engine — scans, scores, and executes options trades.

Targets cheap options with high delta for maximum leverage on a small account.
"""

import json
import os
import logging
from datetime import datetime, timedelta

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
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import ContractType

        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")

        opt_client = OptionHistoricalDataClient(api_key, secret_key)
        trading_client = TradingClient(api_key, secret_key, paper=False)

        # Date range
        today = datetime.utcnow().date()
        max_expiry = today + timedelta(days=MAX_DAYS_TO_EXPIRY)

        # Get option contracts
        contract_type = ContractType.CALL if direction == "bullish" else ContractType.PUT

        req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            expiration_date_gte=today + timedelta(days=MIN_DAYS_TO_EXPIRY),
            expiration_date_lte=max_expiry,
            type=contract_type,
        )

        contracts = trading_client.get_option_contracts(req)
        logger.info(f"Found {len(contracts.option_contracts)} {direction} contracts for {symbol}")

        # Get snapshots for each contract
        candidates = []
        for contract in contracts.option_contracts[:50]:  # Limit to 50 for speed
            try:
                chain_req = OptionChainRequest(
                    underlying_symbol=symbol,
                    expiration_date_gte=contract.expiration_date,
                    expiration_date_lte=contract.expiration_date,
                )
                chain = opt_client.get_option_chain(chain_req)
                snap = chain.get(contract.symbol)
                if not snap:
                    continue

                quote = snap.latest_quote
                trade = snap.latest_trade
                greeks = snap.greeks
                iv = snap.implied_volatility

                if not quote or not greeks:
                    continue

                bid = float(quote.bid_price or 0)
                ask = float(quote.ask_price or 0)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
                spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999
                delta = abs(float(greeks.delta or 0))
                theta = float(greeks.theta or 0)
                gamma = float(greeks.gamma or 0)

                # Filters
                if bid <= 0 or ask <= 0:
                    continue
                if mid * 100 > MAX_OPTION_PREMIUM:  # Per-contract premium
                    continue
                if delta < MIN_DELTA:
                    continue
                if spread_pct > MAX_SPREAD_PCT:
                    continue

                # Score the contract (0-30)
                score = 0

                # Delta scoring (higher delta = more exposure)
                if delta > 0.40:
                    score += 5
                elif delta > 0.25:
                    score += 4
                elif delta > 0.15:
                    score += 3

                # Spread scoring (tighter = better)
                if spread_pct < 5:
                    score += 5
                elif spread_pct < 10:
                    score += 3
                elif spread_pct < 15:
                    score += 1

                # IV scoring (high IV = expensive, but also high potential)
                if iv and 0.15 < iv < 0.35:
                    score += 4
                elif iv and 0.10 < iv < 0.50:
                    score += 2

                # DTE scoring (sweet spot 5-14 days)
                dte = (contract.expiration_date - today).days
                if 5 <= dte <= 14:
                    score += 4
                elif 3 <= dte <= 21:
                    score += 2

                # Gamma scoring (higher = more movement per $1)
                if gamma > 0.01:
                    score += 3
                elif gamma > 0.005:
                    score += 2

                # Cost efficiency (delta per dollar)
                cost_efficiency = delta / mid if mid > 0 else 0
                if cost_efficiency > 5:
                    score += 4
                elif cost_efficiency > 2:
                    score += 2

                # Theta decay scoring (less decay = better)
                daily_decay = abs(theta) / mid if mid > 0 else 0
                if daily_decay < 0.05:
                    score += 3
                elif daily_decay < 0.10:
                    score += 1

                candidates.append({
                    "symbol": contract.symbol,
                    "underlying": symbol,
                    "type": direction,
                    "strike": float(contract.strike_price),
                    "expiry": str(contract.expiration_date),
                    "dte": dte,
                    "bid": round(bid, 2),
                    "ask": round(ask, 2),
                    "mid": round(mid, 2),
                    "spread_pct": round(spread_pct, 1),
                    "delta": round(delta, 4),
                    "gamma": round(gamma, 4),
                    "theta": round(theta, 4),
                    "vega": round(float(greeks.vega or 0), 4),
                    "iv": round(float(iv or 0), 3),
                    "cost_efficiency": round(cost_efficiency, 2),
                    "score": min(score, 30),
                    "max_loss": round(mid * 100, 2),  # Per contract
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
    """Execute an options trade on Alpaca."""
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass

        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        client = TradingClient(api_key, secret_key, paper=False)

        # Check if we can afford it
        max_loss = candidate["max_loss"]
        if max_loss > MAX_OPTION_RISK:
            return {"error": f"Max loss ${max_loss} exceeds ${MAX_OPTION_RISK} limit"}

        acct = client.get_account()
        cash = float(acct.cash)
        if max_loss > cash:
            return {"error": f"Max loss ${max_loss} exceeds cash ${cash}"}

        # Submit order
        order = client.submit_order(
            MarketOrderRequest(
                symbol=candidate["symbol"],
                qty=1,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
        )

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
        from alpaca.trading.client import TradingClient
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        client = TradingClient(api_key, secret_key, paper=False)
        acct = client.get_account()
        acct_cash = float(acct.cash)
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
