"""Multi-Timeframe Confirmation — the edge that prevents bad entries.

Philosophy: "Daily for direction, 30min for trend, 5min for momentum, 1min for execution."
Every timeframe must agree before a trade is taken. A 0DTE call on SPY when the daily
says down, the 30m says sideways, and only the 5m says up is a recipe for a loss.

Timeframes:
  1d  — Direction (the big picture: are we in an uptrend or downtrend?)
  30m — Trend    (medium-term structure: EMAs stacked correctly?)
  5m  — Momentum (short-term RSI/MACD: are we oversold in an uptrend?)
  1m  — Execution (tick-level VWAP: where exactly do we enter?)

Each function returns a dict with structured results. combine_all() gives
the final go/no-go with alignment score.

Uses yfinance for data (same source as market_regime.py). Indicator
parameters match indicators_config.py (RSI, MACD settings per timeframe).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

logger = logging.getLogger("hermes_trader.multi_timeframe")

# ── Timeframe definitions ──────────────────────────────────────────────
# yfinance interval → period mapping (yfinance has hard limits on how much
# intraday data you can request at each interval)
TIMEFRAME_CONFIGS = {
    "1d":  {"interval": "1d",  "period": "3mo",  "min_bars": 50,  "label": "Daily"},
    "30m": {"interval": "30m", "period": "60d",  "min_bars": 40,  "label": "30-Minute"},
    "5m":  {"interval": "5m",  "period": "10d",  "min_bars": 30,  "label": "5-Minute"},
    "1m":  {"interval": "1m",  "period": "5d",   "min_bars": 20,  "label": "1-Minute"},
}

# ── EMA/SMA periods per timeframe ─────────────────────────────────────
TREND_EMAS = {
    "1d":  {"fast": 20, "slow": 50, "sma": 200},
    "30m": {"fast": 9,  "slow": 21, "sma": 50},
    "5m":  {"fast": 8,  "slow": 21, "sma": 50},
    "1m":  {"fast": 5,  "slow": 13, "sma": 30},
}

# ── RSI settings per timeframe (from indicators_config.py) ─────────────
RSI_CONFIG = {
    "1d":  {"period": 14, "overbought": 70, "oversold": 30},
    "30m": {"period": 14, "overbought": 70, "oversold": 30},
    "5m":  {"period": 9,  "overbought": 70, "oversold": 30},
    "1m":  {"period": 5,  "overbought": 80, "oversold": 20},
}

# ── MACD settings per timeframe (from indicators_config.py) ────────────
MACD_CONFIG = {
    "1d":  {"fast": 12, "slow": 26, "signal": 9},
    "30m": {"fast": 12, "slow": 26, "signal": 9},
    "5m":  {"fast": 8,  "slow": 21, "signal": 5},
    "1m":  {"fast": 3,  "slow": 8,  "signal": 3},
}

# ── Weight per timeframe for alignment scoring ─────────────────────────
# Higher timeframe = more weight (daily direction matters most)
TIMEFRAME_WEIGHTS = {
    "1d":  0.35,   # Direction is king
    "30m": 0.30,   # Trend confirms direction
    "5m":  0.20,   # Momentum timing
    "1m":  0.15,   # Execution precision
}


# ═════════════════════════════════════════════════════════════════════════
# 1. get_timeframe_data()
# ═════════════════════════════════════════════════════════════════════════

def get_timeframe_data(
    symbol: str = "SPY",
    timeframes: Optional[list[str]] = None,
) -> dict[str, Optional[object]]:
    """Fetch OHLCV data for each timeframe via yfinance.

    Args:
        symbol: Ticker symbol (default "SPY")
        timeframes: Which timeframes to fetch. Defaults to all four.

    Returns:
        Dict mapping timeframe key ("1d", "30m", "5m", "1m") to a
        pandas DataFrame of OHLCV data, or None if fetch failed.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — cannot fetch timeframe data")
        return {}

    if timeframes is None:
        timeframes = list(TIMEFRAME_CONFIGS.keys())

    result = {}

    for tf in timeframes:
        cfg = TIMEFRAME_CONFIGS.get(tf)
        if cfg is None:
            logger.warning(f"Unknown timeframe '{tf}', skipping")
            result[tf] = None
            continue

        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=cfg["period"], interval=cfg["interval"])

            if data is None or data.empty or len(data) < cfg["min_bars"]:
                logger.warning(
                    f"{symbol} {tf}: got {0 if data is None else len(data)} bars, "
                    f"need ≥{cfg['min_bars']}"
                )
                result[tf] = None
                continue

            # Drop any NaN rows that would break indicator calcs
            data = data.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
            result[tf] = data
            logger.debug(f"{symbol} {tf}: {len(data)} bars loaded")

        except Exception as e:
            logger.error(f"{symbol} {tf}: fetch failed — {e}")
            result[tf] = None

    return result


# ═════════════════════════════════════════════════════════════════════════
# 2. analyze_trend()
# ═════════════════════════════════════════════════════════════════════════

def analyze_trend(
    data: dict[str, Optional[object]],
) -> dict[str, dict]:
    """Analyze EMA/SMA crossover trend for each timeframe.

    For each timeframe with valid data, computes:
      - Fast EMA vs Slow EMA crossover → bullish/bearish
      - Price vs SMA → trend confirmation
      - Trend strength (distance between EMAs as % of price)
      - Recent slope (is the trend accelerating or decelerating?)

    Returns:
        Dict mapping timeframe to trend analysis dict:
        {
            "1d": {
                "direction": "bullish" | "bearish" | "neutral",
                "ema_cross": "bullish" | "bearish" | "neutral",
                "sma_confirmed": True | False,
                "trend_strength": float,  # 0-100 score
                "ema_spread_pct": float,  # fast-slow as % of price
                "slope": float,           # recent EMA slope (%)
                "bars_above_sma": int,
            },
            ...
        }
    """
    results = {}

    for tf, df in data.items():
        if df is None or df.empty:
            results[tf] = {
                "direction": "unknown",
                "ema_cross": "unknown",
                "sma_confirmed": False,
                "trend_strength": 0,
                "ema_spread_pct": 0,
                "slope": 0,
                "bars_above_sma": 0,
            }
            continue

        periods = TREND_EMAS.get(tf, {"fast": 9, "slow": 21, "sma": 50})
        close = df["Close"]
        price = float(close.iloc[-1])

        # ── EMAs ──
        ema_fast = close.ewm(span=periods["fast"], adjust=False).mean()
        ema_slow = close.ewm(span=periods["slow"], adjust=False).mean()

        fast_now = float(ema_fast.iloc[-1])
        slow_now = float(ema_slow.iloc[-1])
        fast_prev = float(ema_fast.iloc[-2]) if len(ema_fast) >= 2 else fast_now
        slow_prev = float(ema_slow.iloc[-2]) if len(ema_slow) >= 2 else slow_now

        # ── SMA (long-term trend filter) ──
        sma_period = min(periods["sma"], len(close))
        sma = close.rolling(sma_period).mean()
        sma_now = float(sma.iloc[-1]) if not np.isnan(sma.iloc[-1]) else None

        # ── EMA crossover detection ──
        # Current state
        currently_bull = fast_now > slow_now
        currently_bear = fast_now < slow_now
        # Previous state
        prev_bull = fast_prev > slow_prev
        prev_bear = fast_prev < slow_prev

        if currently_bull and not prev_bull:
            ema_cross = "golden_cross"  # Fresh bullish crossover
        elif currently_bear and not prev_bear:
            ema_cross = "death_cross"   # Fresh bearish crossover
        elif currently_bull:
            ema_cross = "bullish"       # Already above
        elif currently_bear:
            ema_cross = "bearish"       # Already below
        else:
            ema_cross = "neutral"

        # ── Overall direction (EMA cross + SMA confirmation) ──
        sma_confirmed = sma_now is not None and (
            (currently_bull and price > sma_now) or
            (currently_bear and price < sma_now)
        )

        if currently_bull and (sma_now is None or sma_now == 0 or price > sma_now):
            direction = "bullish"
        elif currently_bear and (sma_now is None or sma_now == 0 or price < sma_now):
            direction = "bearish"
        else:
            direction = "neutral"

        # ── Trend strength (0-100) ──
        # Based on EMA spread and how far price is above/below
        ema_spread = (fast_now - slow_now) / price * 100 if price > 0 else 0
        ema_spread_abs = abs(ema_spread)

        # Score: 0% spread = 0, >1% spread = 100
        spread_score = min(100, ema_spread_abs * 100)

        # Bonus for SMA alignment
        if sma_confirmed:
            spread_score = min(100, spread_score * 1.2)

        # ── Slope (EMA fast direction over last 5 bars) ──
        lookback = min(5, len(ema_fast) - 1)
        if lookback > 0:
            slope = (float(ema_fast.iloc[-1]) - float(ema_fast.iloc[-1 - lookback])) / float(ema_fast.iloc[-1 - lookback]) * 100
        else:
            slope = 0.0

        # ── Bars above/below SMA ──
        bars_above = 0
        if sma_now is not None:
            for i in range(len(close) - 1, -1, -1):
                if np.isnan(sma.iloc[i]):
                    break
                if float(close.iloc[i]) > float(sma.iloc[i]):
                    bars_above += 1
                else:
                    break

        results[tf] = {
            "direction": direction,
            "ema_cross": ema_cross,
            "sma_confirmed": sma_confirmed,
            "trend_strength": round(spread_score, 1),
            "ema_spread_pct": round(ema_spread, 4),
            "slope": round(slope, 4),
            "bars_above_sma": bars_above,
            "price": round(price, 2),
            "ema_fast": round(fast_now, 2),
            "ema_slow": round(slow_now, 2),
            "sma": round(sma_now, 2) if sma_now is not None else None,
        }

    return results


# ═════════════════════════════════════════════════════════════════════════
# 3. calculate_alignment()
# ═════════════════════════════════════════════════════════════════════════

def calculate_alignment(
    trend_results: dict[str, dict],
    direction_override: Optional[str] = None,
) -> dict:
    """Score how many timeframes agree on direction.

    Uses weighted scoring — daily counts for more than 1-minute.
    Alignment score 0-100:
      - 80-100: STRONG alignment → high-confidence trade
      - 60-79:  MODERATE alignment → trade with caution
      - 40-59:  WEAK alignment → likely skip
      - 0-39:   NO alignment → no trade

    Also counts "weight-aligned" percentage for finer granularity.

    Args:
        trend_results: Output from analyze_trend()
        direction_override: Force evaluation in this direction ("bullish"/"bearish")
                           rather than auto-detecting.

    Returns:
        {
            "alignment_score": float,       # 0-100
            "alignment_label": str,         # "STRONG"/"MODERATE"/"WEAK"/"NONE"
            "direction": str,               # "bullish" or "bearish" (dominant)
            "weighted_pct": float,          # % of weight aligned
            "timeframes_aligned": int,      # Count of agreeing TFs
            "timeframes_total": int,        # Count of TFs with data
            "timeframe_details": {...},     # Per-TF alignment to dominant direction
            "strongest_tf": str,            # TF with highest trend strength
            "weakest_tf": str,              # TF with lowest trend strength
        }
    """
    # Collect valid timeframes and their directions
    tf_directions = {}
    for tf, result in trend_results.items():
        if result.get("direction") not in ("unknown", "neutral"):
            tf_directions[tf] = result["direction"]

    if not tf_directions:
        return {
            "alignment_score": 0,
            "alignment_label": "NONE",
            "direction": "none",
            "weighted_pct": 0,
            "timeframes_aligned": 0,
            "timeframes_total": 0,
            "timeframe_details": {},
            "strongest_tf": None,
            "weakest_tf": None,
        }

    # Determine dominant direction
    if direction_override:
        dominant = direction_override
    else:
        # Weighted vote
        bull_weight = sum(
            TIMEFRAME_WEIGHTS.get(tf, 0.1) for tf, d in tf_directions.items()
            if d == "bullish"
        )
        bear_weight = sum(
            TIMEFRAME_WEIGHTS.get(tf, 0.1) for tf, d in tf_directions.items()
            if d == "bearish"
        )
        dominant = "bullish" if bull_weight >= bear_weight else "bearish"

    # Score each TF against the dominant direction
    aligned_weight = 0.0
    total_weight = 0.0
    aligned_count = 0
    tf_details = {}
    strongest = {"tf": None, "strength": -1}
    weakest = {"tf": None, "strength": 101}

    for tf, result in trend_results.items():
        if result.get("direction") == "unknown":
            continue

        tf_weight = TIMEFRAME_WEIGHTS.get(tf, 0.1)
        total_weight += tf_weight

        is_aligned = result["direction"] == dominant
        strength = result.get("trend_strength", 0)

        tf_details[tf] = {
            "direction": result["direction"],
            "aligned": is_aligned,
            "trend_strength": strength,
            "ema_cross": result.get("ema_cross", "unknown"),
        }

        if is_aligned:
            aligned_weight += tf_weight
            aligned_count += 1

        if strength > strongest["strength"]:
            strongest = {"tf": tf, "strength": strength}
        if strength < weakest["strength"]:
            weakest = {"tf": tf, "strength": strength}

    weighted_pct = (aligned_weight / total_weight * 100) if total_weight > 0 else 0

    # Alignment score: weighted percentage is the core metric
    # But we also penalize if a critical timeframe (daily) disagrees
    daily_result = trend_results.get("1d", {})
    daily_aligned = daily_result.get("direction") == dominant if daily_result.get("direction") != "unknown" else False

    alignment_score = weighted_pct

    # Hard penalty: if daily disagrees, cap at 50 regardless of other TFs
    if not daily_aligned and "1d" in trend_results and trend_results["1d"].get("direction") != "unknown":
        alignment_score = min(alignment_score, 50)

    # Soft bonus: all 4 aligned → +10
    valid_tfs = [tf for tf, r in trend_results.items() if r.get("direction") not in ("unknown", "neutral")]
    if len(valid_tfs) == 4 and aligned_count == len(valid_tfs):
        alignment_score = min(100, alignment_score + 10)

    # Label
    if alignment_score >= 80:
        label = "STRONG"
    elif alignment_score >= 60:
        label = "MODERATE"
    elif alignment_score >= 40:
        label = "WEAK"
    else:
        label = "NONE"

    return {
        "alignment_score": round(alignment_score, 1),
        "alignment_label": label,
        "direction": dominant,
        "weighted_pct": round(weighted_pct, 1),
        "timeframes_aligned": aligned_count,
        "timeframes_total": len(valid_tfs),
        "timeframe_details": tf_details,
        "strongest_tf": strongest["tf"],
        "weakest_tf": weakest["tf"],
    }


# ═════════════════════════════════════════════════════════════════════════
# 4. get_momentum()
# ═════════════════════════════════════════════════════════════════════════

def get_momentum(
    data: dict[str, Optional[object]],
) -> dict[str, dict]:
    """Calculate RSI and MACD for each timeframe.

    RSI periods and thresholds match indicators_config.py:
      1m: RSI(5), OB=80, OS=20
      5m: RSI(9), OB=70, OS=30
      30m/1d: RSI(14), OB=70, OS=30

    MACD settings also from indicators_config.py:
      1m: (3,8,3), 5m: (8,21,5), 30m/1d: (12,26,9)

    Returns:
        Dict mapping timeframe to:
        {
            "rsi": float,
            "rsi_signal": "overbought" | "oversold" | "neutral",
            "rsi_bullish": bool,     # RSI supports bullish
            "macd_line": float,
            "macd_signal_line": float,
            "macd_histogram": float,
            "macd_cross": "bullish" | "bearish" | "none",
            "macd_bullish": bool,    # MACD supports bullish
            "momentum_aligned": bool, # RSI + MACD both agree
        }
    """
    results = {}

    for tf, df in data.items():
        if df is None or df.empty:
            results[tf] = {
                "rsi": 50.0,
                "rsi_signal": "neutral",
                "rsi_bullish": True,
                "macd_line": 0.0,
                "macd_signal_line": 0.0,
                "macd_histogram": 0.0,
                "macd_cross": "none",
                "macd_bullish": True,
                "momentum_aligned": True,
            }
            continue

        close = df["Close"]
        rsi_cfg = RSI_CONFIG.get(tf, {"period": 14, "overbought": 70, "oversold": 30})
        macd_cfg = MACD_CONFIG.get(tf, {"fast": 12, "slow": 26, "signal": 9})

        # ── RSI ──
        rsi = _compute_rsi(close, period=rsi_cfg["period"])
        rsi_now = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0

        if rsi_now >= rsi_cfg["overbought"]:
            rsi_signal = "overbought"
        elif rsi_now <= rsi_cfg["oversold"]:
            rsi_signal = "oversold"
        else:
            rsi_signal = "neutral"

        # RSI supports bullish when oversold (bounce coming) or neutral
        # RSI supports bearish when overbought (reversal coming) or neutral
        # For trending: RSI > 50 = bullish, < 50 = bearish
        rsi_bullish = rsi_now > 50

        # ── MACD ──
        ema_fast = close.ewm(span=macd_cfg["fast"], adjust=False).mean()
        ema_slow = close.ewm(span=macd_cfg["slow"], adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=macd_cfg["signal"], adjust=False).mean()
        histogram = macd_line - signal_line

        macd_now = float(macd_line.iloc[-1]) if not np.isnan(macd_line.iloc[-1]) else 0.0
        sig_now = float(signal_line.iloc[-1]) if not np.isnan(signal_line.iloc[-1]) else 0.0
        hist_now = float(histogram.iloc[-1]) if not np.isnan(histogram.iloc[-1]) else 0.0

        # MACD cross detection
        if len(macd_line) >= 2 and not np.isnan(macd_line.iloc[-2]):
            macd_prev = float(macd_line.iloc[-2])
            sig_prev = float(signal_line.iloc[-2])
        else:
            macd_prev = macd_now
            sig_prev = sig_now

        if macd_now > sig_now and macd_prev <= sig_prev:
            macd_cross = "bullish"
        elif macd_now < sig_now and macd_prev >= sig_prev:
            macd_cross = "bearish"
        elif macd_now > sig_now:
            macd_cross = "bullish"
        elif macd_now < sig_now:
            macd_cross = "bearish"
        else:
            macd_cross = "none"

        macd_bullish = macd_now > sig_now  # MACD above signal = bullish

        # ── Combined momentum signal ──
        momentum_aligned = rsi_bullish == macd_bullish

        results[tf] = {
            "rsi": round(rsi_now, 1),
            "rsi_signal": rsi_signal,
            "rsi_bullish": rsi_bullish,
            "macd_line": round(macd_now, 4),
            "macd_signal_line": round(sig_now, 4),
            "macd_histogram": round(hist_now, 4),
            "macd_cross": macd_cross,
            "macd_bullish": macd_bullish,
            "momentum_aligned": momentum_aligned,
        }

    return results


def _compute_rsi(close, period: int = 14):
    """Compute RSI using Wilder's smoothing (EMA-based)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))

    # Wilder's smoothing = EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ═════════════════════════════════════════════════════════════════════════
# 5. get_vwap_bands()
# ═════════════════════════════════════════════════════════════════════════

def get_vwap_bands(
    data: dict[str, Optional[object]],
    num_std: float = 2.0,
) -> dict[str, dict]:
    """Calculate VWAP with standard deviation bands for each timeframe.

    VWAP = Σ(price × volume) / Σ(volume)
    Bands: VWAP ± N × σ where σ = std dev of cumulative price deviations.

    Uses the same band multipliers as indicators_config.py:
      1σ = 1.0, 2σ = 2.0

    For non-intraday timeframes (1d), VWAP resets each bar
    so we compute a "rolling VWAP" using the last N bars instead.

    Returns:
        Dict mapping timeframe to:
        {
            "vwap": float,
            "upper_1s": float,    # +1σ band
            "lower_1s": float,    # -1σ band
            "upper_2s": float,    # +2σ band
            "lower_2s": float,    # -2σ band
            "price_position": str,  # "above_vwap"/"below_vwap"/"at_vwap"
            "band_width_pct": float,  # Total band width as % of VWAP
            "price_in_band": int,  # Which band price is in (0=below 2σ, 1=between 1σ-2σ, 2=inside 1σ, 3=above 1σ)
            "vwap_distance_pct": float,  # Price distance from VWAP as %
        }
    """
    results = {}

    for tf, df in data.items():
        if df is None or df.empty:
            results[tf] = {
                "vwap": 0.0,
                "upper_1s": 0.0,
                "lower_1s": 0.0,
                "upper_2s": 0.0,
                "lower_2s": 0.0,
                "price_position": "unknown",
                "band_width_pct": 0.0,
                "price_in_band": -1,
                "vwap_distance_pct": 0.0,
            }
            continue

        # Use typical price: (H + L + C) / 3
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
        volume = df["Volume"]

        is_intraday = tf in ("1m", "5m", "30m")

        if is_intraday:
            # True VWAP: cumulative from session start
            vwap_val, upper_1s, lower_1s, upper_2s, lower_2s = _compute_session_vwap(
                typical_price, volume, num_std=num_std
            )
        else:
            # Rolling VWAP for daily (rolling 20-bar window)
            window = min(20, len(typical_price))
            vwap_val, upper_1s, lower_1s, upper_2s, lower_2s = _compute_rolling_vwap(
                typical_price, volume, window=window, num_std=num_std
            )

        price = float(df["Close"].iloc[-1])

        # Price position relative to VWAP
        if vwap_val > 0:
            vwap_dist_pct = ((price - vwap_val) / vwap_val) * 100
        else:
            vwap_dist_pct = 0.0

        if price > vwap_val * 1.001:
            price_position = "above_vwap"
        elif price < vwap_val * 0.999:
            price_position = "below_vwap"
        else:
            price_position = "at_vwap"

        # Which band is price in?
        if price > upper_1s:
            price_in_band = 3    # Above +1σ (very extended)
        elif price > vwap_val:
            price_in_band = 2    # Between VWAP and +1σ (normal bullish)
        elif price > lower_1s:
            price_in_band = 1    # Between VWAP and -1σ (normal bearish)
        elif price > lower_2s:
            price_in_band = -1   # Between -1σ and -2σ (oversold)
        else:
            price_in_band = -2   # Below -2σ (extremely oversold)

        # Band width as % of VWAP
        if vwap_val > 0:
            band_width_pct = ((upper_2s - lower_2s) / vwap_val) * 100
        else:
            band_width_pct = 0.0

        results[tf] = {
            "vwap": round(vwap_val, 2),
            "upper_1s": round(upper_1s, 2),
            "lower_1s": round(lower_1s, 2),
            "upper_2s": round(upper_2s, 2),
            "lower_2s": round(lower_2s, 2),
            "price_position": price_position,
            "band_width_pct": round(band_width_pct, 3),
            "price_in_band": price_in_band,
            "vwap_distance_pct": round(vwap_dist_pct, 4),
        }

    return results


def _compute_session_vwap(
    typical_price,
    volume,
    num_std: float = 2.0,
):
    """Compute cumulative VWAP with standard deviation bands.

    VWAP_i = Σ(TP × V)[0..i] / Σ(V)[0..i]
    σ_i = sqrt(Σ(V × (TP - VWAP)²)[0..i] / Σ(V)[0..i])
    """
    tp_vals = typical_price.values
    vol_vals = volume.values

    cum_tp_vol = np.cumsum(tp_vals * vol_vals)
    cum_vol = np.cumsum(vol_vals)

    # Avoid division by zero
    cum_vol_safe = np.where(cum_vol == 0, 1, cum_vol)
    vwap = cum_tp_vol / cum_vol_safe

    # Standard deviation bands
    cum_var = np.cumsum(vol_vals * (tp_vals - vwap) ** 2)
    std_dev = np.sqrt(cum_var / cum_vol_safe)

    # Latest values
    vwap_now = float(vwap[-1]) if not np.isnan(vwap[-1]) else float(typical_price.iloc[-1])
    std_now = float(std_dev[-1]) if not np.isnan(std_dev[-1]) else 0.0

    return (
        vwap_now,
        vwap_now + num_std * std_now,      # upper 1σ (using num_std=1 for 1σ)
        vwap_now - num_std * std_now,
        vwap_now + 2 * num_std * std_now,   # upper 2σ
        vwap_now - 2 * num_std * std_now,
    )


def _compute_rolling_vwap(
    typical_price,
    volume,
    window: int = 20,
    num_std: float = 2.0,
):
    """Compute rolling-window VWAP with standard deviation bands.

    Uses the last `window` bars for a pseudo-VWAP on non-intraday data.
    """
    tp_vals = typical_price.values
    vol_vals = volume.values

    if len(tp_vals) < window:
        window = len(tp_vals)

    # Rolling sums
    tp_vol = tp_vals * vol_vals
    cum_tp_vol = np.nancumsum(tp_vol)
    cum_vol = np.nancumsum(vol_vals)

    # Use only last `window` bars
    if len(cum_tp_vol) >= window:
        window_tp_vol = cum_tp_vol[-1] - (cum_tp_vol[-window - 1] if len(cum_tp_vol) > window else 0)
        window_vol = cum_vol[-1] - (cum_vol[-window - 1] if len(cum_vol) > window else 0)
    else:
        window_tp_vol = cum_tp_vol[-1]
        window_vol = cum_vol[-1]

    if window_vol == 0:
        window_vol = 1

    vwap_now = window_tp_vol / window_vol

    # Std dev over same window
    window_tp = tp_vals[-window:]
    window_vol_arr = vol_vals[-window:]
    squared_devs = window_vol_arr * (window_tp - vwap_now) ** 2
    std_now = float(np.sqrt(np.sum(squared_devs) / window_vol))

    return (
        vwap_now,
        vwap_now + std_now,
        vwap_now - std_now,
        vwap_now + 2 * std_now,
        vwap_now - 2 * std_now,
    )


# ═════════════════════════════════════════════════════════════════════════
# COMBINED: The one-call interface
# ═════════════════════════════════════════════════════════════════════════

def combine_all(
    symbol: str = "SPY",
    direction: Optional[str] = None,
) -> dict:
    """Run the full multi-timeframe analysis in one call.

    Fetches data for all 4 timeframes, runs trend analysis, momentum,
    VWAP bands, and alignment scoring. Returns everything in one dict
    for the trading engine to consume.

    Args:
        symbol: Ticker to analyze (default "SPY")
        direction: Force direction ("bullish"/"bearish") for alignment scoring.
                   If None, auto-detects from weighted vote.

    Returns:
        {
            "symbol": str,
            "timestamp": str,
            "timeframe_data": {tf: DataFrame or None},
            "trend": {tf: trend_analysis},
            "momentum": {tf: momentum_analysis},
            "vwap": {tf: vwap_analysis},
            "alignment": {alignment_result},
            "go_trade": bool,            # True if alignment ≥ 60 AND momentum agrees
            "trade_direction": str,      # "bullish" or "bearish" or "none"
            "confidence": str,           # "HIGH"/"MEDIUM"/"LOW"/"NONE"
            "reasons": list[str],        # Human-readable reasoning
        }
    """
    reasons = []

    # ── Step 1: Fetch data ──
    timeframe_data = get_timeframe_data(symbol)

    available_tfs = [tf for tf, df in timeframe_data.items() if df is not None]
    if not available_tfs:
        return _no_trade_result(symbol, ["No timeframe data available"])

    reasons.append(f"Data loaded for {len(available_tfs)}/4 timeframes: {', '.join(available_tfs)}")

    # ── Step 2: Trend analysis ──
    trend = analyze_trend(timeframe_data)

    for tf, t in trend.items():
        if t["direction"] != "unknown":
            reasons.append(
                f"{TIMEFRAME_CONFIGS.get(tf, {}).get('label', tf)}: "
                f"{t['direction']} (EMA {t['ema_cross']}, strength {t['trend_strength']})"
            )

    # ── Step 3: Alignment ──
    alignment = calculate_alignment(trend, direction_override=direction)
    reasons.append(
        f"Alignment: {alignment['alignment_label']} ({alignment['alignment_score']}/100) "
        f"— {alignment['timeframes_aligned']}/{alignment['timeframes_total']} TFs agree"
    )

    # ── Step 4: Momentum ──
    momentum = get_momentum(timeframe_data)

    # Check if momentum on shorter TFs confirms the alignment direction
    dominant = alignment["direction"]
    momentum_confirm_count = 0
    momentum_total = 0

    for tf in ("5m", "1m"):  # Short-term TFs for timing
        if tf in momentum:
            momentum_total += 1
            m = momentum[tf]
            if dominant == "bullish" and m["rsi_bullish"] and m["macd_bullish"]:
                momentum_confirm_count += 1
            elif dominant == "bearish" and not m["rsi_bullish"] and not m["macd_bullish"]:
                momentum_confirm_count += 1

    if momentum_total > 0:
        reasons.append(
            f"Momentum: {momentum_confirm_count}/{momentum_total} short-TF indicators "
            f"confirm {dominant}"
        )

    # ── Step 5: VWAP ──
    vwap = get_vwap_bands(timeframe_data)

    # VWAP on 1m/5m for execution timing
    for tf in ("1m", "5m"):
        if tf in vwap:
            v = vwap[tf]
            reasons.append(
                f"{TIMEFRAME_CONFIGS.get(tf, {}).get('label', tf)} VWAP: "
                f"${v['vwap']:.2f}, price {v['price_position']} "
                f"({v['vwap_distance_pct']:+.3f}%)"
            )

    # ── Step 6: Go/no-go decision ──
    alignment_score = alignment["alignment_score"]
    daily_trend = trend.get("1d", {}).get("direction", "unknown")

    go_trade = False
    confidence = "NONE"

    if alignment_score >= 80 and momentum_confirm_count == momentum_total and momentum_total > 0:
        go_trade = True
        confidence = "HIGH"
        reasons.append(f"✅ HIGH CONFIDENCE: Strong alignment + momentum confirmation")
    elif alignment_score >= 60 and momentum_confirm_count >= 1:
        go_trade = True
        confidence = "MEDIUM"
        reasons.append(f"⚠️ MEDIUM CONFIDENCE: Moderate alignment + some momentum")
    elif alignment_score >= 60:
        go_trade = True
        confidence = "LOW"
        reasons.append(f"⚠️ LOW CONFIDENCE: Alignment OK but momentum unclear")
    else:
        reasons.append(f"❌ NO TRADE: Alignment too weak ({alignment_score:.0f}/100)")

    trade_direction = dominant if go_trade else "none"

    return {
        "symbol": symbol,
        "timestamp": datetime.utcnow().isoformat(),
        "timeframe_data": timeframe_data,
        "trend": trend,
        "momentum": momentum,
        "vwap": vwap,
        "alignment": alignment,
        "go_trade": go_trade,
        "trade_direction": trade_direction,
        "confidence": confidence,
        "reasons": reasons,
    }


def _no_trade_result(symbol: str, reasons: list[str]) -> dict:
    """Return a no-trade result dict."""
    return {
        "symbol": symbol,
        "timestamp": datetime.utcnow().isoformat(),
        "timeframe_data": {},
        "trend": {},
        "momentum": {},
        "vwap": {},
        "alignment": {
            "alignment_score": 0,
            "alignment_label": "NONE",
            "direction": "none",
            "weighted_pct": 0,
            "timeframes_aligned": 0,
            "timeframes_total": 0,
            "timeframe_details": {},
            "strongest_tf": None,
            "weakest_tf": None,
        },
        "go_trade": False,
        "trade_direction": "none",
        "confidence": "NONE",
        "reasons": reasons,
    }


# ═════════════════════════════════════════════════════════════════════════
# CLI entrypoint
# ═════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    print(f"\n{'='*60}")
    print(f"  MULTI-TIMEFRAME ANALYSIS: {symbol}")
    print(f"{'='*60}\n")

    result = combine_all(symbol)

    # Print summary
    for reason in result["reasons"]:
        print(f"  {reason}")

    print(f"\n{'─'*60}")
    a = result["alignment"]
    print(f"  ALIGNMENT SCORE: {a['alignment_score']}/100 — {a['alignment_label']}")
    print(f"  DIRECTION:       {a['direction']}")
    print(f"  TIMEFRAMES:      {a['timeframes_aligned']}/{a['timeframes_total']} agree")
    print(f"  STRONGEST:       {a['strongest_tf']}")
    print(f"  WEAKEST:         {a['weakest_tf']}")
    print(f"{'─'*60}")
    print(f"  GO TRADE:        {'YES ✅' if result['go_trade'] else 'NO ❌'}")
    print(f"  CONFIDENCE:      {result['confidence']}")
    print(f"  DIRECTION:       {result['trade_direction']}")
    print(f"{'='*60}\n")

    # Print per-TF detail
    print("  Per-Timeframe Detail:")
    for tf in ["1d", "30m", "5m", "1m"]:
        t = result["trend"].get(tf, {})
        m = result["momentum"].get(tf, {})
        v = result["vwap"].get(tf, {})
        label = TIMEFRAME_CONFIGS.get(tf, {}).get("label", tf)
        print(f"\n  {label}:")
        print(f"    Trend:    {t.get('direction', 'N/A')} | EMA: {t.get('ema_cross', 'N/A')} | "
              f"Strength: {t.get('trend_strength', 0):.0f}")
        print(f"    RSI:      {m.get('rsi', 0):.1f} ({m.get('rsi_signal', 'N/A')}) | "
              f"Bullish: {m.get('rsi_bullish', 'N/A')}")
        print(f"    MACD:     {m.get('macd_cross', 'N/A')} | "
              f"Hist: {m.get('macd_histogram', 0):.4f} | Bullish: {m.get('macd_bullish', 'N/A')}")
        print(f"    VWAP:     ${v.get('vwap', 0):.2f} | "
              f"Dist: {v.get('vwap_distance_pct', 0):+.3f}% | "
              f"Band: {v.get('price_position', 'N/A')}")
