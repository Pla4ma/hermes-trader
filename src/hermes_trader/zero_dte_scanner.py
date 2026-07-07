"""0DTE Options Scanner — finds cheap SPY/QQQ options expiring today.

Scans today's options chains for SPY and QQQ via Robinhood MCP, filters for
cheap contracts ($0.50–$5.00 premium), scores them on delta/gamma/volume/
spread/IV, and returns the top 3 candidates for aggressive 0DTE trading.

Usage:
    from hermes_trader.zero_dte_scanner import scan_0dte, get_best_0dte_candidate
    candidates = scan_0dte()               # top 3 scored candidates
    best = get_best_0dte_candidate()       # single best, or None
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from .config import config
import yfinance as yf

logger = logging.getLogger("hermes_trader.zero_dte_scanner")

# ── Constants ─────────────────────────────────────────────────
UNDERLYINGS = ("SPY", "QQQ")

# Price filter: only consider options in this premium range ($/contract)
MIN_PREMIUM_MID = 0.50
MAX_PREMIUM_MID = 5.00

# Top N candidates to return
TOP_N = 3

# Scoring weights (total = 100)
W_DELTA = 25    # Ideal delta 0.20–0.40 for directional 0DTE
W_GAMMA = 25    # Higher gamma = more explosive 0DTE moves
W_VOLUME = 20   # Higher volume = tighter execution
W_SPREAD = 20   # Narrower bid/ask = less slippage
W_IV = 10       # Moderate IV is best for long options

# Ideal parameter ranges
IDEAL_DELTA_LOW = 0.35
IDEAL_DELTA_HIGH = 0.50
IDEAL_IV_LOW = 0.25    # 25% annualized
IDEAL_IV_HIGH = 0.45   # 45% annualized

# Risk-free rate for Greeks estimation
RISK_FREE_RATE = 0.05

# Black-Scholes tau for 0DTE (~1 trading day)
DTE_TAU = 1.0 / 252.0


# ── Helpers ───────────────────────────────────────────────────

def _safe_float(d: dict, *keys: str, default: float = 0.0) -> float:
    """Try multiple keys to extract a float from a dict. Returns default on failure."""
    for key in keys:
        if key in d:
            val = d[key]
            if val is None:
                continue
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return default


def _safe_int(d: dict, *keys: str, default: int = 0) -> int:
    """Try multiple keys to extract an int from a dict."""
    return int(_safe_float(d, *keys, default=float(default)))


# ── MCP Integration ───────────────────────────────────────────

def _unwrap_mcp_response(data: Any) -> Any:
    """Unwrap Robinhood MCP's {'data': ..., 'guide': '...'} envelope.

    All Robinhood MCP tool responses are wrapped in
    {"data": <actual_result>, "guide": "<help_text>"}.
    This helper extracts the inner 'data' payload.
    """
    if isinstance(data, dict) and "data" in data and "guide" in data:
        return data["data"]
    return data


def _mcp_call(tool_name: str, arguments: Optional[dict] = None) -> Any:
    """Make a JSON-RPC call to Robinhood MCP.

    Uses robinhood_mcp_call from the broker adapter if available,
    falls back to direct HTTP call otherwise.
    Automatically unwraps the MCP {'data': ..., 'guide': '...'} envelope.
    """
    try:
        from .integrations.robinhood_broker import robinhood_mcp_call
        return _unwrap_mcp_response(robinhood_mcp_call(tool_name, arguments))
    except ImportError:
        return _unwrap_mcp_response(_mcp_call_direct(tool_name, arguments))


def _mcp_call_direct(tool_name: str, arguments: Optional[dict] = None) -> Any:
    """Direct JSON-RPC call to Robinhood MCP (fallback)."""
    import uuid
    import requests

    token_path = Path.home() / ".hermes" / "mcp-tokens" / "robinhood.json"
    if not token_path.exists():
        raise FileNotFoundError(f"Robinhood token not found at {token_path}")

    with open(token_path) as f:
        token = json.load(f).get("access_token", "")

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    resp = requests.post(
        "https://agent.robinhood.com/mcp/trading",
        json=payload, headers=headers, timeout=30,
    )
    if resp.status_code >= 400:
        raise ValueError(f"MCP HTTP {resp.status_code}: {resp.text[:300]}")

    # Parse SSE response
    result_data = None
    for line in resp.text.strip().splitlines():
        line = line.strip()
        if line.startswith("data:"):
            json_str = line[len("data:"):].strip()
            if json_str:
                try:
                    result_data = json.loads(json_str)
                except json.JSONDecodeError:
                    continue
    if result_data is None:
        result_data = json.loads(resp.text.strip())

    if "error" in result_data:
        raise ValueError(f"MCP error: {result_data['error']}")

    result = result_data.get("result", result_data)
    if isinstance(result, dict) and "content" in result:
        content = result["content"]
        if isinstance(content, list) and len(content) > 0:
            text_block = content[0]
            if isinstance(text_block, dict) and "text" in text_block:
                try:
                    return json.loads(text_block["text"])
                except (json.JSONDecodeError, TypeError):
                    return {"raw": text_block["text"]}
    return result


# ── Data Fetching ─────────────────────────────────────────────

def get_option_chains(symbol: str) -> list[dict]:
    """Get option chains for a symbol via Robinhood MCP."""
    data = _mcp_call("get_option_chains", {"underlying_symbol": symbol})
    if isinstance(data, dict):
        return data.get("chains", data.get("results", []))
    return data if isinstance(data, list) else []


def get_option_instruments(
    symbol: str,
    expiration_date: str,
    option_type: Optional[str] = None,
) -> list[dict]:
    """Get option contracts for a specific expiration date."""
    args: dict[str, Any] = {
        "chain_symbol": symbol,
        "expiration_dates": expiration_date,
    }
    if option_type:
        args["type"] = option_type
    data = _mcp_call("get_option_instruments", args)
    if isinstance(data, dict):
        return data.get("results", data.get("instruments", []))
    return data if isinstance(data, list) else []


def get_option_quotes(instrument_ids: list[str]) -> list[dict]:
    """Get real-time quotes for option contracts (batches of 20)."""
    all_quotes: list[dict] = []
    batch_size = 20
    for i in range(0, len(instrument_ids), batch_size):
        batch = instrument_ids[i:i + batch_size]
        if not batch:
            continue
        try:
            data = _mcp_call("get_option_quotes", {"instrument_ids": batch})
            if isinstance(data, dict):
                all_quotes.extend(data.get("results", []))
            elif isinstance(data, list):
                all_quotes.extend(data)
        except Exception as e:
            logger.warning(f"Quote batch failed (batch {i//batch_size}): {e}")
    return all_quotes


def get_spot_price(symbol: str) -> float:
    """Get current spot price via Robinhood equity quotes (yfinance fallback)."""
    try:
        data = _mcp_call("get_equity_quotes", {"symbols": [symbol]})
        if isinstance(data, dict):
            quotes = data.get("quotes", data.get("results", data))
            if isinstance(quotes, dict):
                q = quotes.get(symbol, {})
            elif isinstance(quotes, list) and quotes:
                q = quotes[0] if isinstance(quotes[0], dict) else {}
            else:
                q = {}
        elif isinstance(data, list) and data:
            q = data[0] if isinstance(data[0], dict) else {}
        else:
            q = {}
        price = _safe_float(q, "last_trade_price", "last_price", "mark_price", "close")
        if price > 0:
            return price
    except Exception as e:
        logger.warning(f"Robinhood quote failed for {symbol}: {e}")

    # Fallback: yfinance
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).fast_info
        return float(info.get("lastPrice", 0))
    except Exception:
        return 0.0


# ── 0DTE Expiration Detection ────────────────────────────────

def find_0dte_expiration(symbol: str) -> Optional[str]:
    """Find today's 0DTE expiration date for a symbol.

    Returns the date string (YYYY-MM-DD) if a 0DTE chain exists today,
    or today's date as a fallback (Robinhood may serve 0DTE even if not
    explicitly listed in the chain's expiration_dates).
    """
    today_str = date.today().strftime("%Y-%m-%d")
    try:
        chains = get_option_chains(symbol)
        for chain in chains:
            exp_dates = chain.get("expiration_dates", [])
            if today_str in exp_dates:
                return today_str
    except Exception as e:
        logger.warning(f"Chain lookup failed for {symbol}: {e}")

    # Don't blindly return today — return None if no chain found
    logger.info(f"No 0DTE chain found for {symbol} on {today_str}")
    return None


# ── Greeks Estimation ─────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF — pure numpy, no scipy needed.

    Uses the error function from the math module (stdlib).
    """
    import math
    x = max(-6.0, min(6.0, x))
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF — pure numpy, no scipy needed."""
    import math
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _estimate_greeks(
    spot: float, strike: float, iv: float, option_type: str
) -> dict[str, float]:
    """Estimate delta and gamma from Black-Scholes for 0DTE.

    Uses pure numpy (no scipy dependency).
    Returns dict with 'delta' and 'gamma' (floats).
    """
    try:
        import numpy as np

        sigma = iv if iv > 0 else 0.30
        tau = DTE_TAU
        r = RISK_FREE_RATE

        # d1 and d2
        d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))

        # Delta
        if option_type == "call":
            delta = _norm_cdf(d1)
        else:
            delta = -_norm_cdf(-d1)

        # Gamma (same for calls and puts)
        gamma = _norm_pdf(d1) / (spot * sigma * np.sqrt(tau))

        return {"delta": delta, "gamma": gamma}
    except Exception as e:
        logger.debug(f"Greeks estimation failed: {e}")
        return {"delta": 0.0, "gamma": 0.0}


# ── Scoring ───────────────────────────────────────────────────

def score_option(
    option_id: str,
    symbol: str,
    option_type: str,
    strike: float,
    bid: float,
    ask: float,
    volume: int,
    open_interest: int,
    iv: float,
    delta: float,
    gamma: float,
    spot: float,
    expiration_date: str,
) -> dict:
    """Score a single 0DTE option candidate on 5 dimensions.

    Returns a scored dict with composite score (0–100) and breakdown.
    """
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else max(bid, ask)
    if mid <= 0 or spot <= 0:
        return {}

    spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 100.0
    distance_pct = abs(strike - spot) / spot * 100

    score_delta = _score_delta(delta)
    score_gamma = _score_gamma(gamma)
    score_volume = _score_volume(volume)
    score_spread = _score_spread(spread_pct)
    score_iv = _score_iv(iv)

    total = round(
        score_delta + score_gamma + score_volume + score_spread + score_iv, 1
    )

    return {
        "option_id": option_id,
        "symbol": symbol,
        "option_type": option_type,
        "strike": round(strike, 2),
        "expiration_date": expiration_date,
        "bid": round(bid, 4),
        "ask": round(ask, 4),
        "mid": round(mid, 4),
        "spread_pct": round(spread_pct, 2),
        "volume": volume,
        "open_interest": open_interest,
        "iv": round(iv, 4),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "distance_pct": round(distance_pct, 3),
        "spot": spot,
        "score": min(total, 100),
        "score_delta": round(score_delta, 1),
        "score_gamma": round(score_gamma, 1),
        "score_volume": round(score_volume, 1),
        "score_spread": round(score_spread, 1),
        "score_iv": round(score_iv, 1),
        "cost_per_contract": round(mid * 100, 2),
    }


def _score_delta(delta: float) -> float:
    """Score delta (0–25). Ideal range: 0.20–0.40 (absolute value)."""
    abs_d = abs(delta)
    if IDEAL_DELTA_LOW <= abs_d <= IDEAL_DELTA_HIGH:
        # Peak at center of ideal range
        center = (IDEAL_DELTA_LOW + IDEAL_DELTA_HIGH) / 2
        dist = abs(abs_d - center) / (IDEAL_DELTA_HIGH - center)
        return W_DELTA * (1.0 - 0.3 * dist)
    elif abs_d < IDEAL_DELTA_LOW:
        # Too far OTM — less directional exposure
        return W_DELTA * max(0, abs_d / IDEAL_DELTA_LOW * 0.7)
    else:
        # Too ITM — expensive, less gamma leverage
        return W_DELTA * max(0, 0.7 - (abs_d - IDEAL_DELTA_HIGH) * 2)


def _score_gamma(gamma: float) -> float:
    """Score gamma (0–25). Higher gamma = more explosive for 0DTE."""
    if gamma >= 0.005:
        return W_GAMMA * 0.8 + W_GAMMA * min(0.2, (gamma - 0.005) * 10)
    elif gamma >= 0.001:
        return W_GAMMA * (0.3 + 0.5 * (gamma - 0.001) / 0.004)
    elif gamma > 0:
        return W_GAMMA * (gamma / 0.001) * 0.3
    else:
        return 0.0


def _score_volume(volume: int) -> float:
    """Score volume (0–20). Higher volume = tighter execution."""
    if volume >= 10000:
        return W_VOLUME * 1.0
    elif volume >= 5000:
        return W_VOLUME * 0.9
    elif volume >= 1000:
        return W_VOLUME * 0.75
    elif volume >= 500:
        return W_VOLUME * 0.6
    elif volume >= 100:
        return W_VOLUME * 0.4
    else:
        return W_VOLUME * 0.1


def _score_spread(spread_pct: float) -> float:
    """Score bid/ask spread (0–20). Narrower = less slippage."""
    if spread_pct <= 2:
        return W_SPREAD * 1.0
    elif spread_pct <= 5:
        return W_SPREAD * 0.8
    elif spread_pct <= 8:
        return W_SPREAD * 0.5
    elif spread_pct <= 10:
        return W_SPREAD * 0.3
    else:
        return W_SPREAD * 0.1


def _score_iv(iv: float) -> float:
    """Score implied volatility (0–10). Moderate IV is best for long options."""
    if IDEAL_IV_LOW <= iv <= IDEAL_IV_HIGH:
        return W_IV * 1.0
    elif iv < IDEAL_IV_LOW:
        # Low IV = cheap but less movement
        return W_IV * (0.5 + 0.5 * max(0, iv) / IDEAL_IV_LOW)
    else:
        # High IV = expensive theta burn
        return W_IV * max(0.2, 1.0 - (iv - IDEAL_IV_HIGH) * 2)


# ── Main Scanner ──────────────────────────────────────────────

def scan_0dte(
    symbols: Optional[list[str]] = None,
    max_candidates: int = TOP_N,
    min_score: float = 30.0,
) -> list[dict]:
    """Scan 0DTE options across configured underlyings.

    Pipeline:
      1. Get option chains for SPY/QQQ
      2. Find today's 0DTE expiration
      3. Fetch option instruments (calls + puts)
      4. Fetch real-time quotes
      5. Filter: price ($0.50–$5.00), volume (>100), spread (<10%)
      6. Score: delta, gamma, volume, spread, IV
      7. Return top N candidates sorted by score

    Returns list of scored candidate dicts.
    """
    if symbols is None:
        symbols = list(UNDERLYINGS)

    today_str = date.today().strftime("%Y-%m-%d")
    all_candidates: list[dict] = []

    for symbol in symbols:
        try:
            spot = get_spot_price(symbol)
            if spot <= 0:
                logger.warning(f"Could not get spot price for {symbol}")
                continue

            exp_date = find_0dte_expiration(symbol)
            if not exp_date:
                logger.info(f"No 0DTE expiration found for {symbol} today")
                continue

            for opt_type in ("call", "put"):
                try:
                    instruments = get_option_instruments(symbol, exp_date, opt_type)
                    if not instruments:
                        continue

                    # Collect instrument IDs for batch quote fetch
                    id_map: dict[str, dict] = {}
                    for inst in instruments:
                        iid = inst.get("id") or inst.get("instrument_id", "")
                        if iid:
                            id_map[iid] = inst

                    if not id_map:
                        continue

                    quotes = get_option_quotes(list(id_map.keys()))
                    quotes_by_id = {
                        q.get("instrument_id") or q.get("id", ""): q
                        for q in quotes if isinstance(q, dict)
                    }

                    for inst_id, inst in id_map.items():
                        quote = quotes_by_id.get(inst_id, {})

                        # Extract fields from instrument + quote (quote overrides)
                        strike = _safe_float(inst, "strike_price", "strike")
                        bid = _safe_float(quote, "bid_price", "bid", default=0.0)
                        ask = _safe_float(quote, "ask_price", "ask", default=0.0)
                        volume = _safe_int(quote, "volume", "today_volume")
                        open_interest = _safe_int(
                            quote, "open_interest", "open_interest_count"
                        )
                        # Extract IV from quote — handle nested greeks dict
                        iv = _safe_float(quote, "implied_volatility", "iv", default=0.0)
                        if iv == 0 and "greeks" in quote and isinstance(quote["greeks"], dict):
                            iv = float(quote["greeks"].get("implied_volatility", 0) or 0)
                        delta = _safe_float(quote, "delta", default=0.0)
                        if delta == 0 and "greeks" in quote and isinstance(quote["greeks"], dict):
                            delta = float(quote["greeks"].get("delta", 0) or 0)
                        gamma = _safe_float(quote, "gamma", default=0.0)
                        if gamma == 0 and "greeks" in quote and isinstance(quote["greeks"], dict):
                            gamma = float(quote["greeks"].get("gamma", 0) or 0)

                        # If Greeks not provided, estimate from Black-Scholes
                        if (delta == 0 or gamma == 0) and spot > 0 and strike > 0:
                            est = _estimate_greeks(spot, strike, iv, opt_type)
                            if delta == 0:
                                delta = est["delta"]
                            if gamma == 0:
                                gamma = est["gamma"]

                        # Filter: price range ($0.50–$5.00 per contract)
                        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else max(bid, ask)
                        if mid < MIN_PREMIUM_MID or mid > MAX_PREMIUM_MID:
                            continue

                        # Filter: minimum volume
                        if volume < config.min_volume_0dte:
                            continue

                        # Filter: max spread (use config threshold)
                        spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 100
                        # Block negative spread (stale/inverted quotes)
                        if spread_pct < 0:
                            spread_pct = 100  # Force max spread for inverted quotes
                        if spread_pct > config.max_spread_pct_0dte * 100:
                            continue

                        # Score the candidate
                        scored = score_option(
                            option_id=inst_id,
                            symbol=symbol,
                            option_type=opt_type,
                            strike=strike,
                            bid=bid,
                            ask=ask,
                            volume=volume,
                            open_interest=open_interest,
                            iv=iv,
                            delta=delta,
                            gamma=gamma,
                            spot=spot,
                            expiration_date=exp_date,
                        )

                        if scored and scored.get("score", 0) >= min_score:
                            all_candidates.append(scored)

                except Exception as e:
                    logger.warning(f"Error scanning {symbol} {opt_type}: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Error scanning {symbol}: {e}")
            continue

    # Sort by score descending, return top N
    all_candidates.sort(key=lambda x: x["score"], reverse=True)
    result = all_candidates[:max_candidates]

    logger.info(
        f"0DTE scan complete: {len(all_candidates)} candidates scored, "
        f"returning top {len(result)}"
    )
    return result


def get_best_0dte_candidate(min_score: float = 30.0) -> Optional[dict]:
    """Get the single best 0DTE candidate across all scanned underlyings.

    Returns None if no candidates meet the minimum score.
    """
    candidates = scan_0dte(min_score=min_score, max_candidates=1)
    return candidates[0] if candidates else None


def format_scan_report(candidates: list[dict]) -> str:
    """Format scan results as a human-readable report."""
    lines = [
        "═══ 0DTE SCAN REPORT ═══",
        f"Scan Time: {datetime.utcnow().isoformat()}",
        f"Underlyings: {', '.join(UNDERLYINGS)}",
        f"Price Range: ${MIN_PREMIUM_MID:.2f}–${MAX_PREMIUM_MID:.2f}/contract",
        f"Candidates Found: {len(candidates)}",
        "",
    ]

    if not candidates:
        lines.append("No candidates found matching filters.")
    else:
        for i, c in enumerate(candidates, 1):
            lines.append(f"#{i} {c['symbol']} {c['option_type'].upper()} K={c['strike']:.1f}")
            lines.append(
                f"   Price: ${c['mid']:.2f} (bid=${c['bid']:.2f} ask=${c['ask']:.2f}) "
                f"Spread: {c['spread_pct']:.1f}%"
            )
            lines.append(
                f"   Volume: {c['volume']:,}  OI: {c['open_interest']:,}"
            )
            lines.append(
                f"   Greeks: Δ={c['delta']:+.4f}  Γ={c['gamma']:.6f}  IV={c['iv']*100:.1f}%"
            )
            lines.append(
                f"   Score: {c['score']:.1f}/100 "
                f"(Δ={c['score_delta']:.1f} Γ={c['score_gamma']:.1f} "
                f"Vol={c['score_volume']:.1f} Spread={c['score_spread']:.1f} "
                f"IV={c['score_iv']:.1f})"
            )
            lines.append(f"   Cost: ${c['cost_per_contract']:.0f}/contract")
            lines.append("")

    return "\n".join(lines)


# ── CLI Entry Point ───────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    results = scan_0dte()
    print(format_scan_report(results))
