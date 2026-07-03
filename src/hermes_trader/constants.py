"""Mandate constants for the Hermes Autonomous Trading System.

These are the hard-coded defaults. Values are overridden by
environment variables of the same name (loaded via config.py).
The agent CANNOT change these — they're imported as module-level constants.

REGULATORY NOTE (effective June 4, 2026):
  The Pattern Day Trader (PDT) rule was eliminated by the SEC.
  Accounts of ANY size can now day-trade without restriction.
  The old $25,000 minimum equity requirement no longer applies.
  Margin is now dynamic/intraday-based (FINRA Rule 4210 amended).
"""

# === Mode ===
DEFAULT_TRADER_MODE = "PAPER_AUTONOMOUS"
VALID_MODES = {"RESEARCH_ONLY", "PAPER_AUTONOMOUS", "TINY_LIVE_AUTONOMOUS", "PAUSED"}

# === Account Mandate ===
MAX_EXPERIMENT_CAPITAL_USD = 50.00
MAX_ACCOUNT_EQUITY_USD = 50.00
MAX_SINGLE_TRADE_LOSS_USD = 2.00
ABSOLUTE_SINGLE_TRADE_LOSS_CAP_USD = 3.00
MAX_DAILY_LOSS_USD = 4.00
MAX_WEEKLY_LOSS_USD = 10.00
MAX_MONTHLY_LOSS_USD = 20.00
MIN_CASH_RESERVE_USD = 5.00

# === Trade Limits ===
MAX_OPEN_POSITIONS = 3
MAX_NEW_TRADES_PER_DAY = 3
MAX_NEW_TRADES_PER_WEEK = 10
MAX_CONSECUTIVE_LOSSES = 3
MAX_EQUITY_ORDER_NOTIONAL_USD = 15.00
MAX_POSITION_NOTIONAL_USD = 25.00

# === Assets ===
ALLOWED_UNDERLYINGS = {"SPY", "QQQ", "VOO", "DIA", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "TQQQ", "SOXL", "SPXL", "UPRO", "TECL", "FNGU", "LABU", "TNA", "AMD", "NFLX"}
ALLOWED_EQUITY_SYMBOLS = {"SPY", "QQQ", "VOO", "DIA", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "TQQQ", "SOXL", "SPXL", "UPRO", "TECL", "FNGU", "LABU", "TNA", "AMD", "NFLX"}
ALLOW_EQUITIES = True
ALLOW_OPTIONS = True
ALLOW_CRYPTO = False
ALLOW_FOREX = False
ALLOW_FUTURES = False
ALLOW_MARGIN = False

# === Options ===
ALLOW_LONG_CALLS = True
ALLOW_LONG_PUTS = True
ALLOW_DEBIT_SPREADS_PAPER = True
ALLOW_DEBIT_SPREADS_LIVE = True
ALLOW_CREDIT_SPREADS = False
ALLOW_NAKED_OPTIONS = False
ALLOW_SHORT_OPTIONS = False

# === 0DTE Options (PDT eliminated — now unrestricted) ===
# PDT rule eliminated June 4, 2026. 0DTE day-trading allowed for ANY account size.
ALLOW_0DTE = True
MIN_DAYS_TO_EXPIRATION = 0       # 0DTE now allowed (was 3 under PDT)
MAX_DAYS_TO_EXPIRATION = 30
EXPIRATION_DANGER_WINDOW_DAYS = 2
MAX_OPTION_PREMIUM_USD = 5.00
MAX_OPTION_SPREAD_WIDTH_USD = 2.00
MAX_CONTRACTS = 2
MIN_OPEN_INTEREST = 200
MIN_VOLUME = 100
MAX_BID_ASK_SPREAD_PCT = 10

# === 0DTE-Specific Risk Rules (Small Account Optimized) ===
# Research-backed: 2% risk per trade → <10% risk of ruin for $50 account
RISK_PER_TRADE_PCT = 0.02            # 2% of account equity per trade
MAX_0DTE_TRADES_PER_DAY = 3          # Max 0DTE trades in a single day
MAX_0DTE_POSITION_SIZE_PCT = 0.10    # Max 10% of account per 0DTE position
DTE_ENTRY_WINDOW_START = "09:45"     # After initial volatility settles
DTE_ENTRY_WINDOW_END = "11:00"       # Before lunch chop
DTE_FORCE_EXIT_TIME = "15:30"        # Close all 0DTE by 3:30 PM ET (30 min before close)
DTE_PROFIT_TARGET_PCT = 0.50         # Exit at 50% profit on premium (tastylive #1 rule)
DTE_STOP_LOSS_PCT = 0.50             # Exit at 50% loss on premium
DTE_TIME_STOP_MINUTES = 30           # Exit if no movement in first 30 minutes
MIN_VOLUME_0DTE = 100                # Minimum volume for 0DTE liquidity
MIN_OPEN_INTEREST_0DTE = 500         # Minimum OI for 0DTE liquidity
MAX_SPREAD_PCT_0DTE = 0.05           # Max 5% bid-ask spread for 0DTE

# === Small Account Phase Thresholds ===
# Phased approach: preservation → growth → scaling
SMALL_ACCOUNT_PHASE_1_EQUITY = 100.00   # Phase 1: $50 → $100 (preservation)
SMALL_ACCOUNT_PHASE_2_EQUITY = 500.00   # Phase 2: $100 → $500 (growth)
SMALL_ACCOUNT_PHASE_3_EQUITY = 2000.00  # Phase 3: $500 → $2000 (scaling)
PHASE_1_RISK_PER_TRADE_PCT = 0.02       # Phase 1: 2% per trade ($1 on $50)
PHASE_2_RISK_PER_TRADE_PCT = 0.03       # Phase 2: 3% per trade
PHASE_3_RISK_PER_TRADE_PCT = 0.04       # Phase 3: 4% per trade
PHASE_1_MAX_POSITIONS = 2               # Phase 1: max 1-2 positions
PHASE_2_MAX_POSITIONS = 3               # Phase 2: max 2-3 positions
PHASE_3_MAX_POSITIONS = 4               # Phase 3: max 3-4 positions

# === Execution ===
REQUIRE_MARKET_OPEN = True
USE_LIMIT_ORDERS_FOR_OPTIONS = True
OPTION_ORDER_TIMEOUT_SECONDS = 300
EQUITY_ORDER_TIMEOUT_SECONDS = 600
ALLOW_ORDER_CHASING = False
MAX_ORDER_RESUBMISSIONS = 1

# === Allowed Strategies ===
ALLOWED_STRATEGIES = {
    "no_trade",
    "fractional_etf",
    "long_call",
    "long_put",
    "debit_spread_paper",
    "debit_spread",
}
LIVE_FORBIDDEN_STRATEGIES = {"debit_spread_paper"}  # Paper only in phase 1
OPTION_STRATEGIES = {"long_call", "long_put", "debit_spread_paper", "debit_spread"}

# === Scoring Thresholds ===
MIN_SCORE_PAPER = 65
MIN_SCORE_LIVE = 80