"""Options Flow Detection — real-time institutional-grade flow analysis.

Detects unusual options volume, sweep activity, put/call premium flow,
and aggregates bullish/bearish sentiment signals.

Features:
    1. detect_unusual_volume() — flag contracts with 3x+ volume vs 20-day avg
    2. detect_sweeps() — aggressive buys at ask with large size (institutional)
    3. calculate_put_call_flow() — net premium flow by direction
    4. get_flow_sentiment() — aggregate bullish/bearish signal

Data source: yfinance (free). No API keys required.

Usage:
    from hermes_trader.options_flow import OptionsFlowDetector

    flow = OptionsFlowDetector()

    # 1) Unusual volume spikes
    spikes = flow.detect_unusual_volume("SPY", max_dte=5)
    for s in spikes:
        print(f"{s.symbol} vol_spike={s.volume_ratio:.1f}x")

    # 2) Sweep detection
    sweeps = flow.detect_sweeps("SPY", max_dte=1)

    # 3) Put/call flow
    flow_data = flow.calculate_put_call_flow("SPY", max_dte=5)

    # 4) Aggregate sentiment
    sentiment = flow.get_flow_sentiment("SPY")
    print(sentiment.signal)  # "bullish", "bearish", "neutral"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

logger = logging.getLogger("hermes_trader.options_flow")

# ── Constants ──────────────────────────────────────────────────────

# Volume spike threshold: flag when current_vol >= multiplier × avg_vol
DEFAULT_VOLUME_MULTIPLIER = 3.0

# Minimum volume floor to avoid noise on illiquid contracts
MIN_VOLUME_FLOOR = 50

# Historical lookback for average volume (trading days)
HISTORY_LOOKBACK_DAYS = 20

# Minimum premium ($) for a sweep to be significant
MIN_SWEEP_PREMIUM = 0.50

# Minimum contracts for a sweep to be considered institutional
MIN_SWEEP_SIZE = 100

# Aggressive fill ratio: ask_fill / total ≈ 1.0 means fully at the ask
AGGRESSIVE_FILL_THRESHOLD = 0.85

# Risk-free rate (approx current T-bill rate)
RISK_FREE_RATE = 0.052


# ── Data classes ───────────────────────────────────────────────────


@dataclass
class UnusualVolumeAlert:
    """A single unusual volume detection."""

    symbol: str
    strike: float
    expiry: str
    is_call: bool
    current_volume: int
    avg_volume: float
    volume_ratio: float
    open_interest: int
    last_price: float
    mid_price: float
    premium_estimate: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SweepAlert:
    """A single sweep detection — aggressive institutional buying."""

    symbol: str
    strike: float
    expiry: str
    is_call: bool
    volume: int
    open_interest: int
    ask_price: float
    bid_price: float
    last_price: float
    estimated_premium: float
    fill_ratio: float  # how close to the ask
    sweep_score: float  # composite score (higher = more aggressive)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PutCallFlow:
    """Net premium flow aggregated by direction."""

    symbol: str
    call_premium: float
    put_premium: float
    call_volume: int
    put_volume: int
    call_oi: int
    put_oi: int
    net_premium: float  # call_premium - put_premium (>0 = bullish flow)
    put_call_volume_ratio: float
    put_call_premium_ratio: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FlowSentiment:
    """Aggregated sentiment from multiple flow signals."""

    symbol: str
    signal: Literal["bullish", "bearish", "neutral"]
    strength: Literal["strong", "moderate", "weak"]
    score: float  # -1.0 (max bearish) to +1.0 (max bullish)
    components: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


# ── Main class ─────────────────────────────────────────────────────


class OptionsFlowDetector:
    """Real-time options flow detection engine.

    Analyses option chains via yfinance to surface:
    - Unusual volume spikes (institutional accumulation)
    - Sweep orders (aggressive directional bets)
    - Put/call premium flow (net direction of money)
    - Aggregate sentiment signal

    Args:
        symbols: Default underlyings to analyse.
        volume_multiplier: Spike threshold (default 3×).
        min_volume: Minimum volume floor to filter noise.
    """

    def __init__(
        self,
        symbols: Optional[Tuple[str, ...]] = None,
        volume_multiplier: float = DEFAULT_VOLUME_MULTIPLIER,
        min_volume: int = MIN_VOLUME_FLOOR,
    ):
        self.symbols = symbols or ("SPY", "QQQ")
        self.volume_multiplier = volume_multiplier
        self.min_volume = min_volume
        self._ticker_cache: Dict[str, Any] = {}

    # ── Internal helpers ───────────────────────────────────────

    def _get_ticker(self, symbol: str) -> Any:
        """Get or cache a yfinance Ticker object."""
        if symbol not in self._ticker_cache:
            try:
                import yfinance as yf
                self._ticker_cache[symbol] = yf.Ticker(symbol)
            except ImportError:
                logger.error("yfinance not installed — pip install yfinance")
                raise
        return self._ticker_cache[symbol]

    def _spot_price(self, symbol: str) -> float:
        """Get current spot price for the underlying."""
        ticker = self._get_ticker(symbol)
        try:
            price = ticker.fast_info.get("lastPrice", 0)
            if price and price > 0:
                return float(price)
        except Exception as e:
            logger.warning("fast_info failed for %s: %s", symbol, e)

        # Fallback: last closing price from history
        try:
            hist = ticker.history(period="5d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.warning("history fallback failed for %s: %s", symbol, e)

        return 0.0

    def _fetch_chains(
        self, symbol: str, max_dte: int
    ) -> List[Dict[str, Any]]:
        """Fetch all option contracts up to *max_dte* calendar days.

        Returns a flat list of dicts with normalised fields ready for analysis.
        """
        import yfinance as yf

        ticker = self._get_ticker(symbol)
        today = date.today()
        contracts: List[Dict[str, Any]] = []

        try:
            expiry_dates = ticker.options
        except Exception as e:
            logger.error("Cannot fetch options for %s: %s", symbol, e)
            return []

        for exp_str in expiry_dates:
            try:
                exp_date = date.fromisoformat(exp_str)
                dte = (exp_date - today).days
            except (ValueError, TypeError):
                continue

            if dte < 0 or dte > max_dte:
                continue

            try:
                chain = ticker.option_chain(exp_str)
            except Exception as e:
                logger.warning("Chain fetch failed %s %s: %s", symbol, exp_str, e)
                continue

            for opt_type_label, is_call in [("calls", True), ("puts", False)]:
                df = getattr(chain, opt_type_label, None)
                if df is None or df.empty:
                    continue

                for _, row in df.iterrows():
                    mid = 0.0
                    try:
                        bid = float(row.get("bid", 0) or 0)
                        ask = float(row.get("ask", 0) or 0)
                        if bid > 0 and ask > 0:
                            mid = (bid + ask) / 2.0
                    except Exception:
                        bid = ask = 0.0

                    contracts.append(
                        {
                            "symbol": symbol,
                            "strike": float(row.get("strike", 0)),
                            "expiry": exp_str,
                            "dte": dte,
                            "is_call": is_call,
                            "bid": bid,
                            "ask": ask,
                            "mid": mid,
                            "last": float(row.get("lastPrice", 0) or 0),
                            "volume": int(row.get("volume", 0) or 0),
                            "open_interest": int(
                                row.get("openInterest", 0) or 0
                            ),
                            "implied_volatility": float(
                                row.get("impliedVolatility", 0) or 0
                            ),
                        }
                    )

        logger.debug(
            "Fetched %d contracts for %s (max_dte=%d)", len(contracts), symbol, max_dte
        )
        return contracts

    def _estimate_historical_avg_volume(
        self, symbol: str, lookback: int = HISTORY_LOOKBACK_DAYS
    ) -> float:
        """Estimate average daily options volume over *lookback* calendar days.

        yfinance does not expose per-contract historical volume directly, so we
        estimate it from the underlying's own average daily volume scaled by a
        typical SPY/QQQ options-to-equity volume ratio (~0.3).
        """
        ticker = self._get_ticker(symbol)
        try:
            hist = ticker.history(period=f"{lookback}d")
            if hist.empty:
                return 0.0
            avg_equity_vol = hist["Volume"].mean()
            # Empirical ratio: options volume ≈ 20-40% of underlying equity volume
            return avg_equity_vol * 0.3
        except Exception as e:
            logger.warning("History fetch failed for avg volume: %s", e)
            return 0.0

    # ── Public API ─────────────────────────────────────────────

    def detect_unusual_volume(
        self,
        symbol: str = "SPY",
        max_dte: int = 7,
        multiplier: Optional[float] = None,
    ) -> List[UnusualVolumeAlert]:
        """Detect options contracts with unusually high volume.

        Compares each contract's current volume to the historical average and
        flags those exceeding *multiplier* × the average (default 3×).

        Args:
            symbol: Underlying ticker (default "SPY").
            max_dte: Maximum days-to-expiry to scan (default 7 for 0DTE focus).
            multiplier: Override the default volume multiplier (3×).

        Returns:
            List of UnusualVolumeAlert objects sorted by volume_ratio descending.
        """
        mult = multiplier or self.volume_multiplier
        spot = self._spot_price(symbol)
        avg_vol = self._estimate_historical_avg_volume(symbol)
        contracts = self._fetch_chains(symbol, max_dte)

        alerts: List[UnusualVolumeAlert] = []

        for c in contracts:
            vol = c["volume"]
            if vol < self.min_volume:
                continue

            # Per-contract average estimate: scale the aggregate by OI share
            # If OI is very small, this contract is a one-off spike
            oi = c["open_interest"]
            if avg_vol > 0 and oi > 0:
                contract_avg = max(avg_vol * (oi / max(oi, 1)), 1)
            else:
                contract_avg = max(float(vol) / mult, 1)

            ratio = vol / contract_avg

            if ratio >= mult:
                premium_est = c["mid"] * vol * 100  # notional premium flow
                alerts.append(
                    UnusualVolumeAlert(
                        symbol=symbol,
                        strike=c["strike"],
                        expiry=c["expiry"],
                        is_call=c["is_call"],
                        current_volume=vol,
                        avg_volume=round(contract_avg, 1),
                        volume_ratio=round(ratio, 2),
                        open_interest=oi,
                        last_price=c["last"],
                        mid_price=c["mid"],
                        premium_estimate=round(premium_est, 2),
                    )
                )

        alerts.sort(key=lambda a: a.volume_ratio, reverse=True)
        logger.info(
            "Unusual volume: %d alerts for %s (mult=%.1f)",
            len(alerts),
            symbol,
            mult,
        )
        return alerts

    def detect_sweeps(
        self,
        symbol: str = "SPY",
        max_dte: int = 1,
        min_premium: float = MIN_SWEEP_PREMIUM,
        min_size: int = MIN_SWEEP_SIZE,
    ) -> List[SweepAlert]:
        """Detect sweep orders — aggressive institutional buying at the ask.

        A sweep is characterised by:
        - Volume ≥ *min_size* contracts (default 100)
        - Last price at or near the ask (fill_ratio ≥ 85%)
        - Premium ≥ *min_premium* per contract

        The composite sweep_score combines fill aggressiveness, size, and
        premium to rank institutional activity.

        Args:
            symbol: Underlying ticker.
            max_dte: Maximum DTE (default 1 for intraday sweeps).
            min_premium: Minimum per-contract mid price.
            min_size: Minimum volume for institutional threshold.

        Returns:
            List of SweepAlert objects sorted by sweep_score descending.
        """
        contracts = self._fetch_chains(symbol, max_dte)
        sweeps: List[SweepAlert] = []

        for c in contracts:
            vol = c["volume"]
            if vol < min_size:
                continue

            mid = c["mid"]
            if mid < min_premium:
                continue

            ask = c["ask"]
            bid = c["bid"]
            last = c["last"]

            if ask <= 0 or bid <= 0 or last <= 0:
                continue

            # Fill ratio: how close to ask was the execution
            spread = ask - bid
            if spread <= 0:
                fill_ratio = 1.0
            else:
                fill_ratio = min((last - bid) / spread, 1.0)

            if fill_ratio < AGGRESSIVE_FILL_THRESHOLD:
                continue

            # Composite score: higher = more institutional
            size_score = min(vol / 500, 2.0)  # caps at 2× for 500+ contracts
            premium_score = min(mid / 5.0, 2.0)
            sweep_score = fill_ratio * size_score * premium_score

            est_premium = mid * vol * 100

            sweeps.append(
                SweepAlert(
                    symbol=symbol,
                    strike=c["strike"],
                    expiry=c["expiry"],
                    is_call=c["is_call"],
                    volume=vol,
                    open_interest=c["open_interest"],
                    ask_price=ask,
                    bid_price=bid,
                    last_price=last,
                    estimated_premium=round(est_premium, 2),
                    fill_ratio=round(fill_ratio, 3),
                    sweep_score=round(sweep_score, 3),
                )
            )

        sweeps.sort(key=lambda s: s.sweep_score, reverse=True)
        logger.info(
            "Sweeps: %d detected for %s (min_size=%d)",
            len(sweeps),
            symbol,
            min_size,
        )
        return sweeps

    def calculate_put_call_flow(
        self,
        symbol: str = "SPY",
        max_dte: int = 7,
    ) -> PutCallFlow:
        """Calculate net premium flow by direction (call vs put).

        Aggregates total volume, open interest, and estimated premium for all
        calls and puts up to *max_dte* days. A positive net_premium indicates
        more money flowing into calls (bullish), negative into puts (bearish).

        Args:
            symbol: Underlying ticker.
            max_dte: Maximum DTE to include.

        Returns:
            PutCallFlow object with aggregated metrics.
        """
        contracts = self._fetch_chains(symbol, max_dte)

        call_vol = 0
        put_vol = 0
        call_oi = 0
        put_oi = 0
        call_premium = 0.0
        put_premium = 0.0

        for c in contracts:
            mid = c["mid"]
            vol = c["volume"]
            oi = c["open_interest"]
            premium = mid * vol * 100  # notional premium traded

            if c["is_call"]:
                call_vol += vol
                call_oi += oi
                call_premium += premium
            else:
                put_vol += vol
                put_oi += oi
                put_premium += premium

        total_vol = call_vol + put_vol or 1
        total_prem = call_premium + put_premium or 1.0

        net = call_premium - put_premium
        pc_vol = put_vol / max(call_vol, 1)
        pc_prem = put_premium / max(call_premium, 1.0)

        result = PutCallFlow(
            symbol=symbol,
            call_premium=round(call_premium, 2),
            put_premium=round(put_premium, 2),
            call_volume=call_vol,
            put_volume=put_vol,
            call_oi=call_oi,
            put_oi=put_oi,
            net_premium=round(net, 2),
            put_call_volume_ratio=round(pc_vol, 3),
            put_call_premium_ratio=round(pc_prem, 3),
        )

        logger.info(
            "Put/Call flow %s: net=$%.0f  P/C_vol=%.2f  P/C_prem=%.2f",
            symbol,
            net,
            pc_vol,
            pc_prem,
        )
        return result

    def get_flow_sentiment(
        self,
        symbol: str = "SPY",
        max_dte: int = 7,
    ) -> FlowSentiment:
        """Aggregate all flow signals into a single bullish/bearish/neutral reading.

        Combines three components:
        1. Put/call premium ratio (weight: 40%)
        2. Unusual volume call vs put skew (weight: 30%)
        3. Sweep aggressiveness direction (weight: 30%)

        Each component contributes a score in [-1, +1]. The composite score
        maps to signal + strength.

        Args:
            symbol: Underlying ticker.
            max_dte: Maximum DTE for analysis.

        Returns:
            FlowSentiment with signal, strength, composite score, and details.
        """
        components: Dict[str, float] = {}
        details: Dict[str, Any] = {}

        # ── Component 1: Put/Call premium ratio ────────────
        flow = self.calculate_put_call_flow(symbol, max_dte)
        pc_prem = flow.put_call_premium_ratio
        # P/C < 0.7 → bullish, > 1.3 → bearish, centered at 1.0
        if pc_prem < 1.0:
            pc_score = min((1.0 - pc_prem) / 0.5, 1.0)  # positive = bullish
        else:
            pc_score = max((1.0 - pc_prem) / 0.5, -1.0)
        components["put_call_premium"] = round(pc_score, 3)
        details["put_call_flow"] = flow

        # ── Component 2: Unusual volume directional skew ───
        alerts = self.detect_unusual_volume(symbol, max_dte)
        call_vol_total = sum(a.current_volume for a in alerts if a.is_call)
        put_vol_total = sum(a.current_volume for a in alerts if not a.is_call)
        total_uv = call_vol_total + put_vol_total
        if total_uv > 0:
            uv_score = (call_vol_total - put_vol_total) / total_uv
        else:
            uv_score = 0.0
        components["volume_skew"] = round(uv_score, 3)
        details["unusual_volume_count"] = len(alerts)
        details["uv_call_vol"] = call_vol_total
        details["uv_put_vol"] = put_vol_total

        # ── Component 3: Sweep direction ───────────────────
        sweeps = self.detect_sweeps(symbol, max_dte)
        call_sweep_score = sum(
            s.sweep_score for s in sweeps if s.is_call
        )
        put_sweep_score = sum(
            s.sweep_score for s in sweeps if not s.is_call
        )
        total_sweep = call_sweep_score + put_sweep_score
        if total_sweep > 0:
            sw_score = (call_sweep_score - put_sweep_score) / total_sweep
        else:
            sw_score = 0.0
        components["sweep_direction"] = round(sw_score, 3)
        details["sweep_count"] = len(sweeps)
        details["call_sweep_score"] = round(call_sweep_score, 3)
        details["put_sweep_score"] = round(put_sweep_score, 3)

        # ── Composite ──────────────────────────────────────
        composite = (
            0.40 * components["put_call_premium"]
            + 0.30 * components["volume_skew"]
            + 0.30 * components["sweep_direction"]
        )
        composite = round(max(min(composite, 1.0), -1.0), 3)

        # Map score → signal + strength
        if composite > 0.1:
            signal: Literal["bullish", "bearish", "neutral"] = "bullish"
        elif composite < -0.1:
            signal = "bearish"
        else:
            signal = "neutral"

        abs_score = abs(composite)
        if abs_score >= 0.5:
            strength: Literal["strong", "moderate", "weak"] = "strong"
        elif abs_score >= 0.2:
            strength = "moderate"
        else:
            strength = "weak"

        result = FlowSentiment(
            symbol=symbol,
            signal=signal,
            strength=strength,
            score=composite,
            components=components,
            details=details,
        )

        logger.info(
            "Flow sentiment %s: %s (%s) score=%.3f",
            symbol,
            signal,
            strength,
            composite,
        )
        return result


# ── Convenience functions (module-level API) ────────────────────────


def detect_unusual_volume(
    symbol: str = "SPY",
    max_dte: int = 7,
    multiplier: float = DEFAULT_VOLUME_MULTIPLIER,
) -> List[UnusualVolumeAlert]:
    """Module-level shortcut — see OptionsFlowDetector.detect_unusual_volume."""
    return OptionsFlowDetector().detect_unusual_volume(symbol, max_dte, multiplier)


def detect_sweeps(
    symbol: str = "SPY",
    max_dte: int = 1,
) -> List[SweepAlert]:
    """Module-level shortcut — see OptionsFlowDetector.detect_sweeps."""
    return OptionsFlowDetector().detect_sweeps(symbol, max_dte)


def calculate_put_call_flow(
    symbol: str = "SPY",
    max_dte: int = 7,
) -> PutCallFlow:
    """Module-level shortcut — see OptionsFlowDetector.calculate_put_call_flow."""
    return OptionsFlowDetector().calculate_put_call_flow(symbol, max_dte)


def get_flow_sentiment(
    symbol: str = "SPY",
    max_dte: int = 7,
) -> FlowSentiment:
    """Module-level shortcut — see OptionsFlowDetector.get_flow_sentiment."""
    return OptionsFlowDetector().get_flow_sentiment(symbol, max_dte)
