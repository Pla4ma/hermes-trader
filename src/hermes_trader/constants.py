"""Mandate constants for the Hermes Autonomous Trading System.

These are the hard-coded defaults. Values are overridden by
environment variables of the same name (loaded via config.py).
The agent CANNOT change these — they're imported as module-level constants.
"""

# === Mode ===
DEFAULT_TRADER_MODE = "PAPER_AUTONOMOUS"
VALID_MODES = {"RESEARCH_ONLY", "PAPER_AUTONOMOUS", "TINY_LIVE_AUTONOMOUS", "PAUSED"}

# === Account Mandate ===
MAX_EXPERIMENT_CAPITAL_USD = 20.00
MAX_ACCOUNT_EQUITY_USD = 25.00
MAX_SINGLE_TRADE_LOSS_USD = 1.50
ABSOLUTE_SINGLE_TRADE_LOSS_CAP_USD = 2.00
MAX_DAILY_LOSS_USD = 1.50
MAX_WEEKLY_LOSS_USD = 3.00
MAX_MONTHLY_LOSS_USD = 6.00
MIN_CASH_RESERVE_USD = 2.00

# === Trade Limits ===
MAX_OPEN_POSITIONS = 1
MAX_NEW_TRADES_PER_DAY = 1
MAX_NEW_TRADES_PER_WEEK = 3
MAX_CONSECUTIVE_LOSSES = 3
MAX_EQUITY_ORDER_NOTIONAL_USD = 6.00
MAX_POSITION_NOTIONAL_USD = 10.00

# === Assets ===
ALLOWED_UNDERLYINGS = {"SPY", "QQQ", "VOO"}
ALLOWED_EQUITY_SYMBOLS = {"SPY", "QQQ", "VOO"}  # Broker-specific symbols match underlyings
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
ALLOW_DEBIT_SPREADS_LIVE = False
ALLOW_CREDIT_SPREADS = False
ALLOW_NAKED_OPTIONS = False
ALLOW_SHORT_OPTIONS = False
ALLOW_0DTE = False
MIN_DAYS_TO_EXPIRATION = 3
MAX_DAYS_TO_EXPIRATION = 21
EXPIRATION_DANGER_WINDOW_DAYS = 2
MAX_OPTION_PREMIUM_USD = 2.00
MAX_OPTION_SPREAD_WIDTH_USD = 1.00
MAX_CONTRACTS = 1
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MAX_BID_ASK_SPREAD_PCT = 15

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
}
LIVE_FORBIDDEN_STRATEGIES = {"debit_spread_paper"}  # Paper only in phase 1
OPTION_STRATEGIES = {"long_call", "long_put", "debit_spread_paper"}

# === Scoring Thresholds ===
MIN_SCORE_PAPER = 70
MIN_SCORE_LIVE = 85