"""Engine Configuration — research-backed optimal parameters.

Based on:
- tastylive research (10,000+ backtested trades)
- Option Alpha backtesting (thousands of parameter combinations)
- Kelly Criterion analysis for $50 account
- Risk of ruin analysis
- Complete Greeks hierarchy (15 Greeks)

Last updated: July 3, 2026
"""

# ═══════════════════════════════════════════════════════════════
# STRATEGY SELECTION (research: directional spreads outperform ICs)
# ═══════════════════════════════════════════════════════════════
DEFAULT_STRATEGY = "bull_put_spread"  # NOT iron condors (ICs underperform)
STRATEGIES = {
    "bull_put_spread": {"direction": "put", "win_rate": 0.83, "description": "Bullish bias, benefits from positive drift"},
    "bear_call_spread": {"direction": "call", "win_rate": 0.78, "description": "Bearish bias, lower win rate due to positive drift"},
    "iron_condor": {"direction": "both", "win_rate": 0.72, "description": "Neutral, loses in trending markets"},
}

# ═══════════════════════════════════════════════════════════════
# DELTA SELECTION (research: 16 delta = tastylive sweet spot)
# ═══════════════════════════════════════════════════════════════
DELTA = {
    "target": 0.16,           # Tastylive optimal (83% WR, profit factor 1.68)
    "min": 0.10,              # 1 standard deviation
    "max": 0.25,              # Aggressive limit
    "vix_adjustment": {       # Option Alpha VIX-adjusted delta
        "low_vol": 0.20,      # VIX < 15: higher delta (need more premium)
        "normal": 0.16,       # VIX 15-25: standard
        "high_vol": 0.10,     # VIX 25-30: lower delta (let IV overstatement work)
        "crisis": 0.10,       # VIX > 30: maximum caution
    },
}

# ═══════════════════════════════════════════════════════════════
# SPREAD WIDTH (research: $1-wide for <$5K, commission-inefficient)
# ═══════════════════════════════════════════════════════════════
WIDTH = {
    "default": 1,             # Required for $50 account
    "max": 2,                 # Can go to $2-wide at $200+
    "rule": "1/3 of premium collected",  # tastylive research
}

# ═══════════════════════════════════════════════════════════════
# DTE OPTIMIZATION (research: enter at 45, exit at 21 or 50%)
# ═══════════════════════════════════════════════════════════════
DTE = {
    "entry": 45,              # Tastylive optimal entry (captures steepest decay)
    "exit_management": 21,    # Close at 21 DTE regardless
    "exit_profit": 0.50,      # Close at 50% profit (#1 rule)
    "exit_stop": 2.0,         # Close at 2x credit received (stop loss)
    "min_dte": 7,             # Never enter with < 7 DTE
    "max_dte": 60,            # Never enter with > 60 DTE
}

# ═══════════════════════════════════════════════════════════════
# EXIT RULES (research: 50% profit = optimal)
# ═══════════════════════════════════════════════════════════════
EXIT = {
    "profit_target": 0.50,    # 50% of max profit (tastylive #1 rule)
    "stop_loss": 2.0,         # 2x credit received (essential for $50)
    "time_exit": 21,          # Close at 21 DTE regardless
    "gamma_exit": 7,          # Close before 7 DTE (gamma risk)
}

# ═══════════════════════════════════════════════════════════════
# POSITION SIZING (research: Kelly for $50 account)
# ═══════════════════════════════════════════════════════════════
SIZING = {
    "method": "kelly_fractional",
    "kelly_fraction": 0.25,   # Quarter Kelly (conservative for $50)
    "max_risk_per_trade": 0.025,  # 2.5% of account ($1.25 for $50)
    "max_positions": 2,       # Max 1-2 positions at a time
    "max_portfolio_risk": 0.50,  # Never risk > 50% of account
    "min_cash_reserve": 0.20,  # Keep 20% cash minimum
}

# ═══════════════════════════════════════════════════════════════
# VIX FILTERS (research: optimal range 15-30)
# ═══════════════════════════════════════════════════════════════
VIX_FILTERS = {
    "min": 10,                # Never sell when VIX < 10
    "max": 40,                # Never sell when VIX > 40
    "optimal_min": 15,        # Optimal range start
    "optimal_max": 30,        # Optimal range end
    "term_structure": {       # VIX term structure
        "contango_threshold": 1.05,  # VIX3M/VIX >= 1.05 = contango
        "strong_contango": 1.10,     # VIX3M/VIX >= 1.10 = strong contango
        "backwardation_threshold": 1.00,  # VIX3M/VIX < 1.00 = backwardation
    },
}

# ═══════════════════════════════════════════════════════════════
# DAY OF WEEK (research: Tuesday best, Friday never)
# ═══════════════════════════════════════════════════════════════
DAY_FILTERS = {
    "best": [1, 2, 3],       # Tuesday, Wednesday, Thursday
    "good": [0],             # Monday (gap risk)
    "avoid": [4],            # Friday (never enter new positions)
    "optimal": 2,            # Tuesday (best premium)
}

# ═══════════════════════════════════════════════════════════════
# TIME OF DAY (research: 9:45-10:30 AM optimal)
# ═══════════════════════════════════════════════════════════════
TIME_FILTERS = {
    "optimal_start": 9.75,   # 9:45 AM ET
    "optimal_end": 10.5,     # 10:30 AM ET
    "good_start": 11.0,      # 11:00 AM ET
    "good_end": 13.0,        # 1:00 PM ET
    "avoid_start": 15.0,     # 3:00 PM ET (gamma risk)
    "avoid_end": 16.0,       # 4:00 PM ET
}

# ═══════════════════════════════════════════════════════════════
# UNDERLYINGS (research: only trade liquid products)
# ═══════════════════════════════════════════════════════════════
UNDERLYINGS = ["SPY", "QQQ", "IWM"]  # Only these for $50 account
# Individual stocks: NEVER (too risky for small accounts)

# ═══════════════════════════════════════════════════════════════
# RISK MANAGEMENT (research: essential for $50 survival)
# ═══════════════════════════════════════════════════════════════
RISK = {
    "max_daily_loss": 0.05,   # 5% of account ($2.50)
    "max_drawdown": 0.20,     # 20% of account ($10)
    "max_consecutive_losses": 5,  # Pause after 5 consecutive losses
    "max_position_risk": 0.025,  # 2.5% per trade
    "black_swan_protection": True,  # Close all if VIX > 40
}

# ═══════════════════════════════════════════════════════════════
# ACCOUNT GROWTH TARGETS (research: realistic expectations)
# ═══════════════════════════════════════════════════════════════
GROWTH = {
    "target_1": 100,          # $100 (2x) — first milestone
    "target_2": 200,          # $200 (4x) — can trade $2-wide spreads
    "target_3": 500,          # $500 (10x) — can trade $2-wide with 2-3 contracts
    "target_4": 1000,         # $1,000 (20x) — begin scaling to optimal Kelly
    "annual_return_target": 0.80,  # 80% annual return (aggressive but realistic)
    "ruin_probability_target": 0.05,  # <5% annual ruin probability
}
