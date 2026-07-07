"""Dealer Positioning — real-time gamma exposure & dealer flow analysis.

Tracks where dealers are positioned by analysing the live options chain.
Key concepts:

  * Gamma Exposure (GEX)        – per-strike and aggregate net GEX.
  * Gamma Flip                  – strike where cumulative GEX changes sign
                                  (positive-GEX above, negative-GEX below, or
                                  vice-versa).  A move through this level can
                                  accelerate a trend.
  * Gamma Squeeze               – self-reinforcing sell/buy pressure when
                                  dealers are short gamma and the market moves
                                  against them.
  * Call / Put Walls            – strikes with outsized open interest that act
                                  as dealer hedging magnets (resistance /
                                  support).
  * Expected Move               – implied move derived from ATM straddle
                                  pricing.

Formulas
--------
  GEX = Gamma × Spot² × OI × 100 × multiplier
  (multiplier = 100 for equity options, 1 for index options like SPX)

All data is fetched via *yfinance*.  Greeks are computed with the
Black-Scholes model exposed through ``greeks_engine.BlackScholesGreeks``.

Usage
-----
    from hermes_trader.dealer_positioning import DealerPositioning

    dp = DealerPositioning()
    report = dp.full_analysis("SPY")
"""

from __future__ import annotations

import math
import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("hermes_trader.dealer_positioning")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_RISK_FREE_RATE = 0.052          # ~5.2 % as of mid-2024
DEFAULT_DIVIDEND_YIELD = 0.013          # SPY ≈ 1.3 %
EQUITY_MULTIPLIER = 100                 # 100 shares per contract
INDEX_MULTIPLIER = 1                    # cash-settled index options
SPREAD_ESTIMATE_PCT = 0.02              # fallback mid-price estimate


# ---------------------------------------------------------------------------
# Data classes – structured, serialisable return values
# ---------------------------------------------------------------------------
@dataclass
class GEXStrike:
    """Net gamma exposure at a single strike."""
    strike: float
    call_gex: float
    put_gex: float
    total_gex: float
    call_oi: int
    put_oi: int
    total_oi: int
    pct_of_total: float                 # % of aggregate |GEX|
    is_flip: bool = False               # True at the gamma-flip strike


@dataclass
class GammaFlip:
    """Details about the gamma-flip point."""
    strike: float
    cumulative_gex_below: float
    cumulative_gex_above: float
    regime: str                         # "positive_gamma" | "negative_gamma"
    spot: float
    distance_from_spot: float           # absolute dollar distance
    distance_pct: float                 # % of spot
    description: str
    trading_implication: str


@dataclass
class GammaSqueeze:
    """Gamma squeeze detection result."""
    detected: bool
    squeeze_direction: str              # "long_squeeze" | "short_squeeze" | "none"
    dealers_gamma_sign: str             # "long" | "short" | "neutral"
    magnitude: float                    # |aggregate GEX| / spot (normalised)
    velocity: float                     # estimated acceleration factor
    risk_level: str                     # "extreme" | "high" | "moderate" | "low"
    description: str
    action: str


@dataclass
class Wall:
    """A call or put wall identified by the module."""
    strike: float
    oi: int
    gex: float
    distance_from_spot: float           # $ distance
    distance_pct: float                 # % of spot
    strength: str                       # "strong" | "moderate" | "weak"
    role: str                           # "resistance" (call) | "support" (put)
    bid_ask_spread: float
    is_near_atm: bool


@dataclass
class ExpectedMove:
    """Implied expected move from straddle pricing."""
    expected_move_dollars: float
    expected_move_pct: float
    expected_move_up: float
    expected_move_down: float
    straddle_price: float
    atm_strike: float
    spot: float
    dte: int
    iv_atm: float
    description: str


@dataclass
class DealerPositionReport:
    """Full dealer-positioning report."""
    symbol: str
    spot: float
    timestamp: str
    gex: List[GEXStrike]
    aggregate_gex: float
    gamma_flip: GammaFlip
    gamma_squeeze: GammaSqueeze
    call_walls: List[Wall]
    put_walls: List[Wall]
    expected_move: ExpectedMove
    regime_summary: str
    trading_rule: str


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def _safe_int(val, default: int = 0) -> int:
    """Safely convert an option-chain value to int."""
    try:
        v = float(val) if val is not None else default
        return int(v) if not math.isnan(v) and not math.isinf(v) else default
    except (ValueError, TypeError):
        return default


def _safe_float(val, default: float = 0.0) -> float:
    try:
        v = float(val) if val is not None else default
        return v if not math.isnan(v) and not math.isinf(v) else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------
class DealerPositioning:
    """Real-time dealer positioning engine.

    Pulls live options-chain data from yfinance, computes Greeks via
    Black-Scholes, and derives dealer-facing metrics.
    """

    def __init__(
        self,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
        dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
    ):
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield
        self._chain_cache: Dict[str, list] = {}
        self._spot_cache: Dict[str, float] = {}

    # -----------------------------------------------------------------------
    # Data fetching
    # -----------------------------------------------------------------------
    def get_spot_price(self, symbol: str = "SPY") -> float:
        """Get current spot price from yfinance."""
        if symbol in self._spot_cache:
            return self._spot_cache[symbol]
        try:
            import yfinance as yf
            spot = yf.Ticker(symbol).fast_info.get("lastPrice", 0)
            self._spot_cache[symbol] = float(spot)
            return self._spot_cache[symbol]
        except Exception as exc:
            logger.warning("Failed to fetch spot for %s: %s", symbol, exc)
            return 0.0

    def _is_index(self, symbol: str) -> bool:
        """Return True for cash-settled index options (SPX, NDX, RUT, …)."""
        return symbol.upper() in {"SPX", "NDX", "RUT", "DJX", "VIX", "SPXW"}

    def _option_multiplier(self, symbol: str) -> int:
        return INDEX_MULTIPLIER if self._is_index(symbol) else EQUITY_MULTIPLIER

    def _fetch_option_chain(self, symbol: str, max_dte: int = 45) -> list:
        """Fetch the full option chain for *symbol* up to *max_dte* days.

        Returns a list of dicts with keys:
            symbol, strike, is_call, bid, ask, volume, open_interest,
            gamma, iv, delta, vega, theta, dte, mid_price
        """
        cache_key = f"{symbol}_{max_dte}"
        if cache_key in self._chain_cache:
            return self._chain_cache[cache_key]

        import yfinance as yf
        from .greeks_engine import BlackScholesGreeks as BSG

        ticker = yf.Ticker(symbol)
        spot = self.get_spot_price(symbol)
        today = date.today()
        chain: list = []

        for exp_str in ticker.options:
            try:
                exp_date = date.fromisoformat(exp_str)
                dte = (exp_date - today).days
            except Exception:
                continue

            if dte > max_dte or dte < 1:
                continue

            try:
                opt = ticker.option_chain(exp_str)
            except Exception as exc:
                logger.debug("Skipping exp %s: %s", exp_str, exc)
                continue

            tau = max(dte, 1) / 365.0
            r = self.risk_free_rate
            q = self.dividend_yield

            for side_df, is_call in [(opt.calls, True), (opt.puts, False)]:
                for _, row in side_df.iterrows():
                    iv = _safe_float(row.get("impliedVolatility", 0))
                    bid = _safe_float(row.get("bid", 0))
                    ask = _safe_float(row.get("ask", 0))
                    last_price = _safe_float(row.get("lastPrice", 0))
                    mid_price = (bid + ask) / 2.0 if bid > 0 and ask > 0 else last_price

                    # Fallback for illiquid / 0-DTE chains
                    if bid <= 0 or ask <= 0:
                        if last_price > 0:
                            bid = last_price * (1 - SPREAD_ESTIMATE_PCT)
                            ask = last_price * (1 + SPREAD_ESTIMATE_PCT)
                            mid_price = last_price
                        else:
                            continue

                    strike = _safe_float(row.get("strike", 0))
                    if strike <= 0 or spot <= 0 or iv <= 0:
                        continue

                    # Greeks via Black-Scholes
                    gamma_val = 0.0
                    delta_val = 0.0
                    vega_val = 0.0
                    theta_val = 0.0
                    try:
                        gamma_val = float(BSG.gamma(spot, strike, r, q, iv, tau))
                        delta_val = float(BSG.delta(spot, strike, r, q, iv, tau,
                                                     "call" if is_call else "put"))
                        vega_val = float(BSG.vega(spot, strike, r, q, iv, tau))
                        theta_val = float(BSG.theta(spot, strike, r, q, iv, tau,
                                                     "call" if is_call else "put"))
                    except Exception:
                        pass

                    chain.append({
                        "symbol": row.get("contractSymbol", ""),
                        "strike": strike,
                        "is_call": is_call,
                        "bid": bid,
                        "ask": ask,
                        "mid_price": mid_price,
                        "volume": _safe_int(row.get("volume", 0)),
                        "open_interest": _safe_int(row.get("openInterest", 0)),
                        "gamma": gamma_val,
                        "iv": iv,
                        "delta": delta_val,
                        "vega": vega_val,
                        "theta": theta_val,
                        "dte": dte,
                    })

        self._chain_cache[cache_key] = chain
        return chain

    def clear_cache(self) -> None:
        """Clear cached chain/spot data (e.g. at start of new session)."""
        self._chain_cache.clear()
        self._spot_cache.clear()

    # ===================================================================
    # 1.  calculate_realtime_gex()
    # ===================================================================
    def calculate_realtime_gex(
        self,
        symbol: str = "SPY",
        max_dte: int = 45,
        top_n: int = 20,
    ) -> dict:
        """Calculate net Gamma Exposure from live chain data.

        Formula
        -------
        GEX = Gamma × Spot² × OI × 100 × multiplier

        * Calls contribute **positive** GEX (dealer long gamma when net short
          options → hedging by buying dips / selling rallies = stabilising).
        * Puts contribute **negative** GEX (dealer short gamma → hedging
          amplifies moves = destabilising).

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g. "SPY", "SPX", "AAPL").
        max_dte : int
            Maximum days-to-expiry to include.
        top_n : int
            Number of top GEX strikes to return in the sorted list.

        Returns
        -------
        dict with keys:
            spot, aggregate_gex, regime, gex_by_strike (list of GEXStrike),
            top_calls, top_puts, timestamp
        """
        chain = self._fetch_option_chain(symbol, max_dte)
        spot = self.get_spot_price(symbol)
        multiplier = self._option_multiplier(symbol)

        gex_by_strike: Dict[float, dict] = {}
        total_gex = 0.0
        total_call_gex = 0.0
        total_put_gex = 0.0
        total_call_oi = 0
        total_put_oi = 0

        for opt in chain:
            strike = opt["strike"]
            gamma = opt["gamma"]
            oi = opt["open_interest"]
            is_call = opt["is_call"]

            # GEX = Gamma × Spot² × OI × 100 × multiplier
            # For equities multiplier=100, for index options multiplier=1
            # The per-contract multiplier (100 shares) is always applied.
            gex = gamma * (spot ** 2) * oi * EQUITY_MULTIPLIER * (1 if is_call else -1)

            if strike not in gex_by_strike:
                gex_by_strike[strike] = {
                    "strike": strike,
                    "call_gex": 0.0, "put_gex": 0.0, "total_gex": 0.0,
                    "call_oi": 0, "put_oi": 0, "total_oi": 0,
                }
            row = gex_by_strike[strike]
            if is_call:
                row["call_gex"] += gex
                row["call_oi"] += oi
                total_call_gex += gex
                total_call_oi += oi
            else:
                row["put_gex"] += gex
                row["put_oi"] += oi
                total_put_gex += gex
                total_put_oi += oi
            row["total_gex"] += gex
            row["total_oi"] = row["call_oi"] + row["put_oi"]
            total_gex += gex

        abs_total_gex = sum(abs(r["total_gex"]) for r in gex_by_strike.values())

        # Build sorted GEX-level list
        gex_levels: List[GEXStrike] = []
        for strike in sorted(gex_by_strike.keys()):
            r = gex_by_strike[strike]
            pct = (abs(r["total_gex"]) / abs_total_gex * 100) if abs_total_gex > 0 else 0
            gex_levels.append(GEXStrike(
                strike=strike,
                call_gex=round(r["call_gex"], 0),
                put_gex=round(r["put_gex"], 0),
                total_gex=round(r["total_gex"], 0),
                call_oi=r["call_oi"],
                put_oi=r["put_oi"],
                total_oi=r["total_oi"],
                pct_of_total=round(pct, 2),
            ))

        # Regime
        if total_gex > 0:
            regime = "positive_gamma"
            regime_desc = (
                "Dealers are NET LONG gamma → they hedge by selling rallies "
                "and buying dips → market tends to pin / rangebound."
            )
        elif total_gex < 0:
            regime = "negative_gamma"
            regime_desc = (
                "Dealers are NET SHORT gamma → they hedge by chasing moves "
                "→ market trend accelerates (volatile)."
            )
        else:
            regime = "neutral"
            regime_desc = "Aggregate GEX is flat — no strong dealer directional hedging."

        # Top strikes by absolute GEX
        sorted_gex = sorted(gex_levels, key=lambda g: abs(g.total_gex), reverse=True)
        top_calls = sorted([g for g in gex_levels if g.call_gex > 0],
                           key=lambda g: g.call_gex, reverse=True)[:top_n]
        top_puts = sorted([g for g in gex_levels if g.put_gex < 0],
                          key=lambda g: g.put_gex)[:top_n]   # most negative first

        result = {
            "symbol": symbol,
            "spot": round(spot, 2),
            "aggregate_gex": round(total_gex, 0),
            "total_call_gex": round(total_call_gex, 0),
            "total_put_gex": round(total_put_gex, 0),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "regime": regime,
            "regime_description": regime_desc,
            "num_strikes": len(gex_levels),
            "gex_by_strike": gex_levels[:top_n * 2],   # broader view
            "top_gex_strikes": sorted_gex[:top_n],
            "top_call_gex": top_calls,
            "top_put_gex": top_puts,
            "timestamp": datetime.utcnow().isoformat(),
        }
        return result

    # ===================================================================
    # 2.  find_gamma_flip()
    # ===================================================================
    def find_gamma_flip(
        self,
        symbol: str = "SPY",
        max_dte: int = 45,
    ) -> GammaFlip:
        """Find the gamma-flip strike — the price where cumulative GEX
        changes sign.

        **Why it matters:**
        Above the flip, dealers are long gamma (stabilising).  Below it,
        they are short gamma (destabilising).  A decisive move *through*
        the flip can trigger a cascade — the market "unwinds" through the
        flip and momentum accelerates.

        Returns
        -------
        GammaFlip dataclass with the flip strike, regime above/below, and
        trading implications.
        """
        chain = self._fetch_option_chain(symbol, max_dte)
        spot = self.get_spot_price(symbol)

        # Aggregate GEX per strike
        gex_map: Dict[float, float] = {}
        for opt in chain:
            strike = opt["strike"]
            gamma = opt["gamma"]
            oi = opt["open_interest"]
            is_call = opt["is_call"]
            gex = gamma * (spot ** 2) * oi * EQUITY_MULTIPLIER * (1 if is_call else -1)
            gex_map[strike] = gex_map.get(strike, 0.0) + gex

        sorted_strikes = sorted(gex_map.keys())

        # Walk from lowest strike upward; cumulative GEX = sum of all GEX at or
        # below the current strike.  The flip is where cumulative GEX crosses
        # zero.
        cumulative = 0.0
        flip_strike = sorted_strikes[0] if sorted_strikes else spot
        cumulative_below = 0.0
        cumulative_above = sum(gex_map.values())

        prev_cum = 0.0
        for strike in sorted_strikes:
            prev_cum = cumulative
            cumulative += gex_map.get(strike, 0.0)

            # Sign change between prev_cum and cumulative
            if (prev_cum < 0 and cumulative >= 0) or (prev_cum > 0 and cumulative <= 0):
                # Linear interpolation for a more precise flip estimate
                if cumulative != prev_cum:
                    frac = abs(prev_cum) / (abs(prev_cum) + abs(cumulative))
                    flip_strike = strike  # approximate; could interpolate
                else:
                    flip_strike = strike
                break
            elif prev_cum == 0 and len(sorted_strikes) > 1:
                flip_strike = strike
                break

        cumulative_below = sum(gex_map.get(s, 0.0) for s in sorted_strikes if s <= flip_strike)
        cumulative_above = sum(gex_map.get(s, 0.0) for s in sorted_strikes if s > flip_strike)

        distance = abs(flip_strike - spot)
        distance_pct = (distance / spot * 100) if spot > 0 else 0

        if flip_strike > spot:
            regime = "flip_above_spot"
            desc = (
                f"Gamma flip at ${flip_strike:.2f} is ABOVE spot (${spot:.2f}). "
                "Market is in negative-GEX territory → trending / volatile."
            )
            action = (
                "A move UP through the flip would transition to positive-GEX "
                "(pinning).  A failure at the flip could trigger accelerated "
                "selling below."
            )
        elif flip_strike < spot:
            regime = "flip_below_spot"
            desc = (
                f"Gamma flip at ${flip_strike:.2f} is BELOW spot (${spot:.2f}). "
                "Market is in positive-GEX territory → rangebound / pinning."
            )
            action = (
                "A break BELOW the flip would transition to negative-GEX "
                "(accelerated selling).  Hold the range above the flip."
            )
        else:
            regime = "at_spot"
            desc = f"Gamma flip is right at spot (${spot:.2f}) — pivotal level."
            action = "Watch for a decisive break in either direction for trend signal."

        return GammaFlip(
            strike=round(flip_strike, 2),
            cumulative_gex_below=round(cumulative_below, 0),
            cumulative_gex_above=round(cumulative_above, 0),
            regime=regime,
            spot=round(spot, 2),
            distance_from_spot=round(distance, 2),
            distance_pct=round(distance_pct, 2),
            description=desc,
            trading_implication=action,
        )

    # ===================================================================
    # 3.  detect_gamma_squeeze()
    # ===================================================================
    def detect_gamma_squeeze(
        self,
        symbol: str = "SPY",
        max_dte: int = 45,
        lookback_strikes: int = 10,
    ) -> GammaSqueeze:
        """Detect a potential gamma squeeze — self-reinforcing selling
        pressure when dealers are short gamma.

        **Mechanism:**
        When dealers are net short gamma (negative aggregate GEX), an
        adverse price move forces them to delta-hedge by selling the
        underlying.  That selling pushes the price further, requiring more
        hedging — a feedback loop.

        The detection heuristic scores:
        1. **Aggregate GEX sign and magnitude** — negative = dealers short.
        2. **Gamma concentration** — if GEX is concentrated in few strikes,
           a break accelerates.
        3. **Put-side OI density** — high put OI near spot increases squeeze
           risk (dealers must hedge puts aggressively on a down-move).

        Returns
        -------
        GammaSqueeze dataclass with direction, magnitude, velocity, risk
        level, and actionable description.
        """
        chain = self._fetch_option_chain(symbol, max_dte)
        spot = self.get_spot_price(symbol)

        # ---- Aggregate GEX ----
        total_gex = 0.0
        total_put_gex = 0.0
        total_call_gex = 0.0
        strike_gex: Dict[float, float] = {}
        total_oi = 0

        for opt in chain:
            strike = opt["strike"]
            gamma = opt["gamma"]
            oi = opt["open_interest"]
            is_call = opt["is_call"]
            gex = gamma * (spot ** 2) * oi * EQUITY_MULTIPLIER * (1 if is_call else -1)
            total_gex += gex
            if is_call:
                total_call_gex += gex
            else:
                total_put_gex += gex
            strike_gex[strike] = strike_gex.get(strike, 0.0) + gex
            total_oi += oi

        # ---- Concentration: top N strikes as % of total |GEX| ----
        abs_gex_by_strike = sorted(
            [(s, abs(g)) for s, g in strike_gex.items()],
            key=lambda x: x[1], reverse=True,
        )
        top_abs_gex = sum(g for _, g in abs_gex_by_strike[:lookback_strikes])
        total_abs_gex = sum(g for _, g in abs_gex_by_strike)
        concentration = (top_abs_gex / total_abs_gex * 100) if total_abs_gex > 0 else 0

        # ---- Put-side density near spot ----
        near_spot_puts = [
            opt for opt in chain
            if not opt["is_call"] and opt["strike"] >= spot * 0.95 and opt["strike"] <= spot
        ]
        put_oi_near_spot = sum(o["open_interest"] for o in near_spot_puts)
        put_oi_density = (put_oi_near_spot / total_oi * 100) if total_oi > 0 else 0

        # ---- Normalised magnitude ----
        magnitude = abs(total_gex) / (spot * spot) if spot > 0 else 0

        # ---- Velocity estimate ----
        # Velocity ≈ concentration × put_oi_density — higher concentration
        # with high put OI = explosive potential
        velocity = concentration * put_oi_density / 100

        # ---- Scoring ----
        score = 0
        if total_gex < 0:
            score += 3                    # dealers short gamma
            if abs(total_gex) > total_abs_gex * 0.5:
                score += 2                # heavily net short
        if concentration > 40:
            score += 1                    # concentrated
        if put_oi_density > 15:
            score += 1                    # lots of put OI near spot
        if velocity > 5:
            score += 2                    # high velocity proxy

        if score >= 7:
            risk = "extreme"
        elif score >= 5:
            risk = "high"
        elif score >= 3:
            risk = "moderate"
        else:
            risk = "low"

        if total_gex < -1e6 and risk in ("extreme", "high"):
            detected = True
            direction = "long_squeeze"    # price drops → dealers buy more puts → sell more stock
            desc = (
                f"⚠️  Gamma squeeze risk DETECTED. Dealers are net short "
                f"${abs(total_gex):,.0f} GEX. Put OI density near spot is "
                f"{put_oi_density:.1f}%.  A break below support can trigger "
                f"accelerated selling as dealers delta-hedge by selling shares."
            )
            action = (
                "BUY protection (long puts or put spreads).  Avoid catching "
                "the knife — wait for the squeeze to exhaust.  If already "
                "long, reduce size or tighten stops."
            )
        elif total_gex < 0:
            detected = False
            direction = "none"
            desc = (
                f"Dealers are short gamma (${abs(total_gex):,.0f} GEX) but "
                f"conditions don't yet scream squeeze.  Monitor put OI "
                f"density ({put_oi_density:.1f}%) and concentration "
                f"({concentration:.1f}%)."
            )
            action = "Stay alert.  If spot drops toward high-OI put strikes, squeeze risk escalates."
        else:
            detected = False
            direction = "none"
            desc = (
                f"Dealers are NET LONG gamma (${total_gex:,.0f} GEX) — they "
                f"stabilise by buying dips and selling rallies.  Squeeze risk "
                f"is negligible."
            )
            action = "Rangebound conditions.  Sell premium / iron condors preferred."

        return GammaSqueeze(
            detected=detected,
            squeeze_direction=direction,
            dealers_gamma_sign="short" if total_gex < 0 else ("long" if total_gex > 0 else "neutral"),
            magnitude=round(magnitude, 8),
            velocity=round(velocity, 4),
            risk_level=risk,
            description=desc,
            action=action,
        )

    # ===================================================================
    # 4.  find_call_wall_put_wall()
    # ===================================================================
    def find_call_wall_put_wall(
        self,
        symbol: str = "SPY",
        max_dte: int = 45,
        top_n: int = 5,
        oi_threshold_pct: float = 10.0,
    ) -> dict:
        """Find call and put walls — strikes with outsized open interest.

        **Call walls** are strikes where dealers are likely short calls (sold
        calls).  As the price approaches, dealers sell the underlying to
        remain delta-neutral → natural resistance.

        **Put walls** are strikes where dealers are likely short puts (sold
        puts).  As the price falls toward them, dealers buy the underlying
        to hedge → natural support.

        Parameters
        ----------
        top_n : int
            Number of walls to return on each side.
        oi_threshold_pct : float
            Minimum OI as % of the maximum OI to qualify as a wall.

        Returns
        -------
        dict with call_walls, put_walls (lists of Wall), nearest_call,
        nearest_put, and a trading_rule summary.
        """
        chain = self._fetch_option_chain(symbol, max_dte)
        spot = self.get_spot_price(symbol)

        # Aggregate OI and GEX by strike and side
        call_data: Dict[float, dict] = {}
        put_data: Dict[float, dict] = {}

        for opt in chain:
            strike = opt["strike"]
            oi = opt["open_interest"]
            gamma = opt["gamma"]
            is_call = opt["is_call"]
            gex = gamma * (spot ** 2) * oi * EQUITY_MULTIPLIER

            target = call_data if is_call else put_data
            if strike not in target:
                target[strike] = {"oi": 0, "gex": 0.0, "bid": 0, "ask": 0}
            target[strike]["oi"] += oi
            target[strike]["gex"] += gex * (1 if is_call else -1)
            # Store representative bid/ask (highest-oi contract wins)
            if oi > target[strike]["oi"] - oi:  # if this is the dominant contract
                target[strike]["bid"] = opt["bid"]
                target[strike]["ask"] = opt["ask"]

        # --- Call walls (strike > spot, high OI → resistance) ---
        max_call_oi = max((d["oi"] for d in call_data.values()), default=1) or 1
        call_walls: List[Wall] = []
        for strike in sorted(call_data.keys()):
            if strike <= spot:
                continue
            d = call_data[strike]
            if d["oi"] < max_call_oi * (oi_threshold_pct / 100):
                continue
            dist = strike - spot
            dist_pct = dist / spot * 100
            pct_oi = d["oi"] / max_call_oi * 100
            strength = "strong" if pct_oi > 80 else ("moderate" if pct_oi > 50 else "weak")
            call_walls.append(Wall(
                strike=round(strike, 2),
                oi=d["oi"],
                gex=round(d["gex"], 0),
                distance_from_spot=round(dist, 2),
                distance_pct=round(dist_pct, 2),
                strength=strength,
                role="resistance",
                bid_ask_spread=round(d["ask"] - d["bid"], 2) if d["ask"] > d["bid"] else 0,
                is_near_atm=dist_pct < 2,
            ))

        call_walls.sort(key=lambda w: w.oi, reverse=True)

        # --- Put walls (strike < spot, high OI → support) ---
        max_put_oi = max((d["oi"] for d in put_data.values()), default=1) or 1
        put_walls: List[Wall] = []
        for strike in sorted(put_data.keys()):
            if strike >= spot:
                continue
            d = put_data[strike]
            if d["oi"] < max_put_oi * (oi_threshold_pct / 100):
                continue
            dist = spot - strike
            dist_pct = dist / spot * 100
            pct_oi = d["oi"] / max_put_oi * 100
            strength = "strong" if pct_oi > 80 else ("moderate" if pct_oi > 50 else "weak")
            put_walls.append(Wall(
                strike=round(strike, 2),
                oi=d["oi"],
                gex=round(d["gex"], 0),
                distance_from_spot=round(dist, 2),
                distance_pct=round(dist_pct, 2),
                strength=strength,
                role="support",
                bid_ask_spread=round(d["ask"] - d["bid"], 2) if d["ask"] > d["bid"] else 0,
                is_near_atm=dist_pct < 2,
            ))

        put_walls.sort(key=lambda w: w.oi, reverse=True)

        # Nearest walls
        nearest_call = min(call_walls, key=lambda w: w.distance_pct) if call_walls else None
        nearest_put = min(put_walls, key=lambda w: w.distance_pct) if put_walls else None

        # Trading rule
        if nearest_put and nearest_call:
            rng = nearest_call.strike - nearest_put.strike
            rule = (
                f"Range: ${nearest_put.strike:.0f} (support) ↔ "
                f"${nearest_call.strike:.0f} (resistance) | "
                f"Expected range: ${rng:.0f} ({rng/spot*100:.1f}%)"
            )
        elif nearest_put:
            rule = f"Support at ${nearest_put.strike:.0f}.  No clear resistance above."
        elif nearest_call:
            rule = f"Resistance at ${nearest_call.strike:.0f}.  No clear support below."
        else:
            rule = "No significant walls detected."

        return {
            "spot": round(spot, 2),
            "call_walls": call_walls[:top_n],
            "put_walls": put_walls[:top_n],
            "nearest_call": nearest_call,
            "nearest_put": nearest_put,
            "trading_rule": rule,
            "total_call_walls": len(call_walls),
            "total_put_walls": len(put_walls),
        }

    # ===================================================================
    # 5.  get_expected_move()
    # ===================================================================
    def get_expected_move(
        self,
        symbol: str = "SPY",
        dte: Optional[int] = None,
    ) -> ExpectedMove:
        """Calculate the expected move from ATM straddle pricing.

        The expected move is the market-implied range over a given period.
        It is derived from the price of an ATM straddle:

            Expected Move ($)  ≈  Straddle Price
            Expected Move (%)  ≈  Straddle Price / Spot

        This represents the 1-standard-deviation move implied by options
        pricing (≈ 68% probability the stock stays within this range).

        Parameters
        ----------
        symbol : str
            Ticker.
        dte : int, optional
            Target days-to-expiry.  If None, picks the nearest expiry ≥ 7
            days (avoids 0-DTE noise).

        Returns
        -------
        ExpectedMove dataclass.
        """
        chain = self._fetch_option_chain(symbol, max_dte=90)
        spot = self.get_spot_price(symbol)

        if not chain or spot <= 0:
            return ExpectedMove(
                expected_move_dollars=0, expected_move_pct=0,
                expected_move_up=spot, expected_move_down=spot,
                straddle_price=0, atm_strike=spot, spot=spot,
                dte=0, iv_atm=0,
                description="Insufficient data to compute expected move.",
            )

        # Group by expiry
        expiry_groups: Dict[int, list] = {}
        for opt in chain:
            exp_dte = opt["dte"]
            expiry_groups.setdefault(exp_dte, []).append(opt)

        # Pick the right expiry
        available_dtes = sorted(expiry_groups.keys())
        if dte is not None:
            # Find closest available DTE
            target_dte = min(available_dtes, key=lambda d: abs(d - dte))
        else:
            # Default: nearest expiry ≥ 7 days, or the shortest available
            future_dtes = [d for d in available_dtes if d >= 7]
            target_dte = future_dtes[0] if future_dtes else (available_dtes[0] if available_dtes else 7)

        opts = expiry_groups.get(target_dte, chain)

        # Find ATM strike — closest to spot
        all_strikes = sorted(set(o["strike"] for o in opts))
        atm_strike = min(all_strikes, key=lambda k: abs(k - spot)) if all_strikes else spot

        # Get call and put at ATM
        atm_call = None
        atm_put = None
        for o in opts:
            if o["strike"] == atm_strike:
                if o["is_call"] and atm_call is None:
                    atm_call = o
                elif not o["is_call"] and atm_put is None:
                    atm_put = o

        if not atm_call or not atm_put:
            # Fallback: use the two strikes closest to spot
            sorted_opts = sorted(opts, key=lambda o: abs(o["strike"] - spot))
            for o in sorted_opts:
                if o["is_call"] and atm_call is None:
                    atm_call = o
                elif not o["is_call"] and atm_put is None:
                    atm_put = o
            if not atm_call or not atm_put:
                return ExpectedMove(
                    expected_move_dollars=0, expected_move_pct=0,
                    expected_move_up=spot, expected_move_down=spot,
                    straddle_price=0, atm_strike=spot, spot=spot,
                    dte=target_dte, iv_atm=0,
                    description="Could not find ATM call and put for straddle.",
                )

        # Straddle price = call mid + put mid
        call_mid = atm_call["mid_price"] if atm_call["mid_price"] > 0 else (atm_call["bid"] + atm_call["ask"]) / 2
        put_mid = atm_put["mid_price"] if atm_put["mid_price"] > 0 else (atm_put["bid"] + atm_put["ask"]) / 2
        straddle_price = call_mid + put_mid

        # Expected move
        em_dollars = straddle_price
        em_pct = (straddle_price / spot * 100) if spot > 0 else 0
        em_up = spot + em_dollars
        em_down = spot - em_dollars

        # ATM IV for reference
        iv_atm = (atm_call["iv"] + atm_put["iv"]) / 2.0

        # Annualise the move for context
        annualised_move_pct = em_pct * (252 / max(target_dte, 1)) ** 0.5

        desc = (
            f"ATM straddle at {target_dte}DTE costs ${straddle_price:.2f} "
            f"({em_pct:.1f}% of spot).  Market implies a ±{em_pct:.1f}% move "
            f"(${em_dollars:.2f}) by expiry — i.e. ${em_down:.2f} to ${em_up:.2f}.  "
            f"Annualised equivalent: ~{annualised_move_pct:.1f}%.  "
            f"ATM IV: {iv_atm*100:.1f}%."
        )

        return ExpectedMove(
            expected_move_dollars=round(em_dollars, 2),
            expected_move_pct=round(em_pct, 2),
            expected_move_up=round(em_up, 2),
            expected_move_down=round(em_down, 2),
            straddle_price=round(straddle_price, 2),
            atm_strike=round(atm_strike, 2),
            spot=round(spot, 2),
            dte=target_dte,
            iv_atm=round(iv_atm, 4),
            description=desc,
        )

    # ===================================================================
    # Full analysis
    # ===================================================================
    def full_analysis(self, symbol: str = "SPY", max_dte: int = 45) -> DealerPositionReport:
        """Run the complete dealer-positioning analysis.

        Combines all five core functions into a single report.
        """
        self.clear_cache()                    # fresh data

        gex_result = self.calculate_realtime_gex(symbol, max_dte)
        gex_levels: List[GEXStrike] = gex_result["gex_by_strike"]

        gamma_flip = self.find_gamma_flip(symbol, max_dte)
        gamma_squeeze = self.detect_gamma_squeeze(symbol, max_dte)
        walls = self.find_call_wall_put_wall(symbol, max_dte)
        exp_move = self.get_expected_move(symbol)

        # Regime summary
        parts = [gex_result["regime_description"]]
        if gamma_squeeze.detected:
            parts.append(f"⚠️  SQUEEZE: {gamma_squeeze.description}")
        if walls["nearest_put"]:
            parts.append(f"Support: ${walls['nearest_put'].strike:.0f}")
        if walls["nearest_call"]:
            parts.append(f"Resistance: ${walls['nearest_call'].strike:.0f}")
        parts.append(f"Expected move: ±{exp_move.expected_move_pct:.1f}%")

        regime_summary = " | ".join(parts)

        # Trading rule
        if gamma_squeeze.detected:
            trade_rule = gamma_squeeze.action
        elif gex_result["regime"] == "positive_gamma":
            trade_rule = (
                "Positive GEX regime — rangebound.  Sell premium (iron condors, "
                "strangles).  Buy near put-wall support, sell near call-wall resistance."
            )
        else:
            trade_rule = (
                "Negative GEX regime — trending/volatile.  Buy options for "
                "directional moves.  Respect the gamma flip level."
            )

        spot = self.get_spot_price(symbol)
        return DealerPositionReport(
            symbol=symbol,
            spot=round(spot, 2),
            timestamp=datetime.utcnow().isoformat(),
            gex=gex_levels,
            aggregate_gex=gex_result["aggregate_gex"],
            gamma_flip=gamma_flip,
            gamma_squeeze=gamma_squeeze,
            call_walls=walls["call_walls"],
            put_walls=walls["put_walls"],
            expected_move=exp_move,
            regime_summary=regime_summary,
            trading_rule=trade_rule,
        )


# ---------------------------------------------------------------------------
# Convenience function for quick one-liners
# ---------------------------------------------------------------------------
def quick_dealer_check(symbol: str = "SPY") -> dict:
    """One-liner: returns a condensed dict of dealer positioning signals."""
    dp = DealerPositioning()
    gex = dp.calculate_realtime_gex(symbol)
    flip = dp.find_gamma_flip(symbol)
    squeeze = dp.detect_gamma_squeeze(symbol)
    walls = dp.find_call_wall_put_wall(symbol)
    em = dp.get_expected_move(symbol)

    return {
        "symbol": symbol,
        "spot": gex["spot"],
        "aggregate_gex": gex["aggregate_gex"],
        "regime": gex["regime"],
        "flip_strike": flip.strike,
        "flip_distance_pct": flip.distance_pct,
        "squeeze_detected": squeeze.detected,
        "squeeze_risk": squeeze.risk_level,
        "nearest_support": walls["nearest_put"].strike if walls["nearest_put"] else None,
        "nearest_resistance": walls["nearest_call"].strike if walls["nearest_call"] else None,
        "expected_move_pct": em.expected_move_pct,
        "expected_move_range": f"${em.expected_move_down:.2f} – ${em.expected_move_up:.2f}",
        "timestamp": datetime.utcnow().isoformat(),
    }
