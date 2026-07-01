"""Daily autonomous workflow — the main loop.

This is the Hermes agent's daily orchestration script.
It runs the full research → evaluate → decide → execute pipeline.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config
from .constants import OPTION_STRATEGIES
from .models.trade_candidate import (
    TradeCandidate, SourceInfo, EvidencePack, ExitPlan,
    OrderDetails, RiskDetails, OptionDetails, ConfidenceInfo,
)
from .models.trade_decision import DecisionLog, PolicyResult
from .models.order_request import OrderRequest
from .policy.risk_gate import PolicyEngine, policy_engine
from .policy.scoring import ScoringEngine
from .integrations.alpaca_broker import PaperBrokerAdapter

logger = logging.getLogger("hermes_trader.workflow")


class DailyWorkflow:
    """Execute one daily cycle: research → score → policy → journal → report."""

    def __init__(self):
        self.broker = PaperBrokerAdapter()
        self.policy = policy_engine
        self.scoring = ScoringEngine()
        self._decision_log_path = config.project_root / "data" / "journals" / "decisions.jsonl"

    def run(self, research_result: Optional[dict] = None) -> dict:
        """Run the full daily cycle.

        Args:
            research_result: Optional pre-computed research. If None, a
                             simple no-trade candidate is used for testing.
        """
        mode = config.trader_mode
        logger.info(f"Daily workflow starting. Mode: {mode}")

        if config.is_kill_switch_active:
            logger.warning("Kill switch ACTIVE — workflow returning NO_TRADE.")
            return {"status": "KILL_SWITCH_ACTIVE", "decision": "no_trade"}

        # 1. Generate candidate (from research or blank)
        candidate = self._build_candidate(research_result)
        if candidate.strategy == "no_trade":
            logger.info("No-trade candidate generated. Logging and exiting.")
            self._log_decision(candidate, PolicyResult(status="NO_TRADE", reasons=["No research provided"], allowed_action="none"))
            return self._build_report(candidate, None, None)

        # 2. Score
        score = self.scoring.score(candidate)
        logger.info(f"Candidate scored: {score.total}/100 (tier: {score.tier})")

        # 3. Fetch account/market/risk state
        account = self.broker.get_account()
        market = self.broker.get_market_snapshot(candidate.underlying)
        risk_snapshot = self.broker.get_risk_snapshot()

        # 4. Policy evaluation
        result = self.policy.evaluate(candidate, account, market, risk_snapshot, score)
        logger.info(f"Policy result: {result.status} | Action: {result.allowed_action}")

        # 5. Execute if approved
        order_result = None
        if result.is_approved or result.can_execute:
            order_result = self._execute(candidate, result)

        # 6. Journal
        self._log_decision(candidate, result, score)

        # 7. Report
        return self._build_report(candidate, result, score, order_result, account)

    def _build_candidate(self, research: Optional[dict] = None) -> TradeCandidate:
        """Build a TradeCandidate from research data or default no-trade."""
        now = datetime.utcnow().isoformat()
        cid = f"cand_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        if research is None:
            # Default no-trade candidate
            return TradeCandidate(
                candidate_id=cid,
                created_at=now,
                mode=config.trader_mode,
                underlying="SPY",
                symbol="SPY",
                asset_class="equity",
                strategy="no_trade",
                direction="neutral",
                action="no_trade",
                confidence=ConfidenceInfo(score_0_to_100=0, label="low", reason="No research data"),
                source=SourceInfo(),
                evidence=EvidencePack(),
                exit_plan=ExitPlan(),
                order=OrderDetails(),
                risk=RiskDetails(),
                option_details=OptionDetails(),
            )

        # Extract structured fields from research dict
        return TradeCandidate(
            candidate_id=cid,
            created_at=now,
            mode=config.trader_mode,
            underlying=research.get("underlying", "SPY"),
            symbol=research.get("symbol", "SPY"),
            asset_class=research.get("asset_class", "equity"),
            strategy=research.get("strategy", "no_trade"),
            direction=research.get("direction", "neutral"),
            action=research.get("action", "no_trade"),
            order=OrderDetails(
                side=research.get("order_side", "buy"),
                order_type=research.get("order_type", "limit"),
                quantity=research.get("order_qty", 0.0),
                notional_usd=research.get("order_notional", 0.0),
                limit_price=research.get("limit_price"),
            ),
            risk=RiskDetails(
                max_loss_usd=research.get("max_loss", 0.0),
                expected_loss_usd=research.get("expected_loss", 0.0),
                max_profit_usd=research.get("max_profit", 0.0),
                risk_reward_ratio=research.get("risk_reward", 0.0),
                position_notional_usd=research.get("notional", 0.0),
            ),
            evidence=EvidencePack(
                market_data_timestamp=research.get("data_timestamp", now),
                vibe_summary=research.get("vibe_summary", ""),
                tradingagents_summary=research.get("agents_summary", ""),
                bull_case=research.get("bull_case", ""),
                bear_case=research.get("bear_case", ""),
                risk_case=research.get("risk_case", ""),
                backtest_summary=research.get("backtest_summary"),
                transaction_cost_assumption=research.get("tx_cost"),
                slippage_assumption=research.get("slippage"),
                known_limitations=research.get("limitations", []),
            ),
            exit_plan=ExitPlan(
                profit_take_rule=research.get("exit_profit_take", ""),
                stop_loss_rule=research.get("exit_stop_loss", ""),
                time_exit_rule=research.get("exit_time", ""),
                expiration_exit_rule=research.get("exit_expiration", ""),
                emergency_exit_rule=research.get("exit_emergency", ""),
            ),
            confidence=ConfidenceInfo(
                score_0_to_100=research.get("confidence_score", 50),
                label=research.get("confidence_label", "medium"),
                reason=research.get("confidence_reason", ""),
            ),
        )

    def _execute(self, candidate: TradeCandidate, policy: "PolicyResult") -> dict:
        """Execute the approved action through the broker."""
        if policy.allowed_action == "close_order":
            return self.broker.close_position(candidate.symbol)
        elif policy.allowed_action == "cancel_order":
            return self.broker.cancel_order(f"paper_{candidate.candidate_id}")
        elif policy.allowed_action in ("paper_order", "live_order"):
            order = OrderRequest(
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                side=candidate.order.side,
                order_type=candidate.order.order_type,
                qty=candidate.order.quantity,
                notional=candidate.order.notional_usd if candidate.order.notional_usd > 0 else None,
                limit_price=candidate.order.limit_price,
            )
            return self.broker.submit_order(order)
        return {"status": "no_action_required"}

    def _log_decision(self, candidate: TradeCandidate, result: "PolicyResult", score: Optional["CandidateScore"] = None) -> None:
        """Append decision to the decision journal."""
        entry = DecisionLog(
            timestamp=datetime.utcnow().isoformat(),
            candidate_id=candidate.candidate_id,
            policy_status=result.status,
            policy_reasons=result.reasons,
            mode=candidate.mode,
            strategy=candidate.strategy,
            underlying=candidate.underlying,
            score_total=score.total if score else 0,
            score_tier=score.tier if score else "unscored",
            kill_switch_active=config.is_kill_switch_active,
        )
        self._decision_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._decision_log_path, "a") as f:
            f.write(entry.model_dump_json() + "\n")

    def _build_report(self, candidate: TradeCandidate, result: Optional["PolicyResult"], score: Optional["CandidateScore"] = None, order_result: Optional[dict] = None, account: Optional[object] = None) -> dict:
        """Build a structured report for Telegram delivery."""
        # If no result provided but candidate is no_trade, infer it
        policy_status = "UNKNOWN"
        if result is not None:
            policy_status = result.status
        elif candidate.strategy == "no_trade":
            policy_status = "NO_TRADE"
        
        report = {
            "cycle_timestamp": datetime.utcnow().isoformat(),
            "mode": config.trader_mode,
            "candidate_id": candidate.candidate_id,
            "underlying": candidate.underlying,
            "strategy": candidate.strategy,
            "action": candidate.action,
            "direction": candidate.direction,
            "policy_status": policy_status,
            "policy_reasons": result.reasons if result else [],
            "score_total": score.total if score else None,
            "score_tier": score.tier if score else None,
            "order_result": order_result,
            "kill_switch_active": config.is_kill_switch_active,
            "live_unlocked": config.is_live_unlocked,
            "confidence": candidate.confidence.score_0_to_100 if candidate else None,
            "config": config.redacted_repr(),
        }

        if account:
            report["account_equity"] = round(account.equity, 2) if hasattr(account, 'equity') else None
            report["account_cash"] = round(account.cash, 2) if hasattr(account, 'cash') else None

        return report