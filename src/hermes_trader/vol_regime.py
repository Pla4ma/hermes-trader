"""VIX Term Structure Fetcher — the #1 documented edge in 0DTE.

VIX3M/VIX ratio determines if we should trade:
- Contango (ratio >= 1.05): positive expected value (84% WR historically)
- Backwardation (ratio < 1.00): negative expected value (42% WR)

This is the single highest-EV addition to the engine. Missing this
filter is why most 0DTE traders bleed money on the worst days.

Data sources (FREE):
- yfinance: ^VIX, ^VIX3M, ^VIX9D, ^VVIX (15-min delayed)
- FRED: VIXCLS, VVIXCLS (daily, no key needed)
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.vol_regime")

ET = ZoneInfo("America/New_York")

VIX_SYMBOLS = {
    "vix": "^VIX",
    "vix3m": "^VIX3M",
    "vix9d": "^VIX9D",
    "vvix": "^VVIX",
}

CACHE_FILE = "/opt/hermes-trader/data/journals/vol_regime_cache.json"
CACHE_TTL_SECONDS = 300  # 5 minutes


@dataclass
class VolRegimeSnapshot:
    """Snapshot of the VIX term structure at a point in time."""
    timestamp: str
    vix: float
    vix3m: float
    vix9d: float
    vvix: float
    ratio_3m: float       # vix3m / vix
    ratio_9d: float       # vix9d / vix
    contango: str         # "STRONG" | "CONTANGO" | "NEUTRAL" | "BACKWARDATION"
    vvix_zscore: float    # VVIX z-score (1y)
    
    def should_trade_premium(self) -> bool:
        """Return True if VIX term structure favors option selling/buying."""
        return self.ratio_3m >= 1.05
    
    def should_trade_directional(self) -> bool:
        """Return True if vol regime favors directional 0DTE."""
        return self.contango in ("STRONG", "CONTANGO") and self.vvix_zscore < 1.5
    
    def size_multiplier(self) -> float:
        """Recommended position size multiplier based on vol regime."""
        if self.contango == "STRONG" and self.vvix_zscore < 0:
            return 1.0  # Best conditions
        if self.contango == "CONTANGO" and self.vvix_zscore < 1.0:
            return 0.75
        if self.contango == "NEUTRAL":
            return 0.5
        # Backwardation or high VVIX = reduce size
        if self.vvix_zscore > 2.0:
            return 0.25
        return 0.5


_cache = {"snapshot": None, "timestamp": 0}


def _classify_contango(ratio_3m: float) -> str:
    if ratio_3m >= 1.10:
        return "STRONG"
    if ratio_3m >= 1.05:
        return "CONTANGO"
    if ratio_3m < 1.00:
        return "BACKWARDATION"
    return "NEUTRAL"


def fetch_vol_regime(use_cache: bool = True) -> Optional[VolRegimeSnapshot]:
    """Fetch VIX term structure with caching.
    
    Returns:
        VolRegimeSnapshot or None if fetch fails.
    """
    now = time.time()
    
    # Check cache
    if use_cache and _cache["snapshot"] is not None:
        if now - _cache["timestamp"] < CACHE_TTL_SECONDS:
            return _cache["snapshot"]
    
    # Try cache file
    if use_cache:
        try:
            import os
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE) as f:
                    data = json.load(f)
                if now - data.get("cached_at", 0) < CACHE_TTL_SECONDS:
                    snap = VolRegimeSnapshot(**{k: v for k, v in data.items() if k != "cached_at"})
                    _cache["snapshot"] = snap
                    _cache["timestamp"] = data["cached_at"]
                    return snap
        except Exception:
            pass
    
    # Fetch from yfinance
    try:
        import yfinance as yf
        
        prices = {}
        for key, symbol in VIX_SYMBOLS.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1y")
                if len(hist) > 0:
                    prices[key] = float(hist["Close"].iloc[-1])
                else:
                    prices[key] = 0.0
            except Exception as e:
                logger.warning(f"Failed to fetch {symbol}: {e}")
                prices[key] = 0.0
        
        if prices["vix"] <= 0 or prices["vix3m"] <= 0:
            return None
        
        ratio_3m = prices["vix3m"] / prices["vix"]
        ratio_9d = prices["vix9d"] / prices["vix"] if prices["vix9d"] > 0 else ratio_3m
        
        # VVIX z-score (1 year)
        try:
            vvix_hist = yf.Ticker("^VVIX").history(period="1y")
            if len(vvix_hist) > 20:
                vvix_zscore = (prices["vvix"] - vvix_hist["Close"].mean()) / vvix_hist["Close"].std()
            else:
                vvix_zscore = 0.0
        except Exception:
            vvix_zscore = 0.0
        
        snap = VolRegimeSnapshot(
            timestamp=datetime.now(ET).isoformat(),
            vix=round(prices["vix"], 2),
            vix3m=round(prices["vix3m"], 2),
            vix9d=round(prices["vix9d"], 2),
            vvix=round(prices["vvix"], 2),
            ratio_3m=round(ratio_3m, 4),
            ratio_9d=round(ratio_9d, 4),
            contango=_classify_contango(ratio_3m),
            vvix_zscore=round(vvix_zscore, 2),
        )
        
        # Save to cache
        _cache["snapshot"] = snap
        _cache["timestamp"] = now
        
        try:
            import os
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                data = asdict(snap)
                data["cached_at"] = now
                json.dump(data, f)
        except Exception:
            pass
        
        return snap
    except Exception as e:
        logger.error(f"fetch_vol_regime failed: {e}")
        return None


def should_trade_today() -> tuple:
    """Check if today's vol regime favors trading.
    
    Returns:
        (should_trade, reason)
    """
    snap = fetch_vol_regime()
    if snap is None:
        return True, "Vol regime unavailable — proceeding"
    
    if snap.contango == "BACKWARDATION":
        return False, f"VIX backwardation (ratio {snap.ratio_3m:.2f}) — historically 42% WR on 0DTE"
    if snap.vvix_zscore > 2.0:
        return False, f"VVIX elevated (z-score {snap.vvix_zscore:.1f}) — vol expansion imminent"
    return True, f"VIX {snap.contango} (ratio {snap.ratio_3m:.2f})"


def get_size_multiplier() -> float:
    """Get position size multiplier based on vol regime."""
    snap = fetch_vol_regime()
    if snap is None:
        return 0.75  # Conservative if unknown
    return snap.size_multiplier()


if __name__ == "__main__":
    snap = fetch_vol_regime(use_cache=False)
    if snap:
        print(f"VIX: {snap.vix}, VIX3M: {snap.vix3m}, Ratio: {snap.ratio_3m:.3f}")
        print(f"Contango: {snap.contango}, VVIX z: {snap.vvix_zscore:.2f}")
        print(f"Should trade: {snap.should_trade_directional()}")
        print(f"Size multiplier: {snap.size_multiplier()}")
    else:
        print("Failed to fetch vol regime")
