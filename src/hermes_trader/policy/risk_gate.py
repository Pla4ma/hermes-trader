"""Deterministic risk autonomy policy engine.

This is CODE, not an LLM prompt. Every order must pass through this
engine before broker execution. No validation here → no order.

Input: TradeCandidate JSON + account/position/market snapshots
Output: APPROVED / REJECTED / PAUSED / NO_TRADE
"""

from datetime import datetime
from typing import Optional

from ..config import config
from ..constants import (
    ALLOWED_STRATEGIES, LIVE_FORBIDDEN_STRATEGIES, OPTION_STRATEGIES,
    MIN_SCORE_PAPER, MIN_SCORE_LIVE,
)
from ..models.trade_candidate import TradeCandidate, CandidateScore
from ..models.trade_decision import PolicyResult
from ..models.position_snapshot import AccountSnapshot, MarketSnapshot, RiskSnapshot


class PolicyEngine:
    """Deterministic gate. All checks are code, not LLM reasoning."""

    def __init__(self):
        self._reasons: list[str] = []

    def evaluate(
        self,
        candidate: TradeCandidate,
        account: AccountSnapshot,
        market: Optional[MarketSnapshot] = None,
        risk_snapshot: Optional[RiskSnapshot] = None,
        score: Optional[CandidateScore] = None,
    ) -> PolicyResult:
        """Run all deterministic checks. First failure returns REJECTED with reasons."""
        self._reasons = []

        # --- Gate 1: Kill Switch ---
        if config.is_kill_switch_active:
            return self._reject("KILL_SWITCH_ACTIVE: Kill switch file exists. All new orders blocked.")

        # --- Gate 2: Mode Compatibility ---
        if not self._check_mode(candidate):
            return self._reject()

        # --- Gate 3: Live Unlock ---
        if not self._check_live_unlock(candidate):
            return self._reject()

        # --- Gate 4: Asset Class ---
        if not self._check_asset_class(candidate):
            return self._reject()

        # --- Gate 5: Symbol & Underlying ---
        if not self._check_symbols(candidate):
            return self._reject()

        # --- Gate 6: Strategy ---
        if not self._check_strategy(candidate):
            return self._reject()

        # --- Gate 7: No-Trade ---
        if candidate.action == "no_trade" or candidate.strategy == "no_trade":
            return self._no_trade("Candidate explicitly requests no_trade.")

        # --- Gate 8: Account Limits ---
        if not self._check_account_limits(account, candidate):
            return self._reject()

        # --- Gate 9: Position Limits ---
        if not self._check_position_limits(account, candidate):
            return self._reject()

        # --- Gate 10: Risk Limits ---
        if not self._check_risk_limits(candidate, risk_snapshot):
            return self._reject()

        # --- Gate 11: Option-Specific Checks ---
        if candidate.strategy in OPTION_STRATEGIES:
            if not self._check_option_rules(candidate, market):
                return self._reject()

        # --- Gate 12: Order Type Rules ---
        if not self._check_order_rules(candidate):
            return self._reject()

        # --- Gate 13: Exit Plan ---
        if candidate.action == "open":
            if not self._check_exit_plan(candidate):
                return self._reject()

        # --- Gate 14: Evidence ---
        if not self._check_evidence(candidate):
            return self._reject()

        # --- Gate 15: Confidence ---
        if not self._check_confidence(candidate):
            return self._reject()

        # --- Gate 16: Scoring Threshold ---
        if not self._check_score(score, candidate):
            return self._reject()

        # --- Gate 17: Market Open (if required) ---
        if not self._check_market_open(market):
            return self._reject()

        # --- ALL CHECKS PASSED ---
        allowed_action = self._determine_action(candidate)
        return PolicyResult(
            status="APPROVED",
            reasons=self._reasons,
            allowed_action=allowed_action,
            risk_summary={
                "max_loss_usd": candidate.risk.max_loss_usd,
                "notional_usd": candidate.risk.position_notional_usd,
                "daily_loss_budget": risk_snapshot.daily_loss_budget_remaining if risk_snapshot else None,
            },
        )

    # ------------------------------------------------------------------

    def _check_mode(self, c: TradeCandidate) -> bool:
        current_mode = config.trader_mode
        if current_mode == "PAUSED":
            if c.action not in ("close", "cancel"):
                self._reasons.append(f"MODE_PAUSED: Only close/cancel allowed. Action '{c.action}' blocked.")
                return False
            self._reasons.append("MODE_PAUSED: Close/cancel action allowed.")
            return True

        if current_mode == "RESEARCH_ONLY":
            if c.action not in ("no_trade",):
                self._reasons.append(f"MODE_RESEARCH_ONLY: No orders allowed. Action '{c.action}' blocked.")
                return False
            return True

        if current_mode == "PAPER_AUTONOMOUS":
            if c.mode == "TINY_LIVE_AUTONOMOUS":
                self._reasons.append("MODE_MISMATCH: Candidate requests LIVE but system is PAPER_AUTONOMOUS.")
                return False
            self._reasons.append("MODE_OK: PAPER_AUTONOMOUS")
            return True

        if current_mode == "TINY_LIVE_AUTONOMOUS":
            self._reasons.append("MODE_OK: TINY_LIVE_AUTONOMOUS")
            return True

        self._reasons.append(f"MODE_UNKNOWN: '{current_mode}'")
        return False

    def _check_live_unlock(self, c: TradeCandidate) -> bool:
        if c.mode == "TINY_LIVE_AUTONOMOUS":
            if not config.is_live_unlocked:
                self._reasons.append("LIVE_UNLOCK_FAILED: Live trading not globally unlocked.")
                return False
            self._reasons.append("LIVE_UNLOCKED: All conditions met.")
        return True

    def _check_asset_class(self, c: TradeCandidate) -> bool:
        if c.asset_class == "equity":
            if not config.allow_equities:
                self._reasons.append("ASSET_BLOCKED: Equities disabled.")
                return False
        elif c.asset_class == "option":
            if not config.allow_options:
                self._reasons.append("ASSET_BLOCKED: Options disabled.")
                return False
        else:
            self._reasons.append(f"ASSET_UNKNOWN: '{c.asset_class}' not supported.")
            return False
        return True

    def _check_symbols(self, c: TradeCandidate) -> bool:
        if c.underlying not in config.allowed_underlyings:
            self._reasons.append(f"UNDERLYING_REJECTED: '{c.underlying}' not in allowlist {config.allowed_underlyings}.")
            return False
        return True

    def _check_strategy(self, c: TradeCandidate) -> bool:
        if c.strategy not in ALLOWED_STRATEGIES:
            self._reasons.append(f"STRATEGY_UNKNOWN: '{c.strategy}' not in allowed strategies.")
            return False
        if c.mode == "TINY_LIVE_AUTONOMOUS" and c.strategy in LIVE_FORBIDDEN_STRATEGIES:
            self._reasons.append(f"STRATEGY_LIVE_FORBIDDEN: '{c.strategy}' is paper-only in Phase 1.")
            return False
        if c.strategy in ("long_call", "long_put"):
            if not (config.allow_long_calls if c.strategy == "long_call" else config.allow_long_puts if c.strategy == "long_put" else True):
                self._reasons.append(f"STRATEGY_DISABLED: '{c.strategy}' not enabled in config.")
                return False
        if c.strategy in ("debit_spread",):
            if not (config.allow_debit_spreads_live):
                self._reasons.append(f"STRATEGY_DISABLED: '{c.strategy}' not enabled in config.")
                return False
        return True

    def _check_account_limits(self, a: AccountSnapshot, c: TradeCandidate) -> bool:
        # Equity inside mandate?
        # FIX: max_account_equity_usd was a static cap. Now we use it as a SOFT cap
        # that only blocks when significantly exceeded. The account can grow.
        if a.equity > config.max_account_equity_usd * 1.10:  # 10% buffer
            self._reasons.append(f"EQUITY_EXCEEDS_MANDATE: ${a.equity:.2f} > ${config.max_account_equity_usd:.2f}")
            return False
        # Sufficient buying power?
        needed = c.risk.max_loss_usd if c.risk.max_loss_usd > 0 else c.order.notional_usd
        if a.buying_power < needed:
            self._reasons.append(f"BUYING_POWER_INSUFFICIENT: Need ${needed:.2f}, have ${a.buying_power:.2f}")
            return False
        # Cash reserve?
        if a.cash < config.min_cash_reserve_usd:
            self._reasons.append(f"CASH_BELOW_RESERVE: ${a.cash:.2f} < ${config.min_cash_reserve_usd:.2f}")
            return False
        # Multi-leg margin check: reject if required maintenance margin > buying power
        if c.order_class == "mleg" and c.required_maintenance_margin is not None:
            if c.required_maintenance_margin > a.buying_power:
                self._reasons.append(
                    f"MLEG_MARGIN_INSUFFICIENT: Required maintenance margin ${c.required_maintenance_margin:.2f} "
                    f"> buying power ${a.buying_power:.2f}"
                )
                return False
        return True

    def _check_position_limits(self, a: AccountSnapshot, c: TradeCandidate) -> bool:
        if c.action == "open" and len(a.positions) >= config.max_open_positions:
            self._reasons.append(f"MAX_POSITIONS: {len(a.positions)} open, limit is {config.max_open_positions}")
            return False
        return True

    def _check_risk_limits(self, c: TradeCandidate, rs: Optional[RiskSnapshot]) -> bool:
        # Max single trade loss
        # Equity loss cap ($3 default)
        if c.asset_class == "equity" and c.risk.max_loss_usd > config.absolute_single_trade_loss_cap_usd:
            self._reasons.append(f"MAX_LOSS_EXCEEDED: ${c.risk.max_loss_usd:.2f} > absolute cap ${config.absolute_single_trade_loss_cap_usd:.2f}")
            return False
        # Options loss cap ($50 default for $207 account)
        if c.asset_class == "option" and c.risk.max_loss_usd > config.absolute_option_loss_cap_usd:
            self._reasons.append(f"OPTION_MAX_LOSS_EXCEEDED: ${c.risk.max_loss_usd:.2f} > option cap ${config.absolute_option_loss_cap_usd:.2f}")
            return False
        if c.risk.expected_loss_usd > config.max_single_trade_loss_usd:
            self._reasons.append(f"EXPECTED_LOSS_EXCEEDED: ${c.risk.expected_loss_usd:.2f} > ${config.max_single_trade_loss_usd:.2f}")
            return False
        # Position notional
        if c.risk.position_notional_usd > config.max_position_notional_usd:
            self._reasons.append(f"POSITION_NOTIONAL_TOO_HIGH: ${c.risk.position_notional_usd:.2f} > ${config.max_position_notional_usd:.2f}")
            return False
        # Equity order notional
        if c.asset_class == "equity" and c.order.notional_usd > config.max_equity_order_notional_usd:
            self._reasons.append(f"EQUITY_NOTIONAL_TOO_HIGH: ${c.order.notional_usd:.2f} > ${config.max_equity_order_notional_usd:.2f}")
            return False

        if rs:
            # Daily loss cap
            if rs.daily_loss_budget_remaining <= 0:
                self._reasons.append(f"DAILY_LOSS_CAP_HIT: No remaining daily loss budget.")
                return False
            # Weekly loss cap
            if rs.weekly_loss_budget_remaining <= 0:
                self._reasons.append(f"WEEKLY_LOSS_CAP_HIT: No remaining weekly loss budget.")
                return False
            # Monthly loss cap
            if rs.monthly_loss_budget_remaining <= 0:
                self._reasons.append(f"MONTHLY_LOSS_CAP_HIT: No remaining monthly loss budget.")
                return False
            # Consecutive losses
            if rs.consecutive_losses >= config.max_consecutive_losses:
                self._reasons.append(f"CONSECUTIVE_LOSS_CAP_HIT: {rs.consecutive_losses} >= {config.max_consecutive_losses}")
                return False
            # Trades today
            if c.action == "open" and rs.trades_today >= config.max_new_trades_per_day:
                self._reasons.append(f"MAX_TRADES_TODAY: {rs.trades_today} >= {config.max_new_trades_per_day}")
                return False
            # Trades this week
            if rs.trades_this_week >= config.max_new_trades_per_week:
                self._reasons.append(f"MAX_TRADES_WEEK: {rs.trades_this_week} >= {config.max_new_trades_per_week}")
                return False

        return True

    def _check_option_rules(self, c: TradeCandidate, market: Optional[MarketSnapshot]) -> bool:
        od = c.option_details

        # DTE range
        if od.days_to_expiration < config.min_days_to_expiration:
            self._reasons.append(f"DTE_TOO_LOW: {od.days_to_expiration} < min {config.min_days_to_expiration}")
            return False
        if od.days_to_expiration > config.max_days_to_expiration:
            self._reasons.append(f"DTE_TOO_HIGH: {od.days_to_expiration} > max {config.max_days_to_expiration}")
            return False

        # Expiration danger window — only blocks positive DTE within the window.
        # 0DTE is allowed (DTE=0 passes since 0 is not > 0).
        if od.days_to_expiration > 0 and od.days_to_expiration <= config.expiration_danger_window_days:
            self._reasons.append(f"EXPIRATION_DANGER: {od.days_to_expiration}DTE within {config.expiration_danger_window_days}-day danger window")
            return False

        # Premium limit
        premium = od.midpoint if od.midpoint > 0 else od.ask
        if premium > config.max_option_premium_usd:
            self._reasons.append(f"PREMIUM_TOO_HIGH: ${premium:.2f} > ${config.max_option_premium_usd:.2f}")
            return False

        # Bid/Ask exists
        if od.bid <= 0:
            self._reasons.append("NO_BID: Bid is missing or zero.")
            return False
        if od.ask <= 0:
            self._reasons.append("NO_ASK: Ask is missing or zero.")
            return False

        # Spread check
        if od.midpoint <= 0:
            self._reasons.append("NO_MIDPOINT: Cannot compute spread with zero/negative midpoint.")
            return False
        spread_pct = abs(((od.ask - od.bid) / od.midpoint) * 100)
        if spread_pct > config.max_bid_ask_spread_pct:
            self._reasons.append(f"SPREAD_TOO_WIDE: {spread_pct:.1f}% > {config.max_bid_ask_spread_pct}%")
            return False

        # Open interest / volume
        if od.open_interest < config.min_open_interest:
            self._reasons.append(f"OI_TOO_LOW: {od.open_interest} < {config.min_open_interest}")
            return False
        if od.volume < config.min_volume:
            self._reasons.append(f"VOLUME_TOO_LOW: {od.volume} < {config.min_volume}")
            return False

        # Naked/short check
        if od.option_type == "call":
            if not config.allow_long_calls and c.action == "open":
                self._reasons.append("LONG_CALLS_DISABLED")
                return False
        if od.option_type == "put":
            if not config.allow_long_puts and c.action == "open":
                self._reasons.append("LONG_PUTS_DISABLED")
                return False

        # Max contracts = 1
        if c.order.quantity > config.max_contracts:
            self._reasons.append(f"MAX_CONTRACTS_EXCEEDED: {c.order.quantity} > {config.max_contracts}")
            return False

        return True

    def _check_order_rules(self, c: TradeCandidate) -> bool:
        # Options → limit orders only
        if c.asset_class == "option" and c.order.order_type == "market":
            self._reasons.append("OPTION_MARKET_ORDER_FORBIDDEN: Options must use limit orders.")
            return False
        return True

    def _check_exit_plan(self, c: TradeCandidate) -> bool:
        if not c.exit_plan.profit_take_rule and not c.exit_plan.stop_loss_rule and not c.exit_plan.time_exit_rule:
            self._reasons.append("NO_EXIT_PLAN: Opening trade requires at least one exit rule.")
            return False
        self._reasons.append("EXIT_PLAN: Present.")
        return True

    def _check_evidence(self, c: TradeCandidate) -> bool:
        if not c.evidence.vibe_summary and not c.evidence.tradingagents_summary:
            self._reasons.append("NO_EVIDENCE: Missing both Vibe-Trading and TradingAgents evidence.")
            return False
        if not c.evidence.market_data_timestamp:
            self._reasons.append("NO_MARKET_DATA_TIMESTAMP: Evidence missing data freshness.")
            return False
        self._reasons.append("EVIDENCE_OK")
        return True

    def _check_confidence(self, c: TradeCandidate) -> bool:
        # Confidence threshold — lowered to 55 to allow more trades through
        if c.confidence.score_0_to_100 < config.min_confidence_score:
            self._reasons.append(f"CONFIDENCE_TOO_LOW: {c.confidence.score_0_to_100} < {config.min_confidence_score}")
            return False
        return True

    def _check_score(self, score: Optional[CandidateScore], c: TradeCandidate) -> bool:
        if score is None:
            return True  # Score optional — skip if not provided
        if c.mode == "TINY_LIVE_AUTONOMOUS":
            if score.total < MIN_SCORE_LIVE:
                self._reasons.append(f"SCORE_TOO_LOW_FOR_LIVE: {score.total} < {MIN_SCORE_LIVE}")
                return False
        elif score.total < MIN_SCORE_PAPER:
            self._reasons.append(f"SCORE_TOO_LOW: {score.total} < {MIN_SCORE_PAPER}")
            return False
        return True

    def _check_market_open(self, market: Optional[MarketSnapshot]) -> bool:
        if config.require_market_open and market is not None and not market.market_open:
            self._reasons.append("MARKET_CLOSED: require_market_open is true and market is closed.")
            return False
        return True

    def _reject(self, first_reason: str = "") -> PolicyResult:
        if first_reason:
            self._reasons.insert(0, first_reason)
        return PolicyResult(
            status="REJECTED",
            reasons=self._reasons,
            allowed_action="none",
        )

    def _no_trade(self, reason: str) -> PolicyResult:
        self._reasons.append(reason)
        return PolicyResult(
            status="NO_TRADE",
            reasons=self._reasons,
            allowed_action="none",
        )

    def _determine_action(self, c: TradeCandidate) -> str:
        if c.action == "close":
            return "close_order"
        if c.action == "cancel":
            return "cancel_order"
        if config.trader_mode == "TINY_LIVE_AUTONOMOUS" and c.mode == "TINY_LIVE_AUTONOMOUS":
            return "live_order"
        return "paper_order"




# Singleton
policy_engine = PolicyEngine()