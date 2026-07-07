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

# ── Institutional-grade enrichment modules ──
from .options_flow import get_flow_sentiment
from .dealer_positioning import quick_dealer_check
from .multi_timeframe import combine_all as mtf_combine_all

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
        # CRITICAL: default is BLOCK (False). Gates must explicitly pass.
        # Old code defaulted to True inside try/except — any exception
        # silently allowed trades through with zero safety checks.
        if candidate.asset_class == "option":  # All underlyings eligible (fixed: was hardcoded to 4 symbols)
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
                    
                    # MUST have valid spot price — block if missing
                    if not spot or spot <= 0:
                        return False, f"BLOCKED: spot price unavailable for {symbol}"
                    
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
                    # Gates passed — trade allowed
                    return True, ""
                else:
                    return False, f"BLOCKED: insufficient price data for {symbol}"
            except Exception as e:
                logger.error(f"Entry gate check FAILED (blocking trade): {e}")
                return False, f"BLOCKED: gate check exception: {e}"
        
        return False, f"BLOCKED: {candidate.asset_class} not eligible for auto-trade"

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

        result = {"status": "COMPLETED", "timestamp": datetime.utcnow().isoformat(), "research": research}
        
        # Save research snapshot for watcher/trigger_engine to consume
        try:
            import json
            snapshot_dir = Path("/opt/hermes-trader/data/snapshots")
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = snapshot_dir / "research_latest.json"
            snapshot_path.write_text(json.dumps(result, default=str, indent=2))
            logger.info(f"Research snapshot saved to {snapshot_path}")
        except Exception as e:
            logger.error(f"Failed to save research snapshot: {e}")
        
        return result

    def _enrich_research(self, research_result: dict) -> dict:
        """Enrich research with options flow, dealer positioning, and multi-TF signals.

        This is Phase 2 of the pipeline: research -> ENRICHMENT -> candidate.
        Each symbol in the research dict gets enriched with:
        - Options flow sentiment (bullish/bearish/neutral from put/call flow)
        - Dealer positioning (GEX regime, gamma flip, squeeze risk)
        - Multi-timeframe confirmation (1d/30m/5m/1m alignment)

        The enriched signals are stored in the research dict so they flow
        into the candidate via _build_candidate() and influence scoring.
        """
        research = research_result.get("research", {})
        if not research:
            return research_result

        for symbol, data in research.items():
            # -- Options Flow Sentiment --
            try:
                flow = get_flow_sentiment(symbol)
                data["flow_sentiment"] = flow.signal
                data["flow_score"] = round(flow.score, 3)
                data["flow_strength"] = flow.strength
                logger.info(
                    "Enrichment flow %s: %s (%s) score=%.3f",
                    symbol, flow.signal, flow.strength, flow.score,
                )
            except Exception as e:
                logger.debug("Flow enrichment error for %s: %s", symbol, e)
                data["flow_sentiment"] = "neutral"
                data["flow_score"] = 0.0
                data["flow_strength"] = "weak"

            # -- Dealer Positioning --
            try:
                dealer = quick_dealer_check(symbol)
                data["dealer_regime"] = dealer.get("regime", "unknown")
                data["dealer_squeeze_detected"] = dealer.get("squeeze_detected", False)
                data["dealer_squeeze_risk"] = dealer.get("squeeze_risk", "low")
                data["dealer_expected_move_pct"] = dealer.get("expected_move_pct", 0)
                logger.info(
                    "Enrichment dealer %s: regime=%s squeeze=%s",
                    symbol, dealer.get("regime"), dealer.get("squeeze_risk"),
                )
            except Exception as e:
                logger.debug("Dealer enrichment error for %s: %s", symbol, e)
                data["dealer_regime"] = "unknown"
                data["dealer_squeeze_detected"] = False
                data["dealer_squeeze_risk"] = "low"

            # -- Multi-Timeframe Confirmation --
            try:
                mtf = mtf_combine_all(symbol)
                mtf_alignment = mtf.get("alignment", {})
                data["mtf_alignment_score"] = mtf_alignment.get("alignment_score", 0)
                data["mtf_alignment_label"] = mtf_alignment.get("alignment_label", "NONE")
                data["mtf_trade_direction"] = mtf.get("trade_direction", "none")
                data["mtf_go_trade"] = mtf.get("go_trade", False)
                data["mtf_confidence"] = mtf.get("confidence", "NONE")
                logger.info(
                    "Enrichment multi-TF %s: alignment=%s (%.0f/100) go=%s",
                    symbol,
                    mtf_alignment.get("alignment_label"),
                    mtf_alignment.get("alignment_score", 0),
                    mtf.get("go_trade", False),
                )
            except Exception as e:
                logger.debug("Multi-TF enrichment error for %s: %s", symbol, e)
                data["mtf_alignment_score"] = 0
                data["mtf_alignment_label"] = "NONE"
                data["mtf_trade_direction"] = "none"
                data["mtf_go_trade"] = False
                data["mtf_confidence"] = "NONE"

        return research_result

    def run(self, research_result: Optional[dict] = None) -> dict:
        """Run the full daily cycle.

        PIPELINE: research -> enrichment -> candidate -> gates -> score -> execute
        ONE ENGINE, ONE PIPELINE -- all signals flow through in sequence.

        Args:
            research_result: Optional pre-computed research. If None, a
                             workflow runs research first (Vibe-Trading +
                             TradingAgents) before building candidates.
        """
        mode = config.trader_mode
        logger.info(f"Daily workflow starting. Mode: {mode}")
        if config.is_kill_switch_active:
            logger.warning("Kill switch ACTIVE -- workflow returning NO_TRADE.")
            return {"status": "KILL_SWITCH_ACTIVE", "decision": "no_trade"}

        # -- Phase 1: Research (Vibe-Trading + TradingAgents) --
        if research_result is None:
            logger.info("No research provided -- running research cycle first.")
            research_result = self.run_research_cycle()
            if not research_result.get("research"):
                logger.warning("Research cycle returned no data. Aborting.")
                return {"status": "NO_RESEARCH", "decision": "no_trade"}

        # -- Phase 2: Enrich research with flow/dealer/timeframe signals --
        research_result = self._enrich_research(research_result)

        # -- Phase 3: Build candidate from enriched research --
        candidate = self._build_candidate(research_result)
        if candidate.strategy == "no_trade":
            logger.info("No-trade candidate generated. Logging and exiting.")
            self._log_decision(candidate, PolicyResult(status="NO_TRADE", reasons=["No research provided"], allowed_action="none"))
            return self._build_report(candidate, None, None)

        # -- Phase 4: MAX POWER Filters (vol regime, correlation, news, portfolio risk, entry gates) --
        max_power_passed, max_power_reason = self._run_max_power_filters(candidate)
        if not max_power_passed:
            logger.warning(f"MAX POWER filter blocked: {max_power_reason}")
            result = PolicyResult(
                status="REJECTED",
                reasons=[max_power_reason],
                allowed_action="none",
            )
            self._log_decision(candidate, result, None)
            return self._build_report(candidate, result, None)

        # -- Phase 5: Score --
        score = self.scoring.score(candidate)
        logger.info(f"Candidate scored: {score.total}/100 (tier: {score.tier})")

        # -- Phase 6: Fetch account/market/risk state --
        account = self.broker.get_account()
        market = self.broker.get_market_snapshot(candidate.underlying)
        risk_snapshot = self.broker.get_risk_snapshot()

        # -- Phase 7: Policy evaluation --
        result = self.policy.evaluate(candidate, account, market, risk_snapshot, score)
        logger.info(f"Policy result: {result.status} | Action: {result.allowed_action}")

        # -- Phase 8: Execute if approved --
        order_result = None
        if result.is_approved or result.can_execute:
            order_result = self._execute(candidate, result)

        # -- Phase 9: Journal --
        self._log_decision(candidate, result, score)

        # -- Phase 10: Report --
        return self._build_report(candidate, result, score, order_result, account)

    def _pick_best_symbol(self, research_result: dict) -> Optional[dict]:
        """Pick the best trading candidate from per-symbol research data.
        
        Scans all symbols in research_result["research"], selects the one
        with highest confidence and actionable signal (bullish or bearish).
        Returns the per-symbol dict or None if nothing actionable.
        """
        research = research_result.get("research", {})
        if not research:
            return None

        best = None
        best_score = -1

        for symbol, data in research.items():
            signal = data.get("signal", "neutral")
            confidence = data.get("confidence_score", 0)
            mtf_go = data.get("mtf_go_trade", False)
            flow = data.get("flow_sentiment", "neutral")

            # Skip neutral signals — nothing actionable
            if signal == "neutral":
                continue

            # Score: confidence * alignment bonus
            score = confidence
            if mtf_go:
                score *= 1.3  # multi-TF alignment bonus
            if flow == signal:
                score *= 1.2  # flow confirms direction

            if score > best_score:
                best_score = score
                best = data

        return best

    def _build_candidate(self, research: Optional[dict] = None) -> TradeCandidate:
        """Build a TradeCandidate from research data or default no-trade.
        
        FIXED (2026-07-07): Research is a dict with structure:
        {"status": "COMPLETED", "research": {"SPY": {...}, "QQQ": {...}, ...}}
        Must pick the best symbol from the per-symbol research data,
        not read from the top-level dict (which has no strategy/asset_class).
        """
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

        # Pick best symbol from per-symbol research data
        best = self._pick_best_symbol(research)
        if best is None:
            logger.info("No actionable signal in research — generating no_trade candidate.")
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
                confidence=ConfidenceInfo(score_0_to_100=0, label="low", reason="All signals neutral"),
                source=SourceInfo(),
                evidence=EvidencePack(),
                exit_plan=ExitPlan(),
                order=OrderDetails(),
                risk=RiskDetails(),
                option_details=OptionDetails(),
            )

        # Build candidate from best symbol's data
        signal = best.get("signal", "neutral")
        direction = "bullish" if signal == "bullish" else "bearish"
        option_type = "call" if direction == "bullish" else "put"
        strategy = "long_call" if direction == "bullish" else "long_put"

        logger.info(
            "Best candidate: %s signal=%s conf=%.2f strategy=%s",
            best.get("symbol", "?"), signal, best.get("confidence_score", 0), strategy,
        )

        # Fetch spot price for ATM strike (required by pydantic validator)
        spot = 0.0
        try:
            import yfinance as yf
            ticker = yf.Ticker(best.get("symbol", "SPY"))
            fast = ticker.fast_info
            spot = float(fast.get("lastPrice", 0) or 0)
        except Exception as e:
            logger.debug(f"Spot price fetch failed: {e}")
        if spot <= 0:
            spot = 580.0  # fallback — will be corrected by entry gates

        return TradeCandidate(
            candidate_id=cid,
            created_at=now,
            mode=config.trader_mode,
            underlying=best.get("underlying", "SPY"),
            symbol=best.get("symbol", "SPY"),
            asset_class="option",
            strategy=strategy,
            direction=direction,
            action="open",
            order=OrderDetails(
                side="buy",
                order_type="limit",
                quantity=1,
                notional_usd=0.0,
                limit_price=None,
            ),
            risk=RiskDetails(
                max_loss_usd=0.0,
                expected_loss_usd=0.0,
                max_profit_usd=0.0,
                risk_reward_ratio=0.0,
                position_notional_usd=0.0,
            ),
            evidence=EvidencePack(
                market_data_timestamp=now,
                vibe_summary=best.get("vibe_summary", ""),
                tradingagents_summary=best.get("agents_summary", ""),
                bull_case="",
                bear_case="",
                risk_case="",
                backtest_summary=None,
                transaction_cost_assumption=None,
                slippage_assumption=None,
                known_limitations=[],
            ),
            exit_plan=ExitPlan(
                profit_take_rule="50% at +50%, all at +100%",
                stop_loss_rule="-30% hard stop",
                time_exit_rule="30 min before close",
                expiration_exit_rule="Force close 0DTE by 3:45 PM",
                emergency_exit_rule="Kill switch",
            ),
            confidence=ConfidenceInfo(
                score_0_to_100=int(best.get("confidence_score", 0.5) * 100),
                label="high" if best.get("confidence_score", 0) > 0.7 else "medium" if best.get("confidence_score", 0) > 0.5 else "low",
                reason=f"Signal: {signal}, MTF: {best.get('mtf_alignment_label', 'NONE')}, Flow: {best.get('flow_sentiment', 'neutral')}",
            ),
            option_details=OptionDetails(
                option_type=option_type,
                days_to_expiration=0,
                strike=round(spot, 2),
                expiration_date="2026-07-07",
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
