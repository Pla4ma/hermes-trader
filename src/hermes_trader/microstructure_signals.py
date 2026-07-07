"""Microstructure Signals — order book pressure + spread analysis.

For each option quote, computes:
- spread_abs / spread_pct: liquidity
- book_imbalance: (bid_size - ask_size) / (bid_size + ask_size) ∈ [-1, 1]
- microprice: (bid * ask_size + ask * bid_size) / (bid_size + ask_size)
- microprice_vs_mid: how much the microprice deviates from mid
- liquidity_score: 0-1 composite

If book_imbalance > 0.30 → BULLISH_PRESSURE
If book_imbalance < -0.30 → BEARISH_PRESSURE

This is a 10-15 second leading signal for the underlying direction.
Reference: orderbook-imbalance-indicator-hft (24★) confirms 10s OBI
predicts 10s price moves.
"""

import json
import logging
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.microstructure")

ET = ZoneInfo("America/New_York")


@dataclass
class Microstructure:
    """Microstructure snapshot for one option contract."""
    timestamp: str
    symbol: str
    option_id: str
    bid: float
    ask: float
    mid: float
    spread_abs: float
    spread_pct: float
    bid_size: int
    ask_size: int
    book_imbalance: float
    microprice: float
    microprice_vs_mid: float
    liquidity_score: float
    
    def is_tradeable(self) -> bool:
        """Return True if this contract is safe to trade."""
        return self.spread_pct < 0.10 and self.bid_size >= 10 and self.ask_size >= 10
    
    def pressure_signal(self) -> str:
        """Get direction signal from book imbalance."""
        if self.book_imbalance > 0.30:
            return "BULLISH_PRESSURE"
        if self.book_imbalance < -0.30:
            return "BEARISH_PRESSURE"
        return "NEUTRAL"
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "option_id": self.option_id,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread_pct": self.spread_pct,
            "book_imbalance": self.book_imbalance,
            "microprice": self.microprice,
            "microprice_vs_mid": self.microprice_vs_mid,
            "liquidity_score": self.liquidity_score,
            "is_tradeable": self.is_tradeable(),
            "pressure_signal": self.pressure_signal(),
        }


def compute_microstructure(quote: dict) -> Microstructure:
    """Compute microstructure from a Robinhood option quote.
    
    Args:
        quote: dict with 'bid_price', 'ask_price', 'bid_size', 'ask_size',
               'mark_price', 'instrument_id', 'symbol'
    
    Returns:
        Microstructure snapshot
    """
    bid = float(quote.get("bid_price", 0) or 0)
    ask = float(quote.get("ask_price", 0) or 0)
    bid_size = int(quote.get("bid_size", 0) or 0)
    ask_size = int(quote.get("ask_size", 0) or 0)
    mid = float(quote.get("mark_price", 0) or (bid + ask) / 2 if (bid + ask) > 0 else 0)
    
    spread_abs = ask - bid if ask > bid else 0
    spread_pct = (spread_abs / mid * 100) if mid > 0 else 100.0
    
    # Book imbalance: -1 (all ask) to +1 (all bid)
    total_size = bid_size + ask_size
    book_imbalance = (bid_size - ask_size) / total_size if total_size > 0 else 0.0
    
    # Microprice: weighted mid based on size
    microprice = ((bid * ask_size + ask * bid_size) / total_size) if total_size > 0 else mid
    
    microprice_vs_mid = microprice - mid
    
    # Liquidity score: 0-1 composite
    # Lower spread = higher score, more size = higher score
    spread_score = max(0, 1 - spread_pct / 0.20)  # 20% spread = 0 score
    size_score = min(1, total_size / 100)  # 100 contracts = max score
    liquidity_score = (spread_score * 0.6 + size_score * 0.4)
    
    return Microstructure(
        timestamp=datetime.now(ET).isoformat(),
        symbol=quote.get("symbol", ""),
        option_id=quote.get("instrument_id", ""),
        bid=bid,
        ask=ask,
        mid=mid,
        spread_abs=spread_abs,
        spread_pct=spread_pct,
        bid_size=bid_size,
        ask_size=ask_size,
        book_imbalance=book_imbalance,
        microprice=microprice,
        microprice_vs_mid=microprice_vs_mid,
        liquidity_score=liquidity_score,
    )


def filter_tradeable_contracts(microstructures: List[Microstructure]) -> List[Microstructure]:
    """Filter to only tradeable contracts."""
    return [m for m in microstructures if m.is_tradeable()]


def get_aggregate_pressure(microstructures: List[Microstructure]) -> dict:
    """Aggregate book pressure across all option contracts for a symbol."""
    if not microstructures:
        return {"signal": "NEUTRAL", "strength": 0.0, "count": 0}
    
    # Volume-weight the imbalance
    total_size = sum(m.bid_size + m.ask_size for m in microstructures)
    if total_size == 0:
        return {"signal": "NEUTRAL", "strength": 0.0, "count": len(microstructures)}
    
    weighted_imbalance = sum(
        m.book_imbalance * (m.bid_size + m.ask_size) for m in microstructures
    ) / total_size
    
    if weighted_imbalance > 0.20:
        signal = "BULLISH_PRESSURE"
    elif weighted_imbalance < -0.20:
        signal = "BEARISH_PRESSURE"
    else:
        signal = "NEUTRAL"
    
    return {
        "signal": signal,
        "strength": round(weighted_imbalance, 3),
        "count": len(microstructures),
        "total_size": total_size,
    }


if __name__ == "__main__":
    # Test
    sample_quote = {
        "bid_price": "1.50",
        "ask_price": "1.55",
        "bid_size": "50",
        "ask_size": "30",
        "mark_price": "1.525",
        "instrument_id": "test",
        "symbol": "SPY",
    }
    micro = compute_microstructure(sample_quote)
    print(f"Spread: {micro.spread_pct:.2f}%")
    print(f"Book imbalance: {micro.book_imbalance:.3f}")
    print(f"Pressure: {micro.pressure_signal()}")
    print(f"Tradeable: {micro.is_tradeable()}")
