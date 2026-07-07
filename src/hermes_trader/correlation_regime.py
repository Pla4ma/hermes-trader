"""Cross-Asset Correlation Regime — the #1 retail blind spot.

Most retail traders treat SPY, QQQ, IWM as independent. They are NOT.

In 2024-2025:
- SPY/QQQ correlation: 0.65-0.95
- IWM decouples during small-cap rotation
- VIX/VVIX correlation rises during stress

This module computes a 12-asset correlation matrix and classifies
the current regime. Different regimes need different strategies:

- RISK_ON_CORRELATED: All longs, no hedges
- MEGA_CAP_ROTATION: Prefer QQQ, avoid IWM
- DEFENSIVE: Widen stops, reduce size
- CRASH_RISK: Block new 0DTE, tighten stops 50%
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.correlation")

ET = ZoneInfo("America/New_York")

SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "XLF", "XLE", "XLK",
           "^VIX", "^VIX3M", "^VVIX"]

CACHE_FILE = "/opt/hermes-trader/data/journals/correlation_cache.json"
CACHE_TTL_SECONDS = 600  # 10 minutes

_cache = {"regime": None, "timestamp": 0}


@dataclass
class CorrelationRegime:
    """Current correlation regime + matrix."""
    timestamp: str
    mean_abs_correlation: float
    spy_qq_correlation: float
    spy_iwm_correlation: float
    spy_vix_correlation: float
    vix_vvix_correlation: float
    regime: str
    notes: List[str] = field(default_factory=list)
    
    def should_block_trades(self) -> bool:
        """Return True if regime is too dangerous for 0DTE."""
        return self.regime == "CRASH_RISK"
    
    def recommended_size_multiplier(self) -> float:
        """Get position size multiplier based on regime."""
        multipliers = {
            "RISK_ON_CORRELATED": 1.0,
            "MEGA_CAP_ROTATION": 0.85,
            "DEFENSIVE": 0.5,
            "CRASH_RISK": 0.0,
            "NEUTRAL": 0.75,
        }
        return multipliers.get(self.regime, 0.75)
    
    def preferred_underlying(self) -> str:
        """Which underlying to prefer given the regime."""
        if self.regime == "MEGA_CAP_ROTATION":
            return "QQQ"  # Mega-cap tech leads
        if self.regime == "DEFENSIVE":
            return "SPY"  # Most liquid, tightest spreads
        return "SPY"


def _classify_regime(mean_abs_corr: float, spy_qq: float, spy_iwm: float,
                     spy_vix: float, vix_vvix: float) -> tuple:
    # CRASH_RISK only when mean abs correlation is EXTREMELY high (typical market is 0.55-0.65)
    if mean_abs_corr > 0.75 and spy_vix < -0.70:
        return "CRASH_RISK", ["High correlation + strong SPY/VIX inversion"]
    if spy_qq > 0.85 and spy_iwm > 0.80:
        return "RISK_ON_CORRELATED", ["Broad risk-on, no rotation"]
    if spy_qq > 0.85 and spy_iwm < 0.50:
        return "MEGA_CAP_ROTATION", ["Mega-cap leads, small caps lagging"]
    if spy_vix < -0.30 and vix_vvix > 0.30:
        return "DEFENSIVE", ["VIX + VVIX rising = hedging demand"]
    return "NEUTRAL", []


def compute_correlation_regime(lookback: int = 60, use_cache: bool = True) -> Optional[CorrelationRegime]:
    """Compute current correlation regime.
    
    Args:
        lookback: Number of days for correlation calculation
        use_cache: Whether to use cached results
    
    Returns:
        CorrelationRegime or None if fetch fails.
    """
    now = time.time()
    
    # Check cache
    if use_cache and _cache["regime"] is not None:
        if now - _cache["timestamp"] < CACHE_TTL_SECONDS:
            return _cache["regime"]
    
    # Try file cache
    if use_cache:
        try:
            import os
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE) as f:
                    data = json.load(f)
                if now - data.get("cached_at", 0) < CACHE_TTL_SECONDS:
                    regime = CorrelationRegime(**{k: v for k, v in data.items() if k != "cached_at"})
                    _cache["regime"] = regime
                    _cache["timestamp"] = data["cached_at"]
                    return regime
        except Exception:
            pass
    
    try:
        import yfinance as yf
        import numpy as np
        import pandas as pd
        
        # Download in one batch
        data = yf.download(SYMBOLS, period=f"{lookback * 2}d", progress=False)["Close"]
        
        if data.empty or len(data) < 30:
            return None
        
        # Compute returns
        rets = np.log(data / data.shift(1)).dropna()
        if len(rets) < lookback:
            return None
        
        # Correlation matrix (last `lookback` days)
        corr = rets.tail(lookback).corr()
        
        # Extract key pairs
        def get_corr(a, b):
            try:
                return float(corr.loc[a, b])
            except (KeyError, ValueError):
                return 0.0
        
        spy_qq = get_corr("SPY", "QQQ")
        spy_iwm = get_corr("SPY", "IWM")
        spy_vix = get_corr("SPY", "^VIX")
        vix_vvix = get_corr("^VIX", "^VVIX")
        
        # Mean absolute correlation (upper triangle)
        mask = np.triu(np.ones(corr.shape), k=1).astype(bool)
        mean_abs = float(corr.where(mask).abs().mean().mean())
        
        # Classify
        regime, notes = _classify_regime(mean_abs, spy_qq, spy_iwm, spy_vix, vix_vvix)
        
        result = CorrelationRegime(
            timestamp=datetime.now(ET).isoformat(),
            mean_abs_correlation=round(mean_abs, 3),
            spy_qq_correlation=round(spy_qq, 3),
            spy_iwm_correlation=round(spy_iwm, 3),
            spy_vix_correlation=round(spy_vix, 3),
            vix_vvix_correlation=round(vix_vvix, 3),
            regime=regime,
            notes=notes,
        )
        
        # Cache
        _cache["regime"] = result
        _cache["timestamp"] = now
        try:
            import os
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                data = asdict(result)
                data["cached_at"] = now
                json.dump(data, f)
        except Exception:
            pass
        
        return result
    except Exception as e:
        logger.error(f"compute_correlation_regime failed: {e}")
        return None


if __name__ == "__main__":
    regime = compute_correlation_regime(use_cache=False)
    if regime:
        print(f"Regime: {regime.regime}")
        print(f"Mean abs corr: {regime.mean_abs_correlation:.2f}")
        print(f"SPY/QQQ: {regime.spy_qq_correlation:.2f}")
        print(f"SPY/VIX: {regime.spy_vix_correlation:.2f}")
        print(f"Block trades: {regime.should_block_trades()}")
        print(f"Size mult: {regime.recommended_size_multiplier()}")
    else:
        print("Failed to compute")
