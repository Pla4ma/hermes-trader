"""Trade Selector — picks the BEST trade using expected value, not just score.

Based on 3 professional traders' feedback (rated engine 9/10):
1. Tier system with regime-aware weighting
2. Expected Return = Prob × Reward - (1-Prob) × Risk - Spread - Theta
3. Correlation filter: no duplicate correlated trades
4. Dynamic thresholds: adapt to VIX and market regime
5. Liquidity filter: minimum volume, OI, max spread%
6. Cooldown: don't re-enter same ticker after stop-out
7. Sector diversification: AI, semis, rates, gold, financials, crypto
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("hermes_trader.trade_selector")

ET = ZoneInfo("America/New_York")

# ═══════════════════════════════════════════════════════════════
# TIER SYSTEM — based on 3 professional traders' recommendations
# ═══════════════════════════════════════════════════════════════

# Tier 1 — PRIMARY: Best liquidity, tightest spreads, trade first
TIER_1 = {"SPY", "QQQ", "IWM", "SMH"}

# Tier 2 — HIGH CONVICTION: Very liquid, bigger moves, AI/sector plays
TIER_2 = {"NVDA", "TSLA", "META", "AAPL", "AMD", "AVGO", "MU", "XLF", "TLT"}

# Tier 3 — SECONDARY / THEMATIC: Good but slower or more specialized
TIER_3 = {"AMZN", "MSFT", "GOOGL", "NFLX", "PLTR", "RTX", "XLE", "JPM"}

# Tier 4 — LEVERAGED / SPECIALTY / HEDGE: Day trades or hedges only
TIER_4 = {"TQQQ", "SOXL", "TNA", "LABU", "COIN", "IBIT", "GLD"}

TIER_BONUS = {1: 10, 2: 5, 3: 0, 4: -5}  # Points added/subtracted by tier

# ═══════════════════════════════════════════════════════════════
# CORRELATION GROUPS — avoid overweighting same trade
# ═══════════════════════════════════════════════════════════════
CORRELATION_GROUPS = {
    # Index exposure — same macro trade
    "us_equity_index": {"SPY", "QQQ", "IWM", "TQQQ"},
    # Semiconductor chain — NVDA GPUs + AVGO custom silicon + MU memory + AMD + SMH sector
    "semiconductor": {"NVDA", "AMD", "AVGO", "MU", "SMH", "SOXL"},
    # Mega-cap tech — similar drivers
    "mega_cap_tech": {"AAPL", "MSFT", "META", "AMZN", "GOOGL", "NFLX"},
    # Small-cap
    "small_cap": {"IWM", "TNA"},
    # Financials
    "financials": {"XLF", "JPM"},
    # Energy
    "energy": {"XLE"},
    # Rates — bond trade
    "rates": {"TLT"},
    # Gold — fear hedge
    "gold": {"GLD"},
    # Crypto — BTC proxy
    "crypto": {"COIN", "IBIT"},
    # Defense
    "defense": {"RTX"},
    # Biotech
    "biotech": {"LABU"},
}

# ═══════════════════════════════════════════════════════════════
# DYNAMIC THRESHOLDS — adapt to market conditions
# ═══════════════════════════════════════════════════════════════
def get_dynamic_threshold(vix_level: float = 20.0) -> dict:
    """Return probability and EV thresholds based on VIX level.
    
    Low vol (VIX < 15): 52-55% probability OK
    Normal (VIX 15-25): 55-58%
    High vol (VIX 25-35): 58-62%
    Extreme (VIX > 35): 62-65%
    """
    if vix_level < 15:
        return {"min_prob": 0.52, "min_ev": 0.08, "label": "quiet"}
    elif vix_level < 25:
        return {"min_prob": 0.55, "min_ev": 0.10, "label": "normal"}
    elif vix_level < 35:
        return {"min_prob": 0.58, "min_ev": 0.12, "label": "high_vol"}
    else:
        return {"min_prob": 0.62, "min_ev": 0.15, "label": "extreme"}


# ═══════════════════════════════════════════════════════════════
# EXPECTED RETURN — the real decision metric
# ═══════════════════════════════════════════════════════════════
def calculate_expected_return(
    probability: float,
    reward_pct: float,
    risk_pct: float,
    spread_pct: float = 0.0,
    theta_decay_pct: float = 0.0,
) -> float:
    """Calculate expected return accounting for real costs.
    
    Expected Return = Probability × Reward - (1-Probability) × Risk - Spread - Theta
    
    This is better than just "score" because:
    - A trade with 89 score but inflated premium may have worse EV
    - Spread cost matters for illiquid options
    - Theta decay kills 0DTE after 2 PM
    """
    ev = (probability * reward_pct) - ((1 - probability) * risk_pct) - spread_pct - theta_decay_pct
    return round(ev, 4)


# ═══════════════════════════════════════════════════════════════
# LIQUIDITY FILTER — minimum standards
# ═══════════════════════════════════════════════════════════════
def check_liquidity(candidate: dict) -> tuple:
    """Check if a candidate meets minimum liquidity standards.
    
    Returns (passes, reason).
    """
    volume = candidate.get("volume", 0) or 0
    open_interest = candidate.get("open_interest", 0) or 0
    spread_pct = candidate.get("spread_pct", 0) or 0
    mid_price = candidate.get("mid", 0) or 0
    
    if volume < 100:
        return False, f"Liquidity: volume {volume} < 100 minimum"
    if open_interest < 50:
        return False, f"Liquidity: OI {open_interest} < 50 minimum"
    if spread_pct > 10:
        return False, f"Liquidity: spread {spread_pct:.1f}% > 10% max"
    if mid_price < 0.10:
        return False, f"Liquidity: price ${mid_price:.2f} too low (< $0.10)"
    
    return True, ""


# ═══════════════════════════════════════════════════════════════
# CORRELATION FILTER — avoid duplicate trades
# ═══════════════════════════════════════════════════════════════
def filter_correlated(candidates: List[dict]) -> List[dict]:
    """Remove correlated duplicates — keep only the best from each group.
    
    If SPY and QQQ are both bullish calls, keep only the higher-ranked one.
    If TQQQ, UPRO, and SPXL are all bullish, keep only the best.
    """
    if not candidates:
        return candidates
    
    # Track which correlation group's trade we've already taken
    used_groups = {}  # group_name -> (direction, symbol)
    filtered = []
    
    for c in candidates:
        sym = c.get("symbol", "")
        direction = c.get("type", "")  # "call" or "put"
        
        # Find which group this symbol belongs to
        group = None
        for g_name, g_members in CORRELATION_GROUPS.items():
            if sym in g_members:
                group = g_name
                break
        
        if group and group in used_groups:
            prev_dir, prev_sym = used_groups[group]
            if prev_dir == direction:
                # Same direction in same correlation group — skip duplicate
                logger.debug(f"Correlation filter: {sym} {direction} skipped (same as {prev_sym} in {group})")
                continue
        
        if group:
            used_groups[group] = (direction, sym)
        filtered.append(c)
    
    return filtered


# ═══════════════════════════════════════════════════════════════
# MAIN SELECTION — picks the single best trade
# ═══════════════════════════════════════════════════════════════
# Track recent stop-outs to enforce cooldown
_recent_stops: Dict[str, datetime] = {}
COOLDOWN_MINUTES = 15


def select_best_trade(
    candidates: List[dict],
    vix_level: float = 20.0,
    open_positions: Optional[List[dict]] = None,
) -> Optional[dict]:
    """Select the single best trade from all scored candidates.
    
    Uses expected return (not just score) as the primary metric.
    Applies tier bonuses, correlation filter, liquidity filter,
    dynamic thresholds, and cooldown.
    
    Returns the best candidate dict, or None if nothing qualifies.
    """
    if not candidates:
        return None
    
    now = datetime.now(ET)
    thresholds = get_dynamic_threshold(vix_level)
    min_prob = thresholds["min_prob"]
    min_ev = thresholds["min_ev"]
    
    # Already holding something?
    held_symbols = set()
    if open_positions:
        held_symbols = {p.get("symbol", "") for p in open_positions}
    
    scored = []
    
    for c in candidates:
        sym = c.get("symbol", "")
        
        # Skip if already holding this symbol
        if sym in held_symbols:
            continue
        
        # Cooldown check — don't re-enter after stop-out
        if sym in _recent_stops:
            elapsed = (now - _recent_stops[sym]).total_seconds() / 60
            if elapsed < COOLDOWN_MINUTES:
                logger.debug(f"Cooldown: {sym} stopped out {elapsed:.0f} min ago, need {COOLDOWN_MINUTES}")
                continue
        
        # Liquidity filter
        liq_pass, liq_reason = check_liquidity(c)
        if not liq_pass:
            logger.debug(f"Liquidity blocked {sym}: {liq_reason}")
            continue
        
        # Tier
        if sym in TIER_1:
            tier = 1
        elif sym in TIER_2:
            tier = 2
        elif sym in TIER_3:
            tier = 3
        else:
            tier = 4
        
        # Get probability and expected return
        prob = c.get("probability", 0.5)
        score = c.get("score", 0)
        
        # Expected return (use candidate's EV if available, else estimate)
        ev = c.get("expected_value", 0)
        if ev == 0:
            # Estimate: probability × typical reward - (1-prob) × typical risk
            ev = calculate_expected_return(
                probability=prob,
                reward_pct=0.30,   # typical 30% gain on 0DTE
                risk_pct=0.50,     # typical 50% loss on 0DTE
                spread_pct=0.02,   # ~2% spread cost
                theta_decay_pct=0.05,  # ~5% theta per hour for 0DTE
            )
        
        # Apply tier bonus
        tier_bonus = TIER_BONUS.get(tier, 0)
        
        # Final ranking score = EV × probability × tier_adjustment
        # We rank by expected return, not raw score
        final_score = (ev * 100) + tier_bonus + (score * 0.1)
        
        # Check thresholds
        if prob < min_prob:
            logger.debug(f"Threshold: {sym} prob {prob:.2f} < {min_prob}")
            continue
        if ev < min_ev:
            logger.debug(f"Threshold: {sym} EV {ev:.4f} < {min_ev}")
            continue
        
        c["_final_score"] = final_score
        c["_ev"] = ev
        c["_tier"] = tier
        c["_prob"] = prob
        scored.append(c)
    
    if not scored:
        return None
    
    # Apply correlation filter
    scored = filter_correlated(scored)
    
    if not scored:
        return None
    
    # Sort by final score (highest expected return first)
    scored.sort(key=lambda x: x.get("_final_score", 0), reverse=True)
    
    best = scored[0]
    logger.info(
        f"Best trade: {best.get('symbol')} {best.get('type')} "
        f"score={best.get('_final_score',0):.1f} "
        f"EV={best.get('_ev',0):.4f} "
        f"prob={best.get('_prob',0):.2f} "
        f"tier={best.get('_tier',4)} "
        f"[{thresholds['label']}]"
    )
    
    return best


def record_stop_out(symbol: str):
    """Record a stop-out for cooldown tracking."""
    _recent_stops[symbol] = datetime.now(ET)
    logger.info(f"Cooldown started for {symbol} — {COOLDOWN_MINUTES} min")


def get_tier_info() -> dict:
    """Return tier information for display."""
    return {
        "tier_1": sorted(TIER_1),
        "tier_2": sorted(TIER_2),
        "tier_3": sorted(TIER_3),
        "tier_4": sorted(TIER_4),
        "correlation_groups": {k: sorted(v) for k, v in CORRELATION_GROUPS.items()},
    }
