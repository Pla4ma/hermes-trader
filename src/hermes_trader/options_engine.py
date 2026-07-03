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
"""

import json
import math
import os
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("hermes_trader.options_engine")


class OptionsEngine:
    """Full-featured options trading engine with institutional-grade signals."""

    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        self._client = None
        self._opt_client = None
        self._trading_client = None

    @property
    def opt_client(self):
        if self._opt_client is None:
            from alpaca.data.historical import OptionHistoricalDataClient
            self._opt_client = OptionHistoricalDataClient(self.api_key, self.secret_key)
        return self._opt_client

    @property
    def trading_client(self):
        if self._trading_client is None:
            from alpaca.trading.client import TradingClient
            self._trading_client = TradingClient(self.api_key, self.secret_key, paper=False)
        return self._trading_client

    def get_spy_price(self) -> float:
        """Get current SPY price."""
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
        # For calls: vanna > 0 → as IV drops, delta increases → dealer must sell to hedge → bearish flow
        # For puts:  vanna < 0 → as IV drops, delta gets less negative → dealer must buy to hedge → bullish flow
        # Net bullish flow when: put vanna (negative) × IV decline
        if option_type == "put":
            # Negative vanna + IV dropping = bullish vanna flow (dealers buying)
            flow_direction = "bullish" if vanna < 0 else "bearish"
        else:
            # Positive vanna + IV dropping = bearish vanna flow (dealers selling)
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
        - ATM call: charm > 0 → delta increases toward 1 as time passes
        - ATM put:  charm < 0 → delta decreases toward -1 as time passes
        - OTM options: charm drives delta toward 0 (worthless)

        Entry timing:
        - For LONG calls: enter when charm is positive and high → delta
          will naturally increase over time → favorable
        - For LONG puts: enter when charm is negative and large magnitude → delta
          will naturally become more negative → favorable
        - Best entry: 5-14 DTE where charm decay is most pronounced

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

        # High absolute charm = fast delta movement = good timing
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

        # Best charm timing: ATM (0.98-1.02 moneyness) with 1-7 DTE
        if 0.98 <= moneyness <= 1.02 and dte <= 7:
            timing_grade = "EXCELLENT"  # ATM near expiry = max charm
        elif 0.96 <= moneyness <= 1.04 and dte <= 14:
            timing_grade = "GOOD"
        elif 0.90 <= moneyness <= 1.10 and dte <= 21:
            timing_grade = "MODERATE"
        else:
            timing_grade = "WEAK"

        # Charm is favorable for long options if it moves delta in our favor
        favorable = False
        if option_type == "call" and charm > 0:
            favorable = True  # Delta increases over time → good for long calls
        elif option_type == "put" and charm < 0:
            favorable = True  # Delta becomes more negative → good for long puts

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

        The IV surface uses SVI parametrization to fit a smooth surface
        through all observed IVs. If the market price deviates significantly
        from the surface, we have a mispricing signal.

        Key signals:
        - Market IV >> Surface IV: option is OVERPRICED → avoid or sell
        - Market IV << Surface IV: option is CHEAP → buy signal
        - Skew deviation: if put IV is way above call IV at same delta,
          there's fear premium → contrarian buy opportunity

        Returns dict with fair_value_score (0-10) and iv_premium.
        """
        score = 0
        iv_premium = 0.0
        fair_iv = market_iv  # Default: assume fair

        try:
            from .iv_surface import IVSurface, bs_implied_vol
        except ImportError:
            from hermes_trader.iv_surface import IVSurface, bs_implied_vol

        try:
            # Build a minimal IV surface from the option chain
            surface = IVSurface(spot=spot, rate=0.05)
            chain_data = self._get_chain_for_surface(symbol, spot)

            if chain_data and len(chain_data) > 10:
                # Fit SVI to each expiry slice
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

                    # Compare market vs fair value
                    iv_premium = (market_iv - fair_iv) / fair_iv if fair_iv > 0 else 0

                    if iv_premium < -0.10:
                        # Market IV 10%+ below surface → cheap option → BUY
                        score = 10
                    elif iv_premium < -0.05:
                        score = 7
                    elif iv_premium < -0.02:
                        score = 4
                    elif iv_premium < 0.02:
                        score = 2  # Fairly priced
                    elif iv_premium < 0.05:
                        score = 0  # Slightly expensive
                    elif iv_premium < 0.10:
                        score = 0  # Expensive
                    else:
                        score = 0  # Very expensive → avoid

                    # Also check skew signal
                    if surface.svi_params:
                        sorted_exp = sorted(surface.slices.keys())
                        if sorted_exp:
                            nearest_exp = min(sorted_exp, key=lambda e: abs(e - tte_years))
                            try:
                                skew_val = surface.skew(nearest_exp)
                                # Steep negative skew = fear → contrarian opportunity
                                if skew_val < -0.03 and option_type == "put":
                                    score = min(score + 3, 10)
                            except Exception:
                                pass
        except Exception as e:
            logger.debug("IV surface computation failed: %s", e)
            # Fallback: use simple IV percentile heuristics
            if market_iv < 0.15:
                score = 5  # Low IV → options cheap
            elif market_iv < 0.20:
                score = 3
            elif market_iv > 0.35:
                score = 0  # High IV → options expensive

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
            from alpaca.data.requests import OptionChainRequest
            today = datetime.utcnow().date()
            req = OptionChainRequest(
                underlying_symbol=symbol,
                expiration_date_gte=today + timedelta(days=1),
                expiration_date_lte=today + timedelta(days=60),
            )
            chain = self.opt_client.get_option_chain(req)

            # Group by expiry
            by_expiry = {}
            for sym, snap in chain.items():
                if not snap.latest_quote or not snap.greeks:
                    continue
                q = snap.latest_quote
                bid = float(q.bid_price or 0)
                ask = float(q.ask_price or 0)
                if bid <= 0 or ask <= 0:
                    continue

                mid = (bid + ask) / 2
                iv = float(snap.implied_volatility or 0)
                if iv <= 0 or iv > 3.0:
                    continue

                is_call = "C" in sym
                try:
                    strike = float(sym.split("C" if is_call else "P")[1]) / 1000
                except Exception:
                    continue

                # Parse expiry from symbol (format: SPY250704C00550000)
                try:
                    exp_str = sym[len(symbol):][:6]
                    exp_date = datetime.strptime(exp_str, "%y%m%d").date()
                    tte = max(1, (exp_date - today).days)
                except Exception:
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
        """
        Get Gamma Exposure regime for position management.

        GEX REGIME IMPACT ON OPTIONS TRADING:
        - Positive GEX (dealers long gamma): Market pins to strikes, rangebound.
          → SELL options (theta collection), avoid directional bets
        - Negative GEX (dealers short gamma): Market moves violently, trending.
          → BUY options (gamma exposure), take directional bets

        Returns regime info and position sizing adjustment factor.
        """
        try:
            from .gamma_positioning import GammaPositioning
        except ImportError:
            from hermes_trader.gamma_positioning import GammaPositioning

        try:
            gp = GammaPositioning()
            gex_data = gp.calculate_gex(symbol, max_dte=7)
            total_gex = gex_data.get("total_gex", 0)
            regime = gex_data.get("regime", "unknown")

            # Position sizing adjustment
            # Negative gamma → increase size (trending market, momentum works)
            # Positive gamma → decrease size (rangebound, harder to profit)
            if regime == "negative_gamma":
                size_factor = 1.2  # 20% larger positions in trending regime
                confidence_boost = 2  # Extra confidence points
            elif regime == "positive_gamma":
                size_factor = 0.8  # 20% smaller positions in rangebound regime
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
        """
        Adjust option score based on GEX regime.

        Rules:
        - Negative GEX + calls → boost score (buying gamma in trending market)
        - Negative GEX + puts → boost score (buying gamma in trending market)
        - Positive GEX + any long option → penalize (market pins, hard to profit)
        - Positive GEX + selling → boost (theta collection in rangebound)
        """
        regime = gex_regime.get("regime", "unknown")
        boost = gex_regime.get("confidence_boost", 0)

        # For long options (which we're buying):
        if regime == "negative_gamma":
            # Trending market → directional options work better
            return max(0, base_score + 3)
        elif regime == "positive_gamma":
            # Rangebound → harder for long options to profit
            return max(0, base_score - 2)
        return base_score

    # =========================================================================
    # ENHANCED SCANNING (integrates all 4 signals)
    # =========================================================================
    def scan_calls(self, symbol: str = "SPY", max_cost: float = 50.0,
                   max_dte: int = 21, min_delta: float = 0.10) -> list[dict]:
        """Scan for tradeable call options with institutional-grade scoring."""
        from alpaca.data.requests import OptionChainRequest

        spy_price = self.get_spy_price()
        today = datetime.utcnow().date()
        max_expiry = today + timedelta(days=max_dte)

        # Get GEX regime once (cached per scan)
        gex_regime = self._get_gex_regime(symbol)

        # Get chain
        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=today + timedelta(days=2),
            expiration_date_lte=max_expiry,
        )
        chain = self.opt_client.get_option_chain(req)

        candidates = []
        for sym, snap in chain.items():
            if "C" not in sym:
                continue
            if not snap.latest_quote or not snap.greeks:
                continue

            q = snap.latest_quote
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            cost = mid * 100  # Per contract
            if cost <= 0 or cost > max_cost:
                continue

            delta = abs(float(snap.greeks.delta or 0))
            gamma = float(snap.greeks.gamma or 0)
            theta = float(snap.greeks.theta or 0)
            vega = float(snap.greeks.vega or 0)
            iv = float(snap.implied_volatility or 0)

            if delta < min_delta:
                continue

            try:
                strike = float(sym.split("C")[1]) / 1000
            except (ValueError, IndexError):
                continue

            dte = max(1, (max_expiry - today).days)
            spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999
            moneyness = (strike - spy_price) / spy_price * 100

            # =================================================================
            # CORE SCORING (0-15 points — original factors)
            # =================================================================
            score = 0

            # 1. Delta sweet spot (0.20-0.40 is ideal for leverage)
            if 0.20 <= delta <= 0.40:
                score += 4
            elif 0.15 <= delta <= 0.50:
                score += 3
            elif delta >= 0.10:
                score += 1

            # 2. Affordability
            if cost <= 20:
                score += 3
            elif cost <= 35:
                score += 2
            elif cost <= 50:
                score += 1

            # 3. Gamma (higher = more bang per $1 move)
            if gamma > 0.05:
                score += 3
            elif gamma > 0.03:
                score += 2
            elif gamma > 0.01:
                score += 1

            # 4. Spread (tighter = better fills)
            if spread_pct < 5:
                score += 3
            elif spread_pct < 10:
                score += 1

            # 5. DTE (5-14 days is sweet spot)
            if 5 <= dte <= 14:
                score += 2
            elif 3 <= dte <= 21:
                score += 1

            # =================================================================
            # INSTITUTIONAL SIGNALS (up to 40 additional points)
            # =================================================================
            inst_score = 0

            # Signal 1: Vanna Flow (0-15)
            vanna_data = self._compute_vanna_flow_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="call"
            )
            inst_score += vanna_data["vanna_flow_score"]

            # Signal 2: Charm Decay Timing (0-15)
            charm_data = self._compute_charm_decay_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="call"
            )
            inst_score += charm_data["charm_timing_score"]

            # Signal 3: IV Surface Fair Value (0-10)
            iv_data = self._compute_iv_surface_signal(
                symbol=symbol, strike=strike, market_iv=iv,
                spot=spy_price, dte=dte, option_type="call"
            )
            inst_score += iv_data["iv_surface_score"]

            # Signal 4: GEX Regime Adjustment
            gex_adjusted = self._compute_gex_adjusted_score(
                inst_score, gex_regime, "call"
            )
            inst_score = gex_adjusted

            total_score = score + inst_score

            candidates.append({
                "symbol": sym,
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
        from alpaca.data.requests import OptionChainRequest

        spy_price = self.get_spy_price()
        today = datetime.utcnow().date()
        max_expiry = today + timedelta(days=max_dte)

        # Get GEX regime once
        gex_regime = self._get_gex_regime(symbol)

        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=today + timedelta(days=2),
            expiration_date_lte=max_expiry,
        )
        chain = self.opt_client.get_option_chain(req)

        candidates = []
        for sym, snap in chain.items():
            if "P" not in sym:
                continue
            if not snap.latest_quote or not snap.greeks:
                continue

            q = snap.latest_quote
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            cost = mid * 100
            if cost <= 0 or cost > max_cost:
                continue

            delta = abs(float(snap.greeks.delta or 0))
            if delta < min_delta:
                continue

            try:
                strike = float(sym.split("P")[1]) / 1000
            except (ValueError, IndexError):
                continue

            gamma = float(snap.greeks.gamma or 0)
            theta = float(snap.greeks.theta or 0)
            vega = float(snap.greeks.vega or 0)
            iv = float(snap.implied_volatility or 0)
            spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999
            dte = max(1, (max_expiry - today).days)

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

            # Signal 1: Vanna Flow (0-15)
            # For puts: negative vanna + IV declining = bullish vanna flow
            # This means: if we're BUYING puts in a vanna-flow environment,
            # we're against the institutional flow → lower score
            vanna_data = self._compute_vanna_flow_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="put"
            )
            # For puts, bullish vanna flow means the market is likely rallying
            # → reduce score for put buying, boost for put selling
            if vanna_data["vanna_flow_direction"] == "bullish":
                inst_score += max(0, vanna_data["vanna_flow_score"] - 5)
            else:
                inst_score += vanna_data["vanna_flow_score"]

            # Signal 2: Charm Decay Timing (0-15)
            charm_data = self._compute_charm_decay_signal(
                S=spy_price, K=strike, tau=dte, sigma=iv, option_type="put"
            )
            inst_score += charm_data["charm_timing_score"]

            # Signal 3: IV Surface Fair Value (0-10)
            iv_data = self._compute_iv_surface_signal(
                symbol=symbol, strike=strike, market_iv=iv,
                spot=spy_price, dte=dte, option_type="put"
            )
            inst_score += iv_data["iv_surface_score"]

            # Signal 4: GEX Regime Adjustment
            gex_adjusted = self._compute_gex_adjusted_score(
                inst_score, gex_regime, "put"
            )
            inst_score = gex_adjusted

            total_score = score + inst_score

            candidates.append({
                "symbol": sym,
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
        """
        Check if vanna flow suggests exiting a position.

        VANNA FLOW EXIT RULES:
        1. If we're long calls and IV has dropped significantly:
           - Vanna flow = dealers buying (unwinding put hedges)
           - This is BULLISH for the market → our calls should be profitable
           - But: if IV keeps dropping, our vega exposure loses money
           - EXIT when: IV has dropped >20% from entry OR vanna flow is exhausted

        2. If we're long puts and IV has dropped:
           - Vanna flow is bullish → our puts are losing on both delta AND vega
           - EXIT immediately if IV drops >10% from entry

        3. If IV is RISING:
           - For calls: vega gains, but vanna flow is bearish (dealers selling)
           - For puts: vega gains AND vanna flow is bullish (dealers buying puts)
           - HOLD puts, be cautious with calls

        Returns: exit signal with reason and urgency.
        """
        if current_iv <= 0 or entry_iv <= 0:
            return {"exit": False, "reason": "IV data unavailable"}

        iv_change_pct = (current_iv - entry_iv) / entry_iv
        option_type = position.get("type", "call")

        # Vanna flow is exhausted when IV stabilizes or reverses
        vanna_exhausted = abs(iv_change_pct) < 0.03  # <3% IV change = quiet

        # EXIT RULES
        if option_type == "call":
            # Long calls: exit if IV drops too much (vega losses exceed delta gains)
            if iv_change_pct < -0.15:
                return {
                    "exit": True,
                    "urgency": "high",
                    "reason": f"IV dropped {iv_change_pct*100:.1f}% from entry → vega losses, vanna flow may be exhausted",
                    "signal": "vanna_iv_crush",
                }
            elif iv_change_pct < -0.10 and vanna_exhausted:
                return {
                    "exit": True,
                    "urgency": "medium",
                    "reason": f"IV down {iv_change_pct*100:.1f}% and stabilizing → vanna flow exhaustion",
                    "signal": "vanna_flow_exhausted",
                }
            # If IV is rising, vanna flow is bearish for calls (dealers selling)
            elif iv_change_pct > 0.20:
                return {
                    "exit": False,
                    "urgency": "watch",
                    "reason": f"IV up {iv_change_pct*100:.1f}% → vega gains but vanna flow bearish, monitor closely",
                    "signal": "vanna_bearish_watch",
                }

        elif option_type == "put":
            # Long puts: exit if IV drops (puts lose on vega AND vanna flow bullish)
            if iv_change_pct < -0.08:
                return {
                    "exit": True,
                    "urgency": "high",
                    "reason": f"IV dropped {iv_change_pct*100:.1f}% → vega loss + bullish vanna flow = double negative for puts",
                    "signal": "vanna_put_catastrophic",
                }
            # If IV rising, vanna flow is bullish AND we have vega gains → hold
            elif iv_change_pct > 0.10:
                return {
                    "exit": False,
                    "urgency": "hold",
                    "reason": f"IV up {iv_change_pct*100:.1f}% → vega gains + bullish vanna flow supports puts",
                    "signal": "vanna_put_favorable",
                }

        return {
            "exit": False,
            "urgency": "hold",
            "reason": f"Vanna flow neutral (IV change: {iv_change_pct*100:.1f}%)",
            "signal": "vanna_neutral",
        }

    # =========================================================================
    # CHARM-BASED ENTRY TIMING
    # =========================================================================
    def get_optimal_entry_dte(self, symbol: str = "SPY",
                               option_type: str = "call") -> dict:
        """
        Determine optimal DTE for entry based on charm decay curves.

        CHARM ENTRY TIMING:
        - Charm is highest (most delta decay) for ATM options at 1-7 DTE
        - But entering too close to expiry = high theta burn
        - Sweet spot: when charm_per_day is maximized relative to theta_per_day
        - This gives us the best "delta acceleration" per dollar of theta paid

        For 0DTE specifically:
        - Charm creates a "delta magnet" effect
        - ATM calls gain delta faster as expiry approaches
        - This is exploitable for 0DTE directional trades

        Returns optimal DTE and timing rationale.
        """
        spot = self.get_spy_price()

        # Test different DTEs to find optimal charm/theta ratio
        best_dte = 7
        best_ratio = 0

        try:
            from .hol_greeks import compute_hol_greeks
        except ImportError:
            from hermes_trader.hol_greeks import compute_hol_greeks

        flag = "c" if option_type == "call" else "p"

        for test_dte in [1, 2, 3, 5, 7, 10, 14, 21, 30]:
            tau_years = test_dte / 365.0
            # Use ATM strike and typical IV
            iv = 0.20 if test_dte > 7 else 0.25  # IV typically higher for short-dated

            try:
                hog = compute_hol_greeks(
                    S=spot, K=spot, t=tau_years, r=0.05,
                    sigma=iv, q=0.0, flag=flag
                )
                # Charm/theta ratio: how much delta acceleration per dollar of theta
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

        # Always scan up to $50 — we want to know what's available
        max_scan_cost = 50.0

        if direction == "bullish":
            candidates = self.scan_calls(max_cost=max_scan_cost)
            strategy = "long_call"
        else:
            candidates = self.scan_puts(max_cost=max_scan_cost)
            strategy = "long_put"

        if not candidates:
            return {"action": "none", "reason": f"No viable {direction} options found"}

        # GEX regime check: in positive gamma regime, prefer selling over buying
        gex = self._get_gex_regime()
        if gex["regime"] == "positive_gamma" and direction == "bullish":
            # In rangebound market, buying calls is less effective
            # Still proceed but note reduced conviction
            for c in candidates:
                c["score"] = max(0, c["score"] - 3)
                c["gex_warning"] = "Positive GEX → rangebound expected, directional bet risky"
            candidates.sort(key=lambda x: x["score"], reverse=True)

        # Find best affordable option
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
        """Execute an options trade."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        try:
            order = self.trading_client.submit_order(
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
                "type": candidate["type"],
                "strike": candidate["strike"],
                "cost": candidate["cost"],
                "delta": candidate["delta"],
                "score": candidate["score"],
                "order_id": str(order.id),
                "status": str(order.status),
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
                "underlying": candidate["underlying"],
                "type": candidate["type"],
                "strike": candidate["strike"],
                "cost": candidate["cost"],
                "delta": candidate["delta"],
                "gamma": candidate.get("gamma", 0),
                "iv": candidate.get("iv", 0),
                "score": candidate["score"],
                "order_id": str(order.id),
                "strategy": "options_engine_v5",
                # Institutional signals
                "vanna": candidate.get("vanna", 0),
                "charm": candidate.get("charm", 0),
                "vanna_flow_score": candidate.get("vanna_flow_score", 0),
                "charm_timing_score": candidate.get("charm_timing_score", 0),
                "iv_surface_score": candidate.get("iv_surface_score", 0),
                "gex_regime": candidate.get("gex_regime", "unknown"),
                "entry_iv": candidate.get("iv", 0),
            }
            with open("/opt/hermes-trader/data/journals/paper_orders.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")

            return result

        except Exception as e:
            return {"error": str(e)}

    def auto_trade(self) -> dict:
        """Fully autonomous options trade with institutional-grade signals."""
        # Determine direction from market regime
        direction = "bullish"
        try:
            from .market_regime import detect_regime
            regime = detect_regime()
            if "BEAR" in regime.get("regime", ""):
                direction = "bearish"
        except Exception:
            pass

        # GEX regime check: might override direction
        gex = self._get_gex_regime()
        if gex["regime"] == "positive_gamma" and direction == "bullish":
            # In positive gamma (rangebound), directional bets less effective
            # Still trade but with reduced conviction
            pass

        # Find best trade
        result = self.find_best_trade(direction)

        if result.get("action") == "trade":
            candidate = result["candidate"]
            execution = self.execute_trade(candidate)
            result["execution"] = execution

            # Store entry IV for exit monitoring
            result["entry_iv"] = candidate.get("iv", 0)

        # Also scan the other direction for hedging
        other_dir = "bearish" if direction == "bullish" else "bullish"
        other_candidates = (
            self.scan_calls(max_cost=20.0) if other_dir == "bullish"
            else self.scan_puts(max_cost=20.0)
        )
        result["hedge_candidates"] = other_candidates[:3] if other_candidates else []

        # Add institutional signal summary
        result["institutional_signals"] = {
            "gex_regime": gex["regime"],
            "gex_flip_strike": gex.get("flip_strike"),
            "entry_timing": self.get_optimal_entry_dte(),
        }

        return result

    def _get_cash(self) -> float:
        """Get available cash."""
        try:
            acct = self.trading_client.get_account()
            return float(acct.cash)
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
        from alpaca.data.requests import OptionChainRequest
        today = datetime.utcnow().date()
        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=today,
            expiration_date_lte=today + timedelta(days=7),
        )
        chain = engine.opt_client.get_option_chain(req)

        total_put_vanna = 0
        total_call_vanna = 0
        vanna_by_strike = {}

        for sym, snap in chain.items():
            if not snap.latest_quote or not snap.greeks:
                continue

            is_call = "C" in sym
            try:
                strike = float(sym.split("C" if is_call else "P")[1]) / 1000
            except Exception:
                continue

            iv = float(snap.implied_volatility or 0)
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

        # Interpret vanna flow
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
    print("=== OPTIONS ENGINE v5 — INSTITUTIONAL GRADE ===")

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
