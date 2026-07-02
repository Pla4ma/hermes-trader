"""Enhanced Daily Workflow with aggressive strategies.

Integrates:
- Pyramid position sizing
- Trailing stops & partial profit-taking
- Backtest validation for every trade idea
- SPY momentum breakout scanner

This is where the "maximum money" mandate is operationalized.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from ..config import config
from ..models.trade_candidate import TradeCandidate, EvidencePack
from ..models.position_snapshot import AccountSnapshot, MarketSnapshot, RiskSnapshot, PositionSnapshot
from ..policy.risk_gate import PolicyEngine, PolicyResult
from ..policy.scoring import ScoringEngine
from ..monitoring.advanced_position_monitor import (
    PositionMonitor, MomentumScanner, PyramidSizer, TrailingStopConfig,
    ProfitTakingConfig, TimeDecayConfig
)
from ..research.backtest_validator import BacktestValidator
from ..integrations.alpaca_broker import PaperBrokerAdapter

logger = logging.getLogger("hermes_trader.workflow.enhanced")


class EnhancedDailyWorkflow:
    """Daily execution loop with aggressive strategies enabled."""
    
    def __init__(self):
        self.account = None
        self.market = None
        self.risk = None
        
        self.policy_engine = PolicyEngine()
        self.scoring_engine = ScoringEngine()
        self.broker = PaperBrokerAdapter()
        
        self._momentum_scanner = MomentumScanner(lookback_days=config.spy_breakout_lookback_days)
        self._backtest_validator = BacktestValidator()
        
        # Aggressive configs
        self._pyramid_sizer = PyramidSizer(
            base_notional=config.initial_trade_notional,
            pyramid_levels=3,
            max_total_notional=config.max_position_notional_usd
        )
    
    def pre_market(self):
        """Pre-market checks: momentum, account, market status."""
        self.account = self.broker.get_account()
        self.market = self._get_market_snapshot()
        spy_status = self._momentum_scanner.check_spy_breakout()
        
        # Print pre-market brief
        brief = {
            "market_open": self.market.market_open,
            "spy_price": spy_status["current_price"],
            "spy_breakout": spy_status["in_breakout"],
            "breakout_level": spy_status["breakout_level"],
            "spy_breakout_pct": spy_status["breakout_pct"],
            "cash_available": self.account.cash,
            "in_trailing_stop": False,
            "position_count": len(self.account.positions)
        }
        logger.info("Pre-Market Brief: %s", brief)
        return brief
    
    def run(self, research_result: Optional[dict] = None) -> dict:
        """Full daily cycle."""
        # Initialize session
        report = {
            "run_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "candidate": None,
            "policy_status": "NO_TRADE",
            "policy_reasons": [],
            "allowed_action": None,
            "actions_taken": [],
            "momentum_scan": None,
            "backtest_result": None,
            "exit_status": "completed"
        }
        
        # Pre-flight: market open?
        self.pre_market()
        if research_result is None:
            return report
        
        # Convert research to TradeCandidate
        candidate_dict = self._research_to_candidate(research_result)
        candidate = TradeCandidate(**candidate_dict)
        
        # Backtest validation (aggressive gate)
        if config.require_backtest_validation and candidate.asset_class == "equity":
            bt_result = self._backtest_validator.validate_trade(
                symbol=candidate.symbol,
                target_pct=0.03,
                stop_pct=0.01
            )
            report["backtest_result"] = bt_result
            if not bt_result["valid"]:
                report["policy_status"] = "REJECTED"
                report["policy_reasons"].append(f"BACKTEST_FAILED: {bt_result['reason']}")
                return self._format_report(report, candidate)
        
        # Momentum scan
        spy_status = self._momentum_scanner.check_spy_breakout()
        report["momentum_scan"] = spy_status
        if spy_status["in_breakout"] and \
           candidate.symbol == "SPY" and \
           candidate.direction == "bullish" and \
           config.allow_pyramid_scaling:
            logger.info("SPY breakout detected — boosting position size")
        
        # Policy gate
        policy_result = self._apply_policy_gate(candidate)
        report["policy_status"] = policy_result.status
        report["policy_reasons"] = policy_result.reasons
        report["allowed_action"] = policy_result.allowed_action
        
        if policy_result.status != "APPROVED":
            return self._format_report(report, candidate)
        
        # Execute trade
        trade_actions = self._execute_trade(candidate, spy_status)
        report["actions_taken"] = trade_actions
        
        return self._format_report(report, candidate)
    
    def monitor_positions(self):
        """Periodic monitor: check trailing stops, profit-taking, time decay."""
        self.account = self.broker.get_account()
        actions = []
        
        for pos in self.account.positions:
            market = self._get_market_snapshot(symbol=pos.symbol)
            
            # Configure advanced monitor
            trailing_conf = TrailingStopConfig(
                initial_stop_pct=config.trailing_stop_initial_pct,
                trail_pct=config.trailing_stop_trail_pct,
                activation_pct=config.trailing_stop_activation_pct
            )
            profit_conf = ProfitTakingConfig(
                first_target_pct=config.profit_taking_first_target_pct,
                first_take_pct=config.profit_taking_first_take_pct
            )
            
            # Create monitor
            monitor = PositionMonitor(
                position=pos,
                market=market,
                trailing_stop_config=trailing_conf,
                profit_taking_config=profit_conf
            )
            
            # Get action
            action = monitor.update(market)
            actions.append(action)
            
            # Execute if not HOLD
            if action["action"] != "hold":
                self._execute_monitor_action(pos, action)
        
        return actions
    
    def _apply_policy_gate(self, candidate: TradeCandidate) -> PolicyResult:
        """Run through policy engine with all gates."""
        account = self.broker.get_account()
        market = self._get_market_snapshot()
        risk = self._get_risk_snapshot()
        
        return self.policy_engine.evaluate(candidate, account, market, risk)
    
    def _execute_trade(self, candidate: TradeCandidate, spy_status: dict) -> list:
        """Execute single trade, possibly with pyramid scaling."""
        actions = []
        
        # Initial order
        from ..models.order_request import OrderRequest
        order = OrderRequest(
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            side=candidate.order.side,
            order_type=candidate.order.order_type,
            qty=candidate.order.quantity,
            limit_price=candidate.order.limit_price
        )
        
        # Momentum boost or pyramid scaling
        if spy_status["in_breakout"] and candidate.symbol == "SPY" and config.allow_pyramid_scaling:
            entry_price = candidate.order.limit_price or spy_status["current_price"]
            current_price = spy_status["current_price"]
            sizing = self._pyramid_sizer.calculate_position_size(
                current_price=current_price,
                entry_price=entry_price,
                current_quantity=order.qty
            )
            if sizing["add_notional"] > 0:
                order.qty += sizing["add_quantity"]
                actions.append({
                    "action": "pyramid_boost",
                    "level": sizing["level"],
                    "reason": sizing["reason"],
                    "new_qty": order.qty,
                    "new_notional": sizing["new_total_notional"]
                })
        
        # Submit
        result = self.broker.submit_order(order)
        actions.append({
            "action": "order_submitted",
            "symbol": candidate.symbol,
            "side": candidate.order.side,
            "order_type": candidate.order.order_type,
            "qty": order.qty,
            "price": candidate.order.limit_price,
            "broker_status": result
        })
        logger.info(f"Trade executed: {actions[-1]}")
        
        return actions
    
    def _execute_monitor_action(self, position: PositionSnapshot, action: dict):
        """Execute trailing stop or profit-taking action."""
        from ..models.order_request import OrderRequest
        
        qty = position.quantity * action["sell_pct"]
        side = "sell" if action["action"] == "sell_all" else "sell"
        
        order = OrderRequest(
            candidate_id=position.position_id,
            symbol=position.symbol,
            side=side,
            order_type="market",
            qty=qty
        )
        
        result = self.broker.submit_order(order)
        
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "symbol": position.symbol,
            "action": action["action"],
            "sell_pct": action["sell_pct"],
            "qty": qty,
            "reason": action["reason"],
            "broker_result": result
        }
        logger.warning(f"Position monitor action: {log_entry}")
        return log_entry
    
    def _research_to_candidate(self, research_result: dict) -> dict:
        """Convert Vibe-Trading research to TradeCandidate."""
        if not research_result:
            return {"action": "no_trade"}
        
        exit_plan = {
            "profit_take_rule": research_result.get("exit_profit_take", ""),
            "stop_loss_rule": research_result.get("exit_stop_loss", ""),
            "time_exit_rule": research_result.get("exit_time", "")
        }
        
        risk = {
            "max_loss_usd": research_result.get("max_loss", 0.0),
            "expected_loss_usd": research_result.get("expected_loss", 0.0),
            "max_profit_usd": research_result.get("max_profit", 0.0),
            "risk_reward_ratio": research_result.get("risk_reward", 0.0),
            "position_notional_usd": research_result.get("notional", 0.0)
        }
        
        evidence = {
            "market_data_timestamp": research_result.get("data_timestamp", ""),
            "vibe_summary": research_result.get("vibe_summary", ""),
            "tradingagents_summary": research_result.get("agents_summary", ""),
            "bull_case": research_result.get("bull_case", ""),
            "bear_case": research_result.get("bear_case", ""),
            "risk_case": research_result.get("risk_case", "")
        }
        
        # Default quantity
        notional = research_result.get("order_notional", config.initial_trade_notional)
        limit_price = research_result.get("limit_price", 0.0) 
        quantity = min(10.0, notional / max(limit_price, 0.01)) if limit_price > 0 else 0.0
        
        return {
            "candidate_id": research_result.get("id", str(uuid.uuid4())),
            "created_at": datetime.utcnow().isoformat() + "Z",
            "mode": "PAPER_AUTONOMOUS",
            "asset_class": "equity",
            "underlying": research_result.get("underlying", "SPY"),
            "symbol": research_result.get("symbol", "SPY"),
            "strategy": "fractional_etf",
            "direction": research_result.get("direction", "bullish"),
            "action": research_result.get("action", "open"),
            "order": {
                "side": research_result.get("order_side", "buy"),
                "order_type": research_result.get("order_type", "limit"),
                "quantity": quantity,
                "notional_usd": notional,
                "limit_price": limit_price
            },
            "risk": risk,
            "evidence": evidence,
            "exit_plan": exit_plan,
            "confidence": {
                "score_0_to_100": research_result.get("confidence_score", 70),
                "label": research_result.get("confidence_label", "medium"),
                "reason": research_result.get("confidence_reason", "")
            }
        }
    
    def _get_market_snapshot(self, symbol: str = "SPY") -> MarketSnapshot:
        """Get current market snapshot from broker."""
        # In paper/live, implement Alpaca calls here
        # For now, return mock snapshot
        return MarketSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            symbol=symbol,
            last_price=440.0,
            bid=439.95,
            ask=440.05,
            spread_pct=0.0227,
            volume=50_000_000,
            market_open=True
        )
    
    def _get_risk_snapshot(self) -> RiskSnapshot:
        """Get current risk snapshot from broker."""
        # In paper/live, implement Alpaca calls here
        # Return conservative defaults
        return RiskSnapshot(
            daily_pnl=0.0,
            weekly_pnl=0.0,
            monthly_pnl=0.0,
            consecutive_losses=0,
            trades_today=0,
            trades_this_week=0,
            daily_loss_budget_remaining=config.max_daily_loss_usd,
            weekly_loss_budget_remaining=config.max_weekly_loss_usd,
            monthly_loss_budget_remaining=config.max_monthly_loss_usd
        )
    
    def _format_report(self, report: dict, candidate: TradeCandidate) -> dict:
        """Final report formatting."""
        if candidate:
            score = self.scoring_engine.score(candidate)
            report.update({
                "candidate": candidate.to_log_dict(),
                "score": {
                    "evidence": score.evidence_score,
                    "committee": score.committee_score,
                    "liquidity": score.liquidity_score,
                    "risk": score.risk_score,
                    "operational": score.operational_score,
                    "technical": score.technical_score,
                    "total": score.total,
                    "tier": score.tier
                }
            })
        return report