"""Configuration loader.

Loads settings from environment variables, falling back to constants.py defaults.
Secrets must be loaded from .env via python-dotenv before config is accessed.
"""

import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from . import constants

# Load .env once at module import
_PROJECT_ROOT = Path("/opt/hermes-trader")
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)
else:
    # Try $HOME/.hermes/.env if no project .env exists
    _HOME_ENV = Path.home() / ".hermes" / ".env"
    if _HOME_ENV.exists():
        load_dotenv(_HOME_ENV)


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_set(name: str, default: set[str]) -> set[str]:
    val = os.getenv(name)
    if val is None:
        return default
    return {s.strip() for s in val.split(",") if s.strip()}


class Config:
    """Runtime configuration with env override over constants defaults."""

    # === Mode ===
    @property
    def trader_mode(self) -> str:
        if hasattr(self, '_trader_mode'):
            return self._trader_mode
        mode = _env_str("TRADER_MODE", constants.DEFAULT_TRADER_MODE)
        if mode not in constants.VALID_MODES:
            return constants.DEFAULT_TRADER_MODE
        return mode

    @trader_mode.setter
    def trader_mode(self, value: str) -> None:
        self._trader_mode = value

    @trader_mode.deleter
    def trader_mode(self) -> None:
        del self._trader_mode

    @property
    def enable_live_trading(self) -> bool:
        return _env_bool("ENABLE_LIVE_TRADING", False)

    @property
    def live_autonomy_mode(self) -> str:
        return _env_str("LIVE_AUTONOMY_MODE", "DISABLED")

    @property
    def live_confirmation_phrase(self) -> str:
        return _env_str("LIVE_CONFIRMATION_PHRASE", "DISABLED")

    # === Account Mandate ===
    max_experiment_capital_usd = property(lambda s: _env_float("MAX_EXPERIMENT_CAPITAL_USD", constants.MAX_EXPERIMENT_CAPITAL_USD))
    max_account_equity_usd = property(lambda s: _env_float("MAX_ACCOUNT_EQUITY_USD", constants.MAX_ACCOUNT_EQUITY_USD))
    max_single_trade_loss_usd = property(lambda s: _env_float("MAX_SINGLE_TRADE_LOSS_USD", constants.MAX_SINGLE_TRADE_LOSS_USD))
    absolute_single_trade_loss_cap_usd = property(lambda s: _env_float("ABSOLUTE_SINGLE_TRADE_LOSS_CAP_USD", constants.ABSOLUTE_SINGLE_TRADE_LOSS_CAP_USD))
    max_daily_loss_usd = property(lambda s: _env_float("MAX_DAILY_LOSS_USD", constants.MAX_DAILY_LOSS_USD))
    max_weekly_loss_usd = property(lambda s: _env_float("MAX_WEEKLY_LOSS_USD", constants.MAX_WEEKLY_LOSS_USD))
    max_monthly_loss_usd = property(lambda s: _env_float("MAX_MONTHLY_LOSS_USD", constants.MAX_MONTHLY_LOSS_USD))
    min_cash_reserve_usd = property(lambda s: _env_float("MIN_CASH_RESERVE_USD", constants.MIN_CASH_RESERVE_USD))

    # === Trade Limits ===
    max_open_positions = property(lambda s: int(_env_float("MAX_OPEN_POSITIONS", constants.MAX_OPEN_POSITIONS)))
    max_new_trades_per_day = property(lambda s: int(_env_float("MAX_NEW_TRADES_PER_DAY", constants.MAX_NEW_TRADES_PER_DAY)))
    max_new_trades_per_week = property(lambda s: int(_env_float("MAX_NEW_TRADES_PER_WEEK", constants.MAX_NEW_TRADES_PER_WEEK)))
    max_consecutive_losses = property(lambda s: int(_env_float("MAX_CONSECUTIVE_LOSSES", constants.MAX_CONSECUTIVE_LOSSES)))
    max_equity_order_notional_usd = property(lambda s: _env_float("MAX_EQUITY_ORDER_NOTIONAL_USD", constants.MAX_EQUITY_ORDER_NOTIONAL_USD))

    # === Execution Advanced ===
    initial_trade_notional = property(lambda s: float(_env_float("INITIAL_TRADE_NOTIONAL", 1000.0)))  # Aggressive: $1k
    allow_pyramid_scaling = property(lambda s: _env_bool("ALLOW_PYRAMID_SCALING", True))           # Aggressive: allow pyramiding
    
    # === Position Management ===
    trailing_stop_initial_pct = property(lambda s: float(_env_float("TRAILING_STOP_INITIAL_PCT", 0.02)))
    trailing_stop_trail_pct = property(lambda s: float(_env_float("TRAILING_STOP_TRAIL_PCT", 0.01)))
    trailing_stop_activation_pct = property(lambda s: float(_env_float("TRAILING_STOP_ACTIVATION_PCT", 0.03)))
    
    profit_taking_first_target_pct = property(lambda s: float(_env_float("PROFIT_TAKING_FIRST_TARGET_PCT", 0.05)))
    profit_taking_first_take_pct = property(lambda s: float(_env_float("PROFIT_TAKING_FIRST_TAKE_PCT", 0.50)))
    
    # === Backtest Validation ===
    require_backtest_validation = property(lambda s: _env_bool("REQUIRE_BACKTEST_VALIDATION", True))
    backtest_min_sharpe = property(lambda s: float(_env_float("BACKTEST_MIN_SHARPE", 1.0)))
    backtest_min_win_rate = property(lambda s: float(_env_float("BACKTEST_MIN_WIN_RATE", 50.0)))

    # === Momentum Agent ===
    spy_breakout_lookback_days = property(lambda s: int(_env_float("SPY_BREAKOUT_LOOKBACK_DAYS", 20)))
    spy_breakout_trade_size = property(lambda s: float(_env_float("SPY_BREAKOUT_TRADE_SIZE", 2000.0)))  # Aggressive: $2k

    # === Assets ===
    allowed_underlyings = property(lambda s: _env_set("ALLOWED_UNDERLYINGS", constants.ALLOWED_UNDERLYINGS))
    allow_equities = property(lambda s: _env_bool("ALLOW_EQUITIES", constants.ALLOW_EQUITIES))
    allow_options = property(lambda s: _env_bool("ALLOW_OPTIONS", constants.ALLOW_OPTIONS))

    # === Options ===
    min_days_to_expiration = property(lambda s: int(_env_float("MIN_DAYS_TO_EXPIRATION", constants.MIN_DAYS_TO_EXPIRATION)))
    max_days_to_expiration = property(lambda s: int(_env_float("MAX_DAYS_TO_EXPIRATION", constants.MAX_DAYS_TO_EXPIRATION)))
    expiration_danger_window_days = property(lambda s: int(_env_float("EXPIRATION_DANGER_WINDOW_DAYS", constants.EXPIRATION_DANGER_WINDOW_DAYS)))
    max_option_premium_usd = property(lambda s: _env_float("MAX_OPTION_PREMIUM_USD", constants.MAX_OPTION_PREMIUM_USD))
    max_option_spread_width_usd = property(lambda s: _env_float("MAX_OPTION_SPREAD_WIDTH_USD", constants.MAX_OPTION_SPREAD_WIDTH_USD))
    max_contracts = property(lambda s: int(_env_float("MAX_CONTRACTS", constants.MAX_CONTRACTS)))
    min_open_interest = property(lambda s: int(_env_float("MIN_OPEN_INTEREST", constants.MIN_OPEN_INTEREST)))
    min_volume = property(lambda s: int(_env_float("MIN_VOLUME", constants.MIN_VOLUME)))
    max_bid_ask_spread_pct = property(lambda s: _env_float("MAX_BID_ASK_SPREAD_PCT", constants.MAX_BID_ASK_SPREAD_PCT))
    allow_0dte = property(lambda s: _env_bool("ALLOW_0DTE", constants.ALLOW_0DTE))

    # === 0DTE-Specific Risk Rules (PDT eliminated June 2026) ===
    risk_per_trade_pct = property(lambda s: _env_float("RISK_PER_TRADE_PCT", constants.RISK_PER_TRADE_PCT))
    max_0dte_trades_per_day = property(lambda s: int(_env_float("MAX_0DTE_TRADES_PER_DAY", constants.MAX_0DTE_TRADES_PER_DAY)))
    max_0dte_position_size_pct = property(lambda s: _env_float("MAX_0DTE_POSITION_SIZE_PCT", constants.MAX_0DTE_POSITION_SIZE_PCT))
    dte_entry_window_start = property(lambda s: _env_str("DTE_ENTRY_WINDOW_START", constants.DTE_ENTRY_WINDOW_START))
    dte_entry_window_end = property(lambda s: _env_str("DTE_ENTRY_WINDOW_END", constants.DTE_ENTRY_WINDOW_END))
    dte_force_exit_time = property(lambda s: _env_str("DTE_FORCE_EXIT_TIME", constants.DTE_FORCE_EXIT_TIME))
    dte_profit_target_pct = property(lambda s: _env_float("DTE_PROFIT_TARGET_PCT", constants.DTE_PROFIT_TARGET_PCT))
    dte_stop_loss_pct = property(lambda s: _env_float("DTE_STOP_LOSS_PCT", constants.DTE_STOP_LOSS_PCT))
    dte_time_stop_minutes = property(lambda s: int(_env_float("DTE_TIME_STOP_MINUTES", constants.DTE_TIME_STOP_MINUTES)))
    min_volume_0dte = property(lambda s: int(_env_float("MIN_VOLUME_0DTE", constants.MIN_VOLUME_0DTE)))
    min_open_interest_0dte = property(lambda s: int(_env_float("MIN_OPEN_INTEREST_0DTE", constants.MIN_OPEN_INTEREST_0DTE)))
    max_spread_pct_0dte = property(lambda s: _env_float("MAX_SPREAD_PCT_0DTE", constants.MAX_SPREAD_PCT_0DTE))

    # === Small Account Phase Thresholds ===
    small_account_phase_1_equity = property(lambda s: _env_float("SMALL_ACCOUNT_PHASE_1_EQUITY", constants.SMALL_ACCOUNT_PHASE_1_EQUITY))
    small_account_phase_2_equity = property(lambda s: _env_float("SMALL_ACCOUNT_PHASE_2_EQUITY", constants.SMALL_ACCOUNT_PHASE_2_EQUITY))
    small_account_phase_3_equity = property(lambda s: _env_float("SMALL_ACCOUNT_PHASE_3_EQUITY", constants.SMALL_ACCOUNT_PHASE_3_EQUITY))
    phase_1_risk_per_trade_pct = property(lambda s: _env_float("PHASE_1_RISK_PER_TRADE_PCT", constants.PHASE_1_RISK_PER_TRADE_PCT))
    phase_2_risk_per_trade_pct = property(lambda s: _env_float("PHASE_2_RISK_PER_TRADE_PCT", constants.PHASE_2_RISK_PER_TRADE_PCT))
    phase_3_risk_per_trade_pct = property(lambda s: _env_float("PHASE_3_RISK_PER_TRADE_PCT", constants.PHASE_3_RISK_PER_TRADE_PCT))
    phase_1_max_positions = property(lambda s: int(_env_float("PHASE_1_MAX_POSITIONS", constants.PHASE_1_MAX_POSITIONS)))
    phase_2_max_positions = property(lambda s: int(_env_float("PHASE_2_MAX_POSITIONS", constants.PHASE_2_MAX_POSITIONS)))
    phase_3_max_positions = property(lambda s: int(_env_float("PHASE_3_MAX_POSITIONS", constants.PHASE_3_MAX_POSITIONS)))

    # === Alpaca ===
    alpaca_api_key = property(lambda s: _env_str("ALPACA_API_KEY", ""))
    alpaca_secret_key = property(lambda s: _env_str("ALPACA_SECRET_KEY", ""))
    alpaca_paper = property(lambda s: _env_bool("ALPACA_PAPER", True))

    @property
    def alpaca_base_url(self) -> str:
        url = _env_str("ALPACA_BASE_URL", "")
        if url:
            return url
        return "https://paper-api.alpaca.markets" if self.alpaca_paper else "https://api.alpaca.markets"

    # === Paths ===
    project_root = property(lambda s: Path(_env_str("PROJECT_ROOT", "/opt/hermes-trader")))
    kill_switch_path = property(lambda s: Path(_env_str("KILL_SWITCH_PATH", "/opt/hermes-trader/KILL_SWITCH")))
    log_dir = property(lambda s: Path(_env_str("LOG_DIR", "/opt/hermes-trader/logs")))
    data_dir = property(lambda s: Path(_env_str("DATA_DIR", "/opt/hermes-trader/data")))

    # === Research Tools ===
    vibe_trading_enabled = property(lambda s: _env_bool("VIBE_TRADING_ENABLED", True))
    vibe_trading_path = property(lambda s: Path(_env_str("VIBE_TRADING_PATH", "/opt/Vibe-Trading")))
    tradingagents_enabled = property(lambda s: _env_bool("TRADINGAGENTS_ENABLED", True))
    tradingagents_path = property(lambda s: Path(_env_str("TRADINGAGENTS_PATH", "/opt/TradingAgents")))

    # === TradingAgents Configuration ====
    tradingagents_deep_think_llm = property(lambda s: _env_str("TRADINGAGENTS_DEEP_THINK_LLM", ""))
    tradingagents_quick_think_llm = property(lambda s: _env_str("TRADINGAGENTS_QUICK_THINK_LLM", ""))
    tradingagents_max_debate_rounds = property(lambda s: int(_env_float("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "3")))  # Aggressive: 3 rounds
    tradingagents_max_risk_rounds = property(lambda s: int(_env_float("TRADINGAGENTS_MAX_RISK_ROUNDS", "3")))     # Aggressive: 3 rounds

    # === Execution ===
    max_position_notional_usd = property(lambda s: _env_float("MAX_POSITION_NOTIONAL_USD", 5000.0))  # Aggressive: $5k limit
    require_market_open = property(lambda s: _env_bool("REQUIRE_MARKET_OPEN", constants.REQUIRE_MARKET_OPEN))
    allow_long_calls = property(lambda s: _env_bool("ALLOW_LONG_CALLS", constants.ALLOW_LONG_CALLS))
    allow_long_puts = property(lambda s: _env_bool("ALLOW_LONG_PUTS", constants.ALLOW_LONG_PUTS))
    allow_debit_spreads_live = property(lambda s: _env_bool("ALLOW_DEBIT_SPREADS_LIVE", constants.ALLOW_DEBIT_SPREADS_LIVE))

    @property
    def is_kill_switch_active(self) -> bool:
        return self._kill_switch_active if hasattr(self, '_kill_switch_active') else self.kill_switch_path.exists()

    @is_kill_switch_active.setter
    def is_kill_switch_active(self, value: bool) -> None:
        self._kill_switch_active = value

    @is_kill_switch_active.deleter
    def is_kill_switch_active(self) -> None:
        del self._kill_switch_active

    @property
    def is_live_unlocked(self) -> bool:
        """Check if all live unlock conditions are met."""
        if hasattr(self, '_live_unlocked'):
            return self._live_unlocked
        return all([
            not self.alpaca_paper,
            self.enable_live_trading,
            self.live_autonomy_mode == "TINY_LIVE_AUTONOMOUS",
            self.live_confirmation_phrase == "I_ACCEPT_THAT_THIS_20_DOLLAR_EXPERIMENT_CAN_LOSE_MONEY",
        ])

    @is_live_unlocked.setter
    def is_live_unlocked(self, value: bool) -> None:
        self._live_unlocked = value

    def redacted_repr(self) -> dict[str, Any]:
        """Return config dict with secrets redacted for logging/reporting."""
        return {
            "trader_mode": self.trader_mode,
            "enable_live_trading": self.enable_live_trading,
            "live_autonomy_mode": self.live_autonomy_mode,
            "live_unlock_confirmed": bool(self.live_confirmation_phrase != "DISABLED"),
            "alpaca_paper": self.alpaca_paper,
            "alpaca_api_key_set": bool(self.alpaca_api_key),
            "alpaca_api_key": "***REDACTED***" if self.alpaca_api_key else "",
            "kill_switch_active": self.is_kill_switch_active,
            "vibe_trading_enabled": self.vibe_trading_enabled,
            "tradingagents_enabled": self.tradingagents_enabled,
            "max_experiment_capital_usd": self.max_experiment_capital_usd,
            "max_single_trade_loss_usd": self.max_single_trade_loss_usd,
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "max_open_positions": self.max_open_positions,
            # PDT eliminated June 2026 — 0DTE now allowed
            "allow_0dte": self.allow_0dte,
            "min_days_to_expiration": self.min_days_to_expiration,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "current_account_phase": self.current_account_phase,
        }

    @property
    def current_account_phase(self) -> int:
        """Determine account growth phase based on current equity.
        
        Phase 1: $50 → $100 (preservation, 2% risk, 1-2 positions)
        Phase 2: $100 → $500 (growth, 3% risk, 2-3 positions)
        Phase 3: $500 → $2000 (scaling, 4% risk, 3-4 positions)
        """
        equity = self.max_account_equity_usd
        if equity < self.small_account_phase_2_equity:
            return 1
        elif equity < self.small_account_phase_3_equity:
            return 2
        else:
            return 3

    @property
    def current_phase_risk_pct(self) -> float:
        """Return the risk-per-trade percentage for the current account phase."""
        phase = self.current_account_phase
        if phase == 1:
            return self.phase_1_risk_per_trade_pct
        elif phase == 2:
            return self.phase_2_risk_per_trade_pct
        else:
            return self.phase_3_risk_per_trade_pct

    @property
    def current_phase_max_positions(self) -> int:
        """Return the max positions allowed for the current account phase."""
        phase = self.current_account_phase
        if phase == 1:
            return self.phase_1_max_positions
        elif phase == 2:
            return self.phase_2_max_positions
        else:
            return self.phase_3_max_positions


# Singleton
config = Config()