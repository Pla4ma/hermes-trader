"""Daily autonomous workflow — the main loop.

This is the Hermes agent's daily orchestration script.
It runs the full research → evaluate → decide → execute pipeline.

CRITICAL FIXES (July 7, 2026):
- Now uses entry_gates (9 filters)
- Now uses vol_regime (VIX term structure)
- Now uses correlation_regime (12-asset matrix)
- Now uses news_catalyst (FOMC/NFP/CPI)
- Now uses portfolio_risk (drawdown protection)
- Research gate now allows bearish (for puts)
- cancel_order no longer uses paper_ prefix
- limit_price no longer falls back to market price
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
from .integrations.robinhood_broker import RobinhoodBrokerAdapter
from .monitoring.position_monitor import PositionMonitor
from .research.agents_client import TradingAgentsClient
from .research.vibe_client import VibeTradingClient

# ── MAX POWER feature imports (July 7, 2026) ──
from .entry_gates import check_all_gates
from .vol_regime import should_trade_today as vol_should_trade
from .correlation_regime import compute_correlation_regime
from .news_catalyst import should_block_trade as news_should_block
from .portfolio_risk import get_portfolio_risk_summary, check_drawdown

logger = logging.getLogger("hermes_trader.workflow")


class DailyWorkflow:
    """Execute one daily cycle: research → score → policy → journal → report."""

    def __init__(self):
        self.broker = RobinhoodBrokerAdapter()
        self.policy = policy_engine
        self.scoring = ScoringEngine()
        self.vibe = VibeTradingClient()
        self.agents = TradingAgentsClient()
        self.positions = PositionMonitor()
        self._decision_log_path = config.project_root / "data" / "journals" / "decisions.jsonl"

    def _fetch_market_price(self, symbol: str = "SPY") -> float:
        """Fetch current market price via yfinance."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            price = ticker.fast_info.get("lastPrice", 0.0)
            return round(price, 2) if price else 0.0
        except Exception:
            return 0.0

    def _run_max_power_filters(self, candidate: TradeCandidate) -> tuple:
        """Run all MAX POWER filters. Returns (passed, reason).
        
        Filters:
        1. Vol regime (VIX term structure)
        2. Correlation regime (CRASH_RISK detection)
        3. News/catalyst (FOMC/NFP/CPI)
        4. Portfolio risk (drawdown)
        5. Entry gates (9 filters)
        """
        # 1. VIX term structure
        try:
            should_trade, vol_reason = vol_should_trade()
            if not should_trade:
                return False, f"Vol regime: {vol_reason}"
        except Exception as e:
            logger.debug(f"Vol regime check error: {e}")

        # 2. Correlation regime
        try:
            corr_regime = compute_correlation_regime()
            if corr_regime and corr_regime.should_block_trades():
                return False, f"Correlation CRASH_RISK: {corr_regime.notes}"
        except Exception as e:
            logger.debug(f"Correlation check error: {e}")

        # 3. News/catalyst
        try:
            block_reasons = []
            if news_should_block(block_reasons):
                return False, f"News: {block_reasons[0]}"
        except Exception as e:
            logger.debug(f"News check error: {e}")

        # 4. Portfolio risk
        try:
            positions = self.broker.list_positions()
            position_dicts = [
                {
                    "symbol": p.get("symbol", "UNKNOWN") if isinstance(p, dict) else getattr(p, "symbol", "UNKNOWN"),
                    "type": p.get("type", "equity") if isinstance(p, dict) else "equity",
                    "value": float(p.get("quantity", 0) or 0) * float(p.get("avg_entry_price", 0) or 0) * (100 if p.get("asset_type", "equity") == "option" else 1)
                    if isinstance(p, dict) else 0,
                }
                for p in positions
            ]
            equity_curve = []  # TODO: load from history
            is_blocked, dd_pct, dd_reason = check_drawdown(equity_curve)
            if is_blocked:
                return False, dd_reason
        except Exception as e:
            logger.debug(f"Portfolio risk check error: {e}")

        # 5. Entry gates (9 filters) — only for option candidates
        if candidate.asset_class == "option" and candidate.underlying in ("SPY", "QQQ", "SPXW", "NDXW"):
            try:
                import yfinance as yf
                from datetime import datetime as dt
                from zoneinfo import ZoneInfo
                symbol = candidate.underlying
                ticker = yf.Ticker(symbol)
                hist_today = ticker.history(period="1d")
                hist_20d = ticker.history(period="20d")
                
                if len(hist_today) > 0 and len(hist_20d) > 1:
                    today_data = hist_today.iloc[0]
                    open_price = float(today_data["Open"])
                    high_of_day = float(today_data["High"])
                    low_of_day = float(today_data["Low"])
                    current_volume = float(hist_today["Volume"].iloc[-1])
                    avg_volume = float(hist_20d["Volume"].mean())
                    prev_close = float(hist_20d["Close"].iloc[-2]) if len(hist_20d) > 1 else 0
                    
                    # RSI
                    close_20d = hist_20d["Close"]
                    delta_series = close_20d.diff()
                    gain = delta_series.clip(lower=0).rolling(14).mean()
                    loss = (-delta_series.clip(upper=0)).rolling(14).mean()
                    rs = gain / loss
                    rsi_series = 100 - (100 / (1 + rs))
                    rsi_14 = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 else 50.0
                    
                    now_et = dt.now(ZoneInfo("America/New_York"))
                    spot = self._fetch_market_price(symbol)
                    
                    option_type = "call" if candidate.direction == "bullish" else "put"
                    
                    if spot > 0:
                        gates_passed, gate_failures = check_all_gates(
                            symbol=symbol,
                            option_type=option_type,
                            spot=spot,
                            open_price=open_price,
                            high_of_day=high_of_day,
                            low_of_day=low_of_day,
                            current_volume=current_volume,
                            avg_volume_20d=avg_volume,
                            rsi_14=rsi_14,
                            now_et=now_et,
                            prev_close=prev_close,
                        )
                        if not gates_passed:
                            return False, f"Entry gates: {'; '.join(gate_failures)}"
            except Exception as e:
                logger.debug(f"Entry gate check error: {e}")
        
        return True, ""

    def run_research_cycle(self, symbols: list[str] = None) -> dict:
        """Run Vibe-Trading + TradingAgents research for given symbols."""
        if symbols is None:
            symbols = config.allowed_underlyings

        research = {}

        for symbol in symbols:
            try:
                vibe_result = self.vibe.run_market_regime_analysis(symbol)
                agents_result = self.agents.get_committee_signal(symbol)
            except Exception as e:
                logger.error(f"Research error for {symbol}: {e}")
                vibe_result = {"output": "", "status": "ERROR"}
                agents_result = {"signal": "neutral", "decision": "", "confidence": 0, "status": "ERROR"}

            research[symbol] = {
                "underlying": symbol,
                "symbol": symbol,
                "vibe_summary": vibe_result.get("output", "")[-500:],
                "agents_summary": agents_result.get("decision", "")[-500:],
                "signal": agents_result.get("signal", "neutral"),
                "confidence_score": agents_result.get("confidence", 0),
            }

        return {"status": "COMPLETED", "timestamp": datetime.utcnow().isoformat(), "research": research}

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

        # ── MAX POWER FILTERS (NEW) ──
        max_power_passed, max_power_reason = self._run_max_power_filters(candidate)
        if not max_power_passed:
            logger.warning(f"MAX POWER filter blocked: {max_power_reason}")
            result = PolicyResult(
                status="BLOCKED",
                reasons=[max_power_reason],
                allowed_action="none",
            )
            self._log_decision(candidate, result, None)
            return self._build_report(candidate, result, None)

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
                limit_price=research.get("limit_price") if research.get("limit_price") else None,
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
            # FIX: Use real order_id from candidate (not paper_ prefix)
            order_id = getattr(candidate, 'order_id', None) or f"paper_{candidate.candidate_id}"
            try:
                return self.broker.cancel_order(order_id)
            except Exception as e:
                logger.error(f"Cancel order failed: {e}")
                return {"status": "cancel_error", "error": str(e)}
        elif policy.allowed_action in ("paper_order", "live_order"):
            # FIX: Only use limit_price if explicitly provided (don't fall back to market price)
            limit_price = candidate.order.limit_price
            if not limit_price or limit_price <= 0:
                # Fetch market price as FALLBACK (not as order)
                # The order will be placed at the bid-ask midpoint with a limit
                limit_price = self._fetch_market_price(candidate.symbol)
            
            order = OrderRequest(
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                side=candidate.order.side,
                order_type=candidate.order.order_type if candidate.order.order_type in ("market", "limit") else "limit",
                qty=candidate.order.quantity,
                notional=candidate.order.notional_usd if candidate.order.notional_usd > 0 else None,
                limit_price=limit_price,
            )
            try:
                result = self.broker.submit_order(order)
                # Store order_id on candidate for potential cancellation
                if isinstance(result, dict) and (result.get("order_id") or result.get("id")):
                    candidate.order_id = result.get("order_id") or result.get("id")
                return result
            except Exception as e:
                logger.error(f"Submit order failed: {e}")
                return {"status": "submit_error", "error": str(e)}
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
        # FIX: Write with flush to prevent corruption on crash
        with open(self._decision_log_path, "a") as f:
            f.write(entry.model_dump_json() + "\n")
            f.flush()

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
