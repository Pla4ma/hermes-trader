"""Candidate scoring engine (Section 35 of mandate).

Scores a trade candidate on 5 dimensions (100 points max).
This is deterministic code, not LLM reasoning.
"""

from ..models.trade_candidate import TradeCandidate, CandidateScore


class ScoringEngine:
    """Deterministic trade candidate scoring engine."""

    def score(self, candidate: TradeCandidate) -> CandidateScore:
        """Score a validated TradeCandidate across all dimensions."""
        evidence = self._score_evidence(candidate)     # /25
        committee = self._score_committee(candidate)   # /25
        liquidity = self._score_liquidity(candidate)   # /20
        risk = self._score_risk(candidate)             # /20
        operational = self._score_operational(candidate) # /10
        return CandidateScore(
            evidence_score=evidence,
            committee_score=committee,
            liquidity_score=liquidity,
            risk_score=risk,
            operational_score=operational,
        )

    def _score_evidence(self, c: TradeCandidate) -> int:
        """Evidence quality (0-25)."""
        score = 0
        ev = c.evidence

        # Has Vibe-Trading analysis
        if ev.vibe_summary and len(ev.vibe_summary) > 100:
            score += 8
        elif ev.vibe_summary:
            score += 4

        # Has TradingAgents analysis
        if ev.tradingagents_summary and len(ev.tradingagents_summary) > 100:
            score += 8
        elif ev.tradingagents_summary:
            score += 4

        # Has bull case
        if ev.bull_case and len(ev.bull_case) > 50:
            score += 3

        # Has bear case
        if ev.bear_case and len(ev.bear_case) > 50:
            score += 3

        # Has risk case
        if ev.risk_case and len(ev.risk_case) > 30:
            score += 3

        # Has market data timestamp (freshness)
        if ev.market_data_timestamp:
            score += 3

        # Has transaction cost / slippage assumptions
        if ev.transaction_cost_assumption or ev.slippage_assumption:
            score += 2

        # Known limitations acknowledged
        score += min(len(ev.known_limitations), 2)  # Cap at 2

        # Bonus for backtest
        if ev.backtest_summary:
            score += 3

        return min(score, 25)

    def _score_committee(self, c: TradeCandidate) -> int:
        """Multi-agent committee consensus (0-25)."""
        score = 0
        ev = c.evidence

        # Both research tools ran
        if ev.vibe_summary and ev.tradingagents_summary:
            score += 10

        # Non-empty market data timestamp suggests real data was used
        if ev.market_data_timestamp:
            score += 5

        # Bull and bear cases both present (balanced analysis)
        if ev.bull_case and ev.bear_case:
            score += 5

        # Risk case present
        if ev.risk_case:
            score += 5

        return min(score, 25)

    def _score_liquidity(self, c: TradeCandidate) -> int:
        """Liquidity assessment (0-20)."""
        score = 0

        # SPY/QQQ/VOO are highly liquid ETFs
        if c.underlying in ("SPY", "QQQ"):
            base = 15
        elif c.underlying == "VOO":
            base = 12
        else:
            base = 5

        # For options, check liquidity metrics
        if c.strategy in ("long_call", "long_put", "debit_spread_paper"):
            od = c.option_details

            # Volume bonus
            if od.volume >= 5000:
                volume_bonus = 5
            elif od.volume >= 1000:
                volume_bonus = 3
            elif od.volume >= 100:
                volume_bonus = 1
            else:
                volume_bonus = -5  # Penalty for low volume

            # Open interest bonus
            if od.open_interest >= 10000:
                oi_bonus = 5
            elif od.open_interest >= 1000:
                oi_bonus = 3
            elif od.open_interest >= 100:
                oi_bonus = 1
            else:
                oi_bonus = -5

            # Spread quality
            spread_pct = abs(((od.ask - od.bid) / od.midpoint) * 100) if od.midpoint > 0 else 100
            if spread_pct <= 5:
                spread_bonus = 5
            elif spread_pct <= 10:
                spread_bonus = 3
            elif spread_pct <= 15:
                spread_bonus = 1
            else:
                spread_bonus = -5

            score = base + volume_bonus + oi_bonus + spread_bonus
        else:
            # Equity — high liquidity by default
            score = 20

        return max(0, min(score, 20))

    def _score_risk(self, c: TradeCandidate) -> int:
        """Risk/reward quality (0-20)."""
        score = 0

        # Position size relative to capital
        risk = c.risk
        capital = 20.0  # $20 experimental capital

        # Position notional as % of capital
        pct = (risk.position_notional_usd / capital) * 100 if capital > 0 else 100
        # 5-25% is sweet spot → 8 points
        if 5 <= pct <= 25:
            score += 8
        elif 1 <= pct < 5:
            score += 5
        elif 25 < pct <= 50:
            score += 3
        elif pct < 1:
            score += 1
        else:
            score += 0  # > 50% → too risky

        # Risk/reward ratio
        if risk.risk_reward_ratio >= 2.0:
            score += 7
        elif risk.risk_reward_ratio >= 1.5:
            score += 5
        elif risk.risk_reward_ratio >= 1.0:
            score += 3
        elif risk.risk_reward_ratio > 0:
            score += 1
        # Negative R:R → no points

        # Max loss as % of capital
        loss_pct = (risk.max_loss_usd / capital) * 100 if capital > 0 else 100
        if loss_pct <= 5:
            score += 5
        elif loss_pct <= 10:
            score += 3
        elif loss_pct <= 20:
            score += 1
        # > 20% → no points

        return min(score, 20)

    def _score_operational(self, c: TradeCandidate) -> int:
        """Operational quality (0-10)."""
        score = 0

        # Has complete exit plan
        if c.exit_plan.profit_take_rule:
            score += 2
        if c.exit_plan.stop_loss_rule:
            score += 2
        if c.exit_plan.time_exit_rule or c.exit_plan.expiration_exit_rule:
            score += 2
        if c.exit_plan.emergency_exit_rule:
            score += 1

        # Has candidate_id (traceability)
        if c.candidate_id:
            score += 1

        # Source tracking
        if c.source.vibe_trading_run_id or c.source.tradingagents_run_id:
            score += 1

        # Known limitations acknowledged
        if c.evidence.known_limitations:
            score += min(len(c.evidence.known_limitations), 2)

        return min(score, 10)