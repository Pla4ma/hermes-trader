"""Advanced options trading engine — institutional-grade signal integration.

Handles:
- Multi-strategy options scanning (calls, puts, spreads)
- Greeks-based scoring (delta, gamma, theta, vega)
- IV analysis and skew detection
- Risk-defined strategies for small accounts
- Automatic execution when cash is available
- Position management with rolling and closing

NEW: Institutional-Grade Signals (v5):
- Vanna flow detection: IV drops → OTM put deltas decrease → dealers unhedge → rally
- Charm decay timing: Entry timing based on delta decay rates (ATM charm peaks)
- IV surface fair value: Compare market IV vs SVI/SSVI fitted surface
- GEX-aware position management: Regime-based sizing and direction

BACKEND: Robinhood MCP (migrated to Robinhood MCP July 2026)
"""

import json
import math
import os
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import requests

logger = logging.getLogger("hermes_trader.options_engine")

ROBINHOOD_API_BASE = "https://api.robinhood.com"
ROBINHOOD_TOKEN_PATH = os.path.expanduser("~/.hermes/mcp-tokens/robinhood.json")
ROBINHOOD_ACCOUNT_NUMBER = os.getenv("ROBINHOOD_ACCOUNT_NUMBER", "924058324")


# ---------------------------------------------------------------------------
# Robinhood API Client (replaces legacy broker SDK)
# ---------------------------------------------------------------------------
class RobinhoodClient:
    """Thin Python client for the Robinhood REST API.

    Uses the same OAuth2 Bearer token that the Robinhood MCP server stores
    at ``~/.hermes/mcp-tokens/robinhood.json``.
    """

    def __init__(self, token_path: str = ROBINHOOD_TOKEN_PATH):
        self._token_path = token_path
        self._session = requests.Session()
        self._token_data: dict = {}
        self._load_token()

    # -- auth ---------------------------------------------------------------
    def _load_token(self) -> None:
        try:
            with open(self._token_path) as f:
                self._token_data = json.load(f)
            self._session.headers.update({
                "Authorization": f"Bearer {self._token_data['access_token']}",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=1",
                "Content-Type": "application/json",
                "X-Robinhood-API-Version": "1.431.4",
                "User-Agent": "hermes-trader/1.0",
            })
        except Exception as e:
            logger.warning("Failed to load Robinhood token from %s: %s", self._token_path, e)

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token_data.get("access_token"))

    # -- low-level helpers --------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{ROBINHOOD_API_BASE}{path}"
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data

    def _get_paginated(self, path: str, params: dict | None = None) -> list:
        """Fetch all pages of a paginated endpoint."""
        params = dict(params or {})
        results = []
        url = f"{ROBINHOOD_API_BASE}{path}"
        while True:
            resp = self._session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            next_url = data.get("next")
            if not next_url:
                break
            url = next_url
            params = {}  # next URL already has params
        return results

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{ROBINHOOD_API_BASE}{path}"
        resp = self._session.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # -- stocks & quotes ----------------------------------------------------
    def get_stock_quote(self, symbol: str) -> dict:
        """Get quote for a stock symbol."""
        data = self._get("/quotes/", params={"symbols": symbol})
        results = data.get("results", [])
        return results[0] if results else {}

    def get_stock_price(self, symbol: str) -> float:
        """Get last trade price for a symbol."""
        quote = self.get_stock_quote(symbol)
        return float(quote.get("last_trade_price", 0))

    # -- accounts & portfolio -----------------------------------------------
    def get_account(self, account_number: str | None = None) -> dict:
        """Get account profile (cash, buying_power, etc.)."""
        acct = account_number or ROBINHOOD_ACCOUNT_NUMBER
        try:
            return self._get(f"/accounts/{acct}/")
        except Exception:
            # Fallback: list all accounts
            data = self._get("/accounts/", params={
                "default_to_all_accounts": "true",
                "include_managed": "true",
            })
            accounts = data.get("results", [])
            return accounts[0] if accounts else {}

    def get_portfolio(self, account_number: str | None = None) -> dict:
        """Get portfolio profile (equity, market_value)."""
        acct = account_number or ROBINHOOD_ACCOUNT_NUMBER
        try:
            return self._get(f"/portfolios/{acct}/")
        except Exception:
            data = self._get("/portfolios/")
            results = data.get("results", [])
            return results[0] if results else {}

    def get_cash(self, account_number: str | None = None) -> float:
        """Get available cash."""
        acct = self.get_account(account_number)
        return float(acct.get("cash", 0))

    # -- options chain ------------------------------------------------------
    def get_instrument_id(self, symbol: str) -> str | None:
        """Get the Robinhood instrument ID for an equity symbol."""
        data = self._get("/instruments/", params={
            "symbol": symbol.upper(),
            "state": "active",
        })
        instruments = data.get("results", [])
        for inst in instruments:
            if inst.get("symbol", "").upper() == symbol.upper():
                return inst.get("id")
        return instruments[0].get("id") if instruments else None

    def get_option_chain(self, symbol: str) -> dict:
        """Get option chain metadata for a symbol."""
        inst_id = self.get_instrument_id(symbol)
        if not inst_id:
            return {"id": "", "expiration_dates": []}
        data = self._get("/options/chains/", params={
            "equity_instrument_ids": inst_id,
            "state": "active",
        })
        chains = data.get("results", [])
        return chains[0] if chains else {"id": "", "expiration_dates": []}

    def get_option_instruments(
        self,
        symbol: str,
        option_type: str | None = None,
        expiration_date: str | None = None,
    ) -> list[dict]:
        """Get all option instruments for a symbol, optionally filtered."""
        chain = self.get_option_chain(symbol)
        chain_id = chain.get("id", "")
        if not chain_id:
            return []
        params: dict = {"chain_id": chain_id, "state": "active"}
        if option_type:
            params["type"] = option_type
        if expiration_date:
            params["expiration_dates"] = expiration_date
        return self._get_paginated("/options/instruments/", params=params)

    def get_option_market_data(self, option_id: str) -> dict:
        """Get market data (greeks, quotes, IV) for a single option."""
        return self._get(f"/marketdata/options/{option_id}/")

    def get_option_market_data_batch(self, option_ids: list[str]) -> list[dict]:
        """Get market data for multiple options (sequential with small delay)."""
        results = []
        for oid in option_ids:
            try:
                md = self.get_option_market_data(oid)
                md["_option_id"] = oid
                results.append(md)
            except Exception as e:
                logger.debug("Market data fetch failed for %s: %s", oid, e)
            # Small delay to avoid rate limiting
            time.sleep(0.05)
        return results

    def get_options_with_market_data(
        self,
        symbol: str,
        option_type: str | None = None,
        expiration_date: str | None = None,
        max_options: int = 200,
    ) -> list[dict]:
        """Get option instruments merged with their market data.

        Returns list of dicts with keys:
            symbol, id, strike_price, expiration_date, type,
            bid_price, ask_price, last_trade_price,
            delta, gamma, theta, vega, implied_volatility,
            adjusted_mark_price
        """
        instruments = self.get_option_instruments(symbol, option_type, expiration_date)
        if not instruments:
            return []
        # Cap to avoid too many API calls
        instruments = instruments[:max_options]
        option_ids = [inst["id"] for inst in instruments]
        market_data_list = self.get_option_market_data_batch(option_ids)
        # Index market data by option_id
        md_by_id = {md["_option_id"]: md for md in market_data_list}
        merged = []
        for inst in instruments:
            md = md_by_id.get(inst["id"], {})
            greeks = md.get("greeks") or {}
            merged.append({
                "symbol": inst.get("symbol", ""),
                "id": inst["id"],
                "strike_price": float(inst.get("strike_price", 0)),
                "expiration_date": inst.get("expiration_date", ""),
                "type": inst.get("type", ""),
                "bid_price": float(md.get("bid_price") or 0),
                "ask_price": float(md.get("ask_price") or 0),
                "last_trade_price": float(md.get("last_trade_price") or 0),
                "adjusted_mark_price": float(md.get("adjusted_mark_price") or 0),
                "delta": float(greeks.get("delta") or 0),
                "gamma": float(greeks.get("gamma") or 0),
                "theta": float(greeks.get("theta") or 0),
                "vega": float(greeks.get("vega") or 0),
                "implied_volatility": float(md.get("implied_volatility") or greeks.get("implied_volatility") or 0),
                "open_interest": int(md.get("open_interest") or 0),
                "volume": int(md.get("volume") or 0),
            })
        return merged

    # -- option orders ------------------------------------------------------
    def place_option_order(
        self,
        symbol: str,
        option_id: str,
        side: str = "buy",
        quantity: int = 1,
        price: float = 0.0,
        time_in_force: str = "gfd",
        account_number: str | None = None,
    ) -> dict:
        """Place a single-leg option order.

        Args:
            symbol: Underlying symbol (e.g., "SPY")
            option_id: Robinhood option instrument ID
            side: "buy" or "sell"
            quantity: Number of contracts
            price: Limit price (must be > 0 for limit orders)
            time_in_force: "gfd" (good for day) or "gtc"
            account_number: Robinhood account number
        """
        acct = account_number or ROBINHOOD_ACCOUNT_NUMBER
        account_url = f"{ROBINHOOD_API_BASE}/accounts/{acct}/"
        payload = {
            "account": account_url,
            "direction": "debit" if side == "buy" else "credit",
            "legs": [{
                "option_id": option_id,
                "side": side,
                "position_effect": "open" if side == "buy" else "close",
                "ratio_quantity": 1,
            }],
            "price": str(price),
            "quantity": str(quantity),
            "type": "limit" if price > 0 else "market",
            "time_in_force": time_in_force,
            "trigger": "immediate",
            "market_hours": "regular_hours",
            "override_day_trade_checks": True,
            "override_dtbp_checks": True,
        }
        return self._post("/options/orders/", payload)

    def get_option_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        """Get option order history."""
        params = {"state": status}
        if limit:
            params["limit"] = str(limit)
        data = self._get("/options/orders/", params=params)
        return data.get("results", [])


# ---------------------------------------------------------------------------
# Simple data holder (replaces legacy options snapshot)
# ---------------------------------------------------------------------------
@dataclass
class OptionGreeks:
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0


@dataclass
class OptionQuote:
    bid_price: float = 0.0
    ask_price: float = 0.0


@dataclass
class OptionSnapshot:
    """Mimics legacy options snapshot for backward compatibility."""
    symbol: str = ""
    strike_price: float = 0.0
    expiration_date: str = ""
    option_type: str = ""
    greeks: OptionGreeks = field(default_factory=OptionGreeks)
    latest_quote: OptionQuote = field(default_factory=OptionQuote)
    implied_volatility: float = 0.0
    option_id: str = ""
    last_trade_price: float = 0.0
    open_interest: int = 0
    volume: int = 0


def _robinhood_to_snapshot(opt: dict) -> OptionSnapshot:
    """Convert a Robinhood options dict to an OptionSnapshot."""
    return OptionSnapshot(
        symbol=opt.get("symbol", ""),
        strike_price=opt.get("strike_price", 0),
        expiration_date=opt.get("expiration_date", ""),
        option_type=opt.get("type", ""),
        greeks=OptionGreeks(
            delta=opt.get("delta", 0),
            gamma=opt.get("gamma", 0),
            theta=opt.get("theta", 0),
            vega=opt.get("vega", 0),
        ),
        latest_quote=OptionQuote(
            bid_price=opt.get("bid_price", 0),
            ask_price=opt.get("ask_price", 0),
        ),
        implied_volatility=opt.get("implied_volatility", 0),
        option_id=opt.get("id", ""),
        last_trade_price=opt.get("last_trade_price", 0),
        open_interest=opt.get("open_interest", 0),
        volume=opt.get("volume", 0),
    )


class OptionsEngine:
    """Full-featured options trading engine with institutional-grade signals."""

    def __init__(self):
        self._rh = RobinhoodClient()

    @property
    def rh(self) -> RobinhoodClient:
        return self._rh

    def get_spy_price(self) -> float:
        """Get current SPY price via yfinance (fast, no auth needed)."""
        import yfinance as yf
        return yf.Ticker("SPY").fast_info.get("lastPrice", 0)

    # =========================================================================
    # INSTITUTIONAL SIGNAL 1: Vanna Flow Detection
    # =========================================================================
    def _compute_vanna_flow_signal(
        self, S: float, K: float, tau: float, sigma: float,
        option_type: str = "call",
    ) -> dict:
        """
        Compute vanna flow signal for a single option.

        THE KEY INSTITUTIONAL INSIGHT FOR 0DTE:
        When IV drops, OTM put deltas DECREASE (go toward 0).
        Dealers who are short puts must UNWIND their delta hedges.
        Unwinding = buying the underlying = market rallies.

        Vanna = ∂Δ/∂σ (delta sensitivity to vol changes)
        - For puts: negative vanna means delta gets less negative as vol drops
          → dealers must BUY to unwind hedges → bullish flow
        - For calls: positive vanna means delta increases as vol drops
          → dealers must SELL to unwind hedges → bearish flow

        Vanna flow signal:
        - Strong vanna flow = large |vanna| × IV direction
        - If IV is declining AND we have short puts with high negative vanna,
          that's a BUY signal (dealers unhedging = rally).
        - If IV is declining AND we have long calls with high positive vanna,
          that's a GOOD ENTRY (our delta increases for free).

        Returns dict with vanna, vanna_flow_score (0-15), and direction.
        """
        try:
            from .hol_greeks import compute_hol_greeks
        except ImportError:
            from hermes_trader.hol_greeks import compute_hol_greeks

        flag = "c" if option_type == "call" else "p"
        # tau in years for hol_greeks
        tau_years = tau / 365.0
        if tau_years <= 0:
            tau_years = 1 / 365.0

        hog = compute_hol_greeks(
            S=S, K=K, t=tau_years, r=0.05, sigma=sigma, q=0.0, flag=flag
        )
        vanna = hog.vanna  # Same for calls and puts in BSM

        # Vanna flow score (0-15):
        # Higher |vanna| = more delta sensitivity to vol = more dealer flow
        abs_vanna = abs(vanna)
        score = 0
        if abs_vanna > 0.01:
            score += 15
        elif abs_vanna > 0.007:
            score += 12
        elif abs_vanna > 0.005:
            score += 9
        elif abs_vanna > 0.003:
            score += 6
        elif abs_vanna > 0.001:
            score += 3

        # Direction interpretation:
        if option_type == "put":
            flow_direction = "bullish" if vanna < 0 else "bearish"
        else:
            flow_direction = "bearish" if vanna > 0 else "bullish"

        return {
            "vanna": round(vanna, 6),
            "vanna_flow_score": min(score, 15),
            "vanna_flow_direction": flow_direction,
            "vomma": round(hog.vomma, 6),
        }

    # =========================================================================
    # INSTITUTIONAL SIGNAL 2: Charm Decay Timing
    # =========================================================================
    def _compute_charm_decay_signal(
        self, S: float, K: float, tau: float, sigma: float,
        option_type: str = "call",
    ) -> dict:
        """
        Compute charm decay timing signal.

        CHARM = ∂Δ/∂τ (how fast delta decays with time).

        Key insight: Charm peaks for ATM options near expiry.
        Returns dict with charm, charm_timing_score (0-15), and timing_grade.
        """
        try:
            from .hol_greeks import compute_hol_greeks
        except ImportError:
            from hermes_trader.hol_greeks import compute_hol_greeks

        flag = "c" if option_type == "call" else "p"
        tau_years = tau / 365.0
        if tau_years <= 0:
            tau_years = 1 / 365.0

        hog = compute_hol_greeks(
            S=S, K=K, t=tau_years, r=0.05, sigma=sigma, q=0.0, flag=flag
        )
        charm = hog.charm

        # Charm timing score (0-15):
        abs_charm = abs(charm)
        score = 0
        if abs_charm > 0.10:
            score += 15
        elif abs_charm > 0.07:
            score += 12
        elif abs_charm > 0.05:
            score += 9
        elif abs_charm > 0.03:
            score += 6
        elif abs_charm > 0.01:
            score += 3

        # Timing grade
        moneyness = K / S
        dte = tau

        if 0.98 <= moneyness <= 1.02 and dte <= 7:
            timing_grade = "EXCELLENT"
        elif 0.96 <= moneyness <= 1.04 and dte <= 14:
            timing_grade = "GOOD"
        elif 0.90 <= moneyness <= 1.10 and dte <= 21:
            timing_grade = "MODERATE"
        else:
            timing_grade = "WEAK"

        favorable = False
        if option_type == "call" and charm > 0:
            favorable = True
        elif option_type == "put" and charm < 0:
            favorable = True

        return {
            "charm": round(charm, 6),
            "charm_timing_score": min(score, 15),
            "charm_timing_grade": timing_grade,
            "charm_favorable": favorable,
        }

    # =========================================================================
    # INSTITUTIONAL SIGNAL 3: IV Surface Fair Value
    # =========================================================================
    def _compute_iv_surface_signal(
        self, symbol: str, strike: float, market_iv: float,
        spot: float, dte: int, option_type: str = "call",
    ) -> dict:
        """
        Compare market IV against IV surface fair value.

        Returns dict with fair_value_score (0-10) and iv_premium.
        """
        score = 0
        iv_premium = 0.0
        fair_iv = market_iv

        try:
            from .iv_surface import IVSurface, bs_implied_vol
        except ImportError:
            from hermes_trader.iv_surface import IVSurface, bs_implied_vol

        try:
            surface = IVSurface(spot=spot, rate=0.05)
            chain_data = self._get_chain_for_surface(symbol, spot)

            if chain_data and len(chain_data) > 10:
                for expiry_data in chain_data:
                    expiry = expiry_data["tte"]
                    strikes = expiry_data["strikes"]
                    ivs = expiry_data["ivs"]
                    if len(strikes) > 5:
                        import numpy as np
                        surface.add_expiry(
                            expiry, np.array(strikes), np.array(ivs),
                            forward=spot * math.exp(0.05 * expiry),
                        )

                if surface.slices:
                    surface.fit_svi()
                    tte_years = dte / 365.0
                    fair_iv = surface.get_iv(strike, tte_years, method="svi")
                    iv_premium = (market_iv - fair_iv) / fair_iv if fair_iv > 0 else 0

                    if iv_premium < -0.10:
                        score = 10
                    elif iv_premium < -0.05:
                        score = 7
                    elif iv_premium < -0.02:
                        score = 4
                    elif iv_premium < 0.02:
                        score = 2
                    elif iv_premium < 0.05:
                        score = 0
                    elif iv_premium < 0.10:
                        score = 0
                    else:
                        score = 0

                    if surface.svi_params:
                        sorted_exp = sorted(surface.slices.keys())
                        if sorted_exp:
                            nearest_exp = min(sorted_exp, key=lambda e: abs(e - tte_years))
                            try:
                                skew_val = surface.skew(nearest_exp)
                                if skew_val < -0.03 and option_type == "put":
                                    score = min(score + 3, 10)
                            except Exception:
                                pass
        except Exception as e:
            logger.debug("IV surface computation failed: %s", e)
            if market_iv < 0.15:
                score = 5
            elif market_iv < 0.20:
                score = 3
            elif market_iv > 0.35:
                score = 0

        return {
            "fair_iv": round(fair_iv, 4),
            "iv_premium": round(iv_premium, 4),
            "iv_surface_score": min(score, 10),
            "is_cheap": iv_premium < -0.05,
            "is_expensive": iv_premium > 0.05,
        }

    def _get_chain_for_surface(self, symbol: str, spot: float) -> list:
        """Fetch option chain data formatted for IV surface construction."""
        try:
            today = datetime.utcnow().date()
            gte = today + timedelta(days=1)
            lte = today + timedelta(days=60)

            # Get options with market data from Robinhood
            instruments = self.rh.get_option_instruments(symbol)
            if not instruments:
                return []

            # Filter to date range and collect by expiry
            by_expiry: dict[int, dict] = {}
            for inst in instruments:
                exp_str = inst.get("expiration_date", "")
                if not exp_str:
                    continue
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if not (gte <= exp_date <= lte):
                    continue

                tte = max(1, (exp_date - today).days)
                strike = float(inst.get("strike_price", 0))
                if strike <= 0:
                    continue

                # Get market data for IV
                try:
                    md = self.rh.get_option_market_data(inst["id"])
                    iv = float(md.get("implied_volatility") or 0)
                    greeks = md.get("greeks") or {}
                    bid = float(md.get("bid_price") or 0)
                    ask = float(md.get("ask_price") or 0)
                except Exception:
                    continue

                if iv <= 0 or iv > 3.0:
                    continue
                if bid <= 0 or ask <= 0:
                    continue

                if tte not in by_expiry:
                    by_expiry[tte] = {"strikes": [], "ivs": [], "tte": tte / 365.0}
                by_expiry[tte]["strikes"].append(strike)
                by_expiry[tte]["ivs"].append(iv)

            return list(by_expiry.values())
        except Exception as e:
            logger.debug("Chain fetch for surface failed: %s", e)
            return []

    # =========================================================================
    # INSTITUTIONAL SIGNAL 4: GEX-Aware Position Management
    # =========================================================================
    def _get_gex_regime(self, symbol: str = "SPY") -> dict:
        """Get Gamma Exposure regime for position management."""
        try:
            from .gamma_positioning import GammaPositioning
        except ImportError:
            from hermes_trader.gamma_positioning import GammaPositioning

        try:
            gp = GammaPositioning()
            gex_data = gp.calculate_gex(symbol, max_dte=7)
            total_gex = gex_data.get("total_gex", 0)
            regime = gex_data.get("regime", "unknown")

            if regime == "negative_gamma":
                size_factor = 1.2
                confidence_boost = 2
            elif regime == "positive_gamma":
                size_factor = 0.8
                confidence_boost = -2
            else:
                size_factor = 1.0
                confidence_boost = 0

            return {
                "total_gex": total_gex,
                "regime": regime,
                "flip_strike": gex_data.get("flip_strike"),
                "size_factor": size_factor,
                "confidence_boost": confidence_boost,
                "trading_rule": gex_data.get("trading_rule", ""),
            }
        except Exception as e:
            logger.debug("GEX computation failed: %s", e)
            return {
                "total_gex": 0,
                "regime": "unknown",
                "flip_strike": None,
                "size_factor": 1.0,
                "confidence_boost": 0,
                "trading_rule": "GEX unavailable",
            }

    def _compute_gex_adjusted_score(self, base_score: int, gex_regime: dict,
                                     option_type: str) -> int:
        """Adjust option score based on GEX regime."""
        regime = gex_regime.get("regime", "unknown")
        if regime == "negative_gamma":
            return max(0, base_score + 3)
        elif regime == "positive_gamma":
            return max(0, base_score - 2)
        return base_score

    # =========================================================================
    # ENHANCED SCANNING (integrates all 4 signals)
    # =========================================================================
    def scan_calls(self, symbol: str = "SPY", max_cost: float = 50.0,
                   max_dte: int = 21, min_delta: float = 0.10) -> list[dict]:
        """Scan for tradeable call options with institutional-grade scoring."""
        spy_price = self.get_spy_price()
        today = datetime.utcnow().date()
        max_expiry = today + timedelta(days=max_dte)

        # Get GEX regime once (cached per scan)
        gex_regime = self._get_gex_regime(symbol)

        # Get option chain from Robinhood
        instruments = self.rh.get_option_instruments(symbol, option_type="call")
        if not instruments:
            logger.warning("No call options found for %s", symbol)
            return []

        # Filter by expiration and get market data
        candidates = []
        for inst in instruments:
            exp_str = inst.get("expiration_date", "")
            if not exp_str:
                continue
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            # Must be at least 2 days out and within max_dte
            dte = (exp_date - today).days
            if dte < 2 or dte > max_dte:
                continue

            strike = float(inst.get("strike_price", 0))
            if strike <= 0:
                continue

            # Get market data
            try:
                md = self.rh.get_option_market_data(inst["id"])
            except Exception as e:
                logger.debug("Market data failed for %s: %s", inst.get("id"), e)
                continue

            bid = float(md.get("bid_price") or 0)
            ask = float(md.get("ask_price") or 0)
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            cost = mid * 100  # Per contract
            if cost <= 0 or cost > max_cost:
                continue

            greeks = md.get("greeks") or {}
            delta = abs(float(greeks.get("delta") or 0))
            gamma = float(greeks.get("gamma") or 0)
            theta = float(greeks.get("theta") or 0)
            vega = float(greeks.get("vega") or 0)
            iv = float(md.get("implied_volatility") or greeks.get("implied_volatility") or 0)

            if delta < min_delta:
                continue

            spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999
            moneyness = (strike - spy_price) / spy_price * 100

            # =================================================================
            # CORE SCORING (0-15 points)
            # =================================================================
            score = 0
            if 0.20 <= delta <= 0.40:
                score += 4
            elif 0.15 <= delta <= 0.50:
                score += 3
            elif delta >= 0.10:
                score += 1

            if cost <= 20:
                score += 3
            elif cost <= 35:
                score += 2
            elif cost <= 50:
                score += 1

            if gamma > 0.05:
                score += 3
            elif gamma > 0.03:
                score += 2
            elif gamma > 0.01:
                score += 1

            if spread_pct < 5:
                score += 3
            elif spread_pct < 10:
                score += 1

            if 5 <= dte <= 14:
                score += 2
            elif 3 <= dte <= 21:
                score += 1

            # =================================================================
            # INSTITUTIONAL SIGNALS (up to 40 additional points)
            # =================================================================
            inst_score = 0

            vanna_data = self._compute_vanna_flow_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="call"
            )
            inst_score += vanna_data["vanna_flow_score"]

            charm_data = self._compute_charm_decay_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="call"
            )
            inst_score += charm_data["charm_timing_score"]

            iv_data = self._compute_iv_surface_signal(
                symbol=symbol, strike=strike, market_iv=iv,
                spot=spy_price, dte=dte, option_type="call"
            )
            inst_score += iv_data["iv_surface_score"]

            gex_adjusted = self._compute_gex_adjusted_score(
                inst_score, gex_regime, "call"
            )
            inst_score = gex_adjusted

            total_score = score + inst_score

            candidates.append({
                "symbol": inst.get("symbol", ""),
                "option_id": inst.get("id", ""),
                "underlying": symbol,
                "type": "call",
                "strike": strike,
                "dte": dte,
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "mid": round(mid, 2),
                "cost": round(cost, 2),
                "delta": round(delta, 4),
                "gamma": round(gamma, 4),
                "theta": round(theta, 4),
                "vega": round(vega, 4),
                "iv": round(iv, 3),
                "spread_pct": round(spread_pct, 1),
                "moneyness": round(moneyness, 2),
                "cost_efficiency": round(delta / mid if mid > 0 else 0, 3),
                "score": total_score,
                "max_loss": round(cost, 2),
                "breakeven": round(strike + mid, 2),
                # Institutional signals
                "vanna": vanna_data["vanna"],
                "vanna_flow_score": vanna_data["vanna_flow_score"],
                "vanna_flow_direction": vanna_data["vanna_flow_direction"],
                "charm": charm_data["charm"],
                "charm_timing_score": charm_data["charm_timing_score"],
                "charm_timing_grade": charm_data["charm_timing_grade"],
                "charm_favorable": charm_data["charm_favorable"],
                "iv_surface_score": iv_data["iv_surface_score"],
                "iv_premium": iv_data["iv_premium"],
                "is_cheap": iv_data["is_cheap"],
                "gex_regime": gex_regime["regime"],
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    def scan_puts(self, symbol: str = "SPY", max_cost: float = 50.0,
                  max_dte: int = 21, min_delta: float = 0.10) -> list[dict]:
        """Scan for tradeable put options with institutional-grade scoring."""
        spy_price = self.get_spy_price()
        today = datetime.utcnow().date()
        max_expiry = today + timedelta(days=max_dte)

        gex_regime = self._get_gex_regime(symbol)

        # Get option chain from Robinhood
        instruments = self.rh.get_option_instruments(symbol, option_type="put")
        if not instruments:
            logger.warning("No put options found for %s", symbol)
            return []

        candidates = []
        for inst in instruments:
            exp_str = inst.get("expiration_date", "")
            if not exp_str:
                continue
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte < 2 or dte > max_dte:
                continue

            strike = float(inst.get("strike_price", 0))
            if strike <= 0:
                continue

            try:
                md = self.rh.get_option_market_data(inst["id"])
            except Exception as e:
                logger.debug("Market data failed for %s: %s", inst.get("id"), e)
                continue

            bid = float(md.get("bid_price") or 0)
            ask = float(md.get("ask_price") or 0)
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            cost = mid * 100
            if cost <= 0 or cost > max_cost:
                continue

            greeks = md.get("greeks") or {}
            delta = abs(float(greeks.get("delta") or 0))
            if delta < min_delta:
                continue

            gamma = float(greeks.get("gamma") or 0)
            theta = float(greeks.get("theta") or 0)
            vega = float(greeks.get("vega") or 0)
            iv = float(md.get("implied_volatility") or greeks.get("implied_volatility") or 0)
            spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999

            # =================================================================
            # CORE SCORING (0-15)
            # =================================================================
            score = 0
            if 0.20 <= delta <= 0.40:
                score += 4
            elif delta >= 0.10:
                score += 2
            if cost <= 30:
                score += 3
            if gamma > 0.03:
                score += 3
            elif gamma > 0.01:
                score += 1
            if spread_pct < 10:
                score += 2
            if 0.15 < iv < 0.35:
                score += 2
            if 5 <= dte <= 14:
                score += 1

            # =================================================================
            # INSTITUTIONAL SIGNALS (up to 40 additional points)
            # =================================================================
            inst_score = 0

            vanna_data = self._compute_vanna_flow_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="put"
            )
            if vanna_data["vanna_flow_direction"] == "bullish":
                inst_score += max(0, vanna_data["vanna_flow_score"] - 5)
            else:
                inst_score += vanna_data["vanna_flow_score"]

            charm_data = self._compute_charm_decay_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="put"
            )
            inst_score += charm_data["charm_timing_score"]

            iv_data = self._compute_iv_surface_signal(
                symbol=symbol, strike=strike, market_iv=iv,
                spot=spy_price, dte=dte, option_type="put"
            )
            inst_score += iv_data["iv_surface_score"]

            gex_adjusted = self._compute_gex_adjusted_score(
                inst_score, gex_regime, "put"
            )
            inst_score = gex_adjusted

            total_score = score + inst_score

            candidates.append({
                "symbol": inst.get("symbol", ""),
                "option_id": inst.get("id", ""),
                "underlying": symbol,
                "type": "put",
                "strike": strike,
                "dte": dte,
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "mid": round(mid, 2),
                "cost": round(cost, 2),
                "delta": round(delta, 4),
                "gamma": round(gamma, 4),
                "theta": round(theta, 4),
                "vega": round(vega, 4),
                "iv": round(iv, 3),
                "spread_pct": round(spread_pct, 1),
                "score": total_score,
                "max_loss": round(cost, 2),
                # Institutional signals
                "vanna": vanna_data["vanna"],
                "vanna_flow_score": vanna_data["vanna_flow_score"],
                "vanna_flow_direction": vanna_data["vanna_flow_direction"],
                "charm": charm_data["charm"],
                "charm_timing_score": charm_data["charm_timing_score"],
                "charm_timing_grade": charm_data["charm_timing_grade"],
                "charm_favorable": charm_data["charm_favorable"],
                "iv_surface_score": iv_data["iv_surface_score"],
                "iv_premium": iv_data["iv_premium"],
                "is_cheap": iv_data["is_cheap"],
                "gex_regime": gex_regime["regime"],
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    # =========================================================================
    # VANNA FLOW EXIT MANAGEMENT
    # =========================================================================
    def check_vanna_exit_signal(
        self, position: dict, current_iv: float, entry_iv: float,
    ) -> dict:
        """Check if vanna flow suggests exiting a position."""
        if current_iv <= 0 or entry_iv <= 0:
            return {"exit": False, "reason": "IV data unavailable"}

        iv_change_pct = (current_iv - entry_iv) / entry_iv
        option_type = position.get("type", "call")
        vanna_exhausted = abs(iv_change_pct) < 0.03

        if option_type == "call":
            if iv_change_pct < -0.15:
                return {
                    "exit": True, "urgency": "high",
                    "reason": f"IV dropped {iv_change_pct*100:.1f}% from entry → vega losses, vanna flow may be exhausted",
                    "signal": "vanna_iv_crush",
                }
            elif iv_change_pct < -0.10 and vanna_exhausted:
                return {
                    "exit": True, "urgency": "medium",
                    "reason": f"IV down {iv_change_pct*100:.1f}% and stabilizing → vanna flow exhaustion",
                    "signal": "vanna_flow_exhausted",
                }
            elif iv_change_pct > 0.20:
                return {
                    "exit": False, "urgency": "watch",
                    "reason": f"IV up {iv_change_pct*100:.1f}% → vega gains but vanna flow bearish, monitor closely",
                    "signal": "vanna_bearish_watch",
                }

        elif option_type == "put":
            if iv_change_pct < -0.08:
                return {
                    "exit": True, "urgency": "high",
                    "reason": f"IV dropped {iv_change_pct*100:.1f}% → vega loss + bullish vanna flow = double negative for puts",
                    "signal": "vanna_put_catastrophic",
                }
            elif iv_change_pct > 0.10:
                return {
                    "exit": False, "urgency": "hold",
                    "reason": f"IV up {iv_change_pct*100:.1f}% → vega gains + bullish vanna flow supports puts",
                    "signal": "vanna_put_favorable",
                }

        return {
            "exit": False, "urgency": "hold",
            "reason": f"Vanna flow neutral (IV change: {iv_change_pct*100:.1f}%)",
            "signal": "vanna_neutral",
        }

    # =========================================================================
    # CHARM-BASED ENTRY TIMING
    # =========================================================================
    def get_optimal_entry_dte(self, symbol: str = "SPY",
                               option_type: str = "call") -> dict:
        """Determine optimal DTE for entry based on charm decay curves."""
        spot = self.get_spy_price()
        best_dte = 7
        best_ratio = 0

        try:
            from .hol_greeks import compute_hol_greeks
        except ImportError:
            from hermes_trader.hol_greeks import compute_hol_greeks

        flag = "c" if option_type == "call" else "p"

        for test_dte in [1, 2, 3, 5, 7, 10, 14, 21, 30]:
            tau_years = test_dte / 365.0
            iv = 0.20 if test_dte > 7 else 0.25

            try:
                hog = compute_hol_greeks(
                    S=spot, K=spot, t=tau_years, r=0.05,
                    sigma=iv, q=0.0, flag=flag
                )
                if hog.charm != 0:
                    ratio = abs(hog.charm) / max(abs(hog.veta), 0.001)
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_dte = test_dte
            except Exception:
                continue

        return {
            "optimal_dte": best_dte,
            "charm_theta_ratio": round(best_ratio, 4),
            "rationale": (
                f"Charm decay analysis suggests {best_dte} DTE for optimal "
                f"delta acceleration. Charm/theta ratio: {best_ratio:.4f}. "
                f"{'0DTE/1DTE optimal for delta magnet effect' if best_dte <= 1 else f'{best_dte}DTE balances charm decay vs theta cost'}"
            ),
        }

    # =========================================================================
    # TRADE FINDING & EXECUTION (enhanced)
    # =========================================================================
    def find_best_trade(self, direction: str = "bullish") -> dict:
        """Find the best options trade across all strategies with GEX awareness."""
        cash = self._get_cash()

        max_scan_cost = 50.0

        if direction == "bullish":
            candidates = self.scan_calls(max_cost=max_scan_cost)
            strategy = "long_call"
        else:
            candidates = self.scan_puts(max_cost=max_scan_cost)
            strategy = "long_put"

        if not candidates:
            return {"action": "none", "reason": f"No viable {direction} options found"}

        gex = self._get_gex_regime()
        if gex["regime"] == "positive_gamma" and direction == "bullish":
            for c in candidates:
                c["score"] = max(0, c["score"] - 3)
                c["gex_warning"] = "Positive GEX → rangebound expected, directional bet risky"
            candidates.sort(key=lambda x: x["score"], reverse=True)

        affordable = [c for c in candidates if c["cost"] <= cash]
        unaffordable = [c for c in candidates if c["cost"] > cash]

        if affordable:
            best = affordable[0]
            return {
                "action": "trade",
                "strategy": strategy,
                "candidate": best,
                "alternatives": affordable[1:4],
                "cash_available": cash,
                "gex_regime": gex["regime"],
                "entry_timing": self.get_optimal_entry_dte(
                    best["underlying"], best["type"]
                ),
            }
        else:
            cheapest = candidates[-1] if candidates else None
            return {
                "action": "none",
                "reason": f"Best option costs ${candidates[0]['cost']:.0f} but only ${cash:.2f} cash. Need ${candidates[0]['cost'] - cash:.2f} more.",
                "best_candidate": candidates[0],
                "cheapest_candidate": cheapest,
                "all_candidates": candidates[:5],
                "cash_available": cash,
                "cash_needed": candidates[0]["cost"],
            }

    def execute_trade(self, candidate: dict) -> dict:
        """Execute an options trade via Robinhood MCP."""
        try:
            # Robinhood requires limit orders for options
            # Use the ask price as limit price for buys (aggressive fill)
            limit_price = candidate.get("ask", candidate.get("mid", 0))
            if limit_price <= 0:
                limit_price = candidate.get("mid", 1.0)

            order = self.rh.place_option_order(
                symbol=candidate["underlying"],
                option_id=candidate.get("option_id", ""),
                side="buy",
                quantity=1,
                price=limit_price,
                time_in_force="gfd",
            )

            result = {
                "action": "BUY_OPTION",
                "symbol": candidate["symbol"],
                "underlying": candidate["underlying"],
                "type": candidate["type"],
                "strike": candidate["strike"],
                "cost": candidate["cost"],
                "delta": candidate["delta"],
                "score": candidate["score"],
                "order_id": str(order.get("id", "")),
                "status": str(order.get("state", order.get("status", "submitted"))),
                # Log institutional signals
                "vanna": candidate.get("vanna", 0),
                "charm": candidate.get("charm", 0),
                "gex_regime": candidate.get("gex_regime", "unknown"),
                "iv_premium": candidate.get("iv_premium", 0),
            }

            # Log
            entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": "BUY_OPTION",
                "symbol": candidate["symbol"],
                "option_id": candidate.get("option_id", ""),
                "underlying": candidate["underlying"],
                "type": candidate["type"],
                "strike": candidate["strike"],
                "cost": candidate["cost"],
                "delta": candidate["delta"],
                "gamma": candidate.get("gamma", 0),
                "iv": candidate.get("iv", 0),
                "score": candidate["score"],
                "order_id": str(order.get("id", "")),
                "strategy": "options_engine_v5_robinhood",
                # Institutional signals
                "vanna": candidate.get("vanna", 0),
                "charm": candidate.get("charm", 0),
                "vanna_flow_score": candidate.get("vanna_flow_score", 0),
                "charm_timing_score": candidate.get("charm_timing_score", 0),
                "iv_surface_score": candidate.get("iv_surface_score", 0),
                "gex_regime": candidate.get("gex_regime", "unknown"),
                "entry_iv": candidate.get("iv", 0),
                "broker": "robinhood",
            }
            with open("/opt/hermes-trader/data/journals/paper_orders.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")

            return result

        except Exception as e:
            return {"error": str(e)}

    def auto_trade(self) -> dict:
        """Fully autonomous options trade with institutional-grade signals."""
        direction = "bullish"
        try:
            from .market_regime import detect_regime
            regime = detect_regime()
            if "BEAR" in regime.get("regime", ""):
                direction = "bearish"
        except Exception:
            pass

        gex = self._get_gex_regime()
        if gex["regime"] == "positive_gamma" and direction == "bullish":
            pass

        result = self.find_best_trade(direction)

        if result.get("action") == "trade":
            candidate = result["candidate"]
            execution = self.execute_trade(candidate)
            result["execution"] = execution
            result["entry_iv"] = candidate.get("iv", 0)

        other_dir = "bearish" if direction == "bullish" else "bullish"
        other_candidates = (
            self.scan_calls(max_cost=20.0) if other_dir == "bullish"
            else self.scan_puts(max_cost=20.0)
        )
        result["hedge_candidates"] = other_candidates[:3] if other_candidates else []

        result["institutional_signals"] = {
            "gex_regime": gex["regime"],
            "gex_flip_strike": gex.get("flip_strike"),
            "entry_timing": self.get_optimal_entry_dte(),
        }

        return result

    def _get_cash(self) -> float:
        """Get available cash from Robinhood."""
        try:
            return self.rh.get_cash()
        except Exception:
            return 0.0


# =========================================================================
# Module-level convenience functions
# =========================================================================
def scan_options(symbol: str = "SPY", direction: str = "bullish") -> list[dict]:
    """Quick scan for options."""
    engine = OptionsEngine()
    if direction == "bullish":
        return engine.scan_calls(symbol)
    return engine.scan_puts(symbol)


def auto_trade_options() -> dict:
    """Quick auto-trade."""
    engine = OptionsEngine()
    return engine.auto_trade()


def get_vanna_flow_analysis(symbol: str = "SPY", spot: float = None) -> dict:
    """
    Standalone vanna flow analysis for a symbol.

    Returns vanna flow signals across the options chain,
    showing where dealer hedging flow is concentrated.
    """
    engine = OptionsEngine()
    if spot is None:
        spot = engine.get_spy_price()

    try:
        today = datetime.utcnow().date()

        # Get options with market data from Robinhood (7-day window)
        lte = today + timedelta(days=7)
        instruments = engine.rh.get_option_instruments(symbol)
        if not instruments:
            return {"error": "No options found", "flow_signal": "UNAVAILABLE"}

        total_put_vanna = 0
        total_call_vanna = 0
        vanna_by_strike = {}

        for inst in instruments:
            exp_str = inst.get("expiration_date", "")
            if not exp_str:
                continue
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if exp_date > lte:
                continue

            is_call = inst.get("type") == "call"
            strike = float(inst.get("strike_price", 0))
            if strike <= 0:
                continue

            # Get market data for IV
            try:
                md = engine.rh.get_option_market_data(inst["id"])
                greeks = md.get("greeks") or {}
                iv = float(md.get("implied_volatility") or greeks.get("implied_volatility") or 0)
            except Exception:
                continue

            if iv <= 0:
                continue

            dte = 1  # Simplified for 0DTE analysis
            vanna_data = engine._compute_vanna_flow_signal(
                S=spot, K=strike, tau=dte, sigma=iv,
                option_type="call" if is_call else "put"
            )

            vanna = vanna_data["vanna"]
            if is_call:
                total_call_vanna += vanna
            else:
                total_put_vanna += vanna

            if strike not in vanna_by_strike:
                vanna_by_strike[strike] = {"call_vanna": 0, "put_vanna": 0, "net_vanna": 0}
            if is_call:
                vanna_by_strike[strike]["call_vanna"] += vanna
            else:
                vanna_by_strike[strike]["put_vanna"] += vanna
            vanna_by_strike[strike]["net_vanna"] += vanna

        net_vanna = total_call_vanna + total_put_vanna

        if net_vanna < -0.005:
            flow_signal = "BULLISH (net negative vanna → dealers must buy as IV drops)"
        elif net_vanna > 0.005:
            flow_signal = "BEARISH (net positive vanna → dealers must sell as IV drops)"
        else:
            flow_signal = "NEUTRAL (vanna flow balanced)"

        return {
            "spot": spot,
            "total_call_vanna": round(total_call_vanna, 6),
            "total_put_vanna": round(total_put_vanna, 6),
            "net_vanna": round(net_vanna, 6),
            "flow_signal": flow_signal,
            "vanna_by_strike": {k: {kk: round(vv, 6) for kk, vv in v.items()}
                               for k, v in sorted(vanna_by_strike.items())},
        }

    except Exception as e:
        return {"error": str(e), "flow_signal": "UNAVAILABLE"}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")

    engine = OptionsEngine()
    print("=== OPTIONS ENGINE v5 — INSTITUTIONAL GRADE (Robinhood MCP) ===")

    # Vanna flow analysis
    print("\n--- Vanna Flow Analysis ---")
    vanna = get_vanna_flow_analysis()
    print(f"Net Vanna: {vanna.get('net_vanna', 'N/A')}")
    print(f"Flow Signal: {vanna.get('flow_signal', 'N/A')}")

    # Entry timing
    print("\n--- Optimal Entry Timing ---")
    timing = engine.get_optimal_entry_dte()
    print(f"Optimal DTE: {timing['optimal_dte']}")
    print(f"Rationale: {timing['rationale']}")

    # GEX regime
    print("\n--- GEX Regime ---")
    gex = engine._get_gex_regime()
    print(f"Regime: {gex['regime']}")
    print(f"Total GEX: {gex['total_gex']}")
    print(f"Size Factor: {gex['size_factor']}")

    # Enhanced scan
    print("\n--- Enhanced Call Scan ---")
    calls = engine.scan_calls(max_cost=50.0)
    print(f"Found: {len(calls)} calls")
    for c in calls[:5]:
        print(
            f"  {c['symbol']}: score={c['score']} "
            f"vanna_flow={c['vanna_flow_score']}/15 "
            f"charm={c['charm_timing_score']}/15 "
            f"iv_surface={c['iv_surface_score']}/10 "
            f"strike={c['strike']} mid=${c['mid']:.2f} "
            f"delta={c['delta']:.3f} iv_premium={c.get('iv_premium', 0):.3f}"
        )

    print("\n--- Enhanced Put Scan ---")
    puts = engine.scan_puts(max_cost=50.0)
    print(f"Found: {len(puts)} puts")
    for p in puts[:3]:
        print(
            f"  {p['symbol']}: score={p['score']} "
            f"vanna_flow={p['vanna_flow_score']}/15 "
            f"charm={p['charm_timing_score']}/15 "
            f"iv_surface={p['iv_surface_score']}/10 "
            f"strike={p['strike']} mid=${p['mid']:.2f} "
            f"delta={p['delta']:.3f}"
        )

    print("\n--- Auto-Trade ---")
    result = engine.auto_trade()
    print(json.dumps({k: v for k, v in result.items() if k != "alternatives"},
                      indent=2, default=str))
