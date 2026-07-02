"""Candidate scoring engine (Section 35 of mandate).

Scores a trade candidate on 6 dimensions (100 points max).
This is deterministic code, not LLM reasoning.
"""

from ..models.trade_candidate import TradeCandidate, CandidateScore
from ..research.technical_scan import TechnicalScanner


class ScoringEngine:
    """Deterministic trade candidate scoring engine."""

    def __init__(self):
        self.technical_scanner = TechnicalScanner()

    def score(self, candidate: TradeCandidate) -> CandidateScore:
        """Score a validated TradeCandidate across all dimensions."""
        evidence = self._score_evidence(candidate)     # /20
        committee = self._score_committee(candidate)   # /20
        liquidity = self._score_liquidity(candidate)   # /15
        risk = self._score_risk(candidate)             # /15
        operational = self._score_operational(candidate) # /10
        technical = self._score_technical(candidate)   # /20
        return CandidateScore(
            evidence_score=evidence,
            committee_score=committee,
            liquidity_score=liquidity,
            risk_score=risk,
            operational_score=operational,
            technical_score=technical,
        )

    def _score_evidence(self, c: TradeCandidate) -> int:
        """Evidence quality (0-20)."""
        score = 0
        ev = c.evidence

        # Has Vibe-Trading analysis
        if ev.vibe_summary and len(ev.vibe_summary) > 100:
            score += 6
        elif ev.vibe_summary:
            score += 3

        # Has TradingAgents analysis
        if ev.tradingagents_summary and len(ev.tradingagents_summary) > 100:
            score += 6
        elif ev.tradingagents_summary:
            score += 3

        # Has bull case
        if ev.bull_case and len(ev.bull_case) > 50:
            score += 2

        # Has bear case
        if ev.bear_case and len(ev.bear_case) > 50:
            score += 2

        # Has risk case
        if ev.risk_case and len(ev.risk_case) > 30:
            score += 2

        # Has market data timestamp (freshness)
        if ev.market_data_timestamp:
            score += 2

        # Has transaction cost / slippage assumptions
        if ev.transaction_cost_assumption or ev.slippage_assumption:
            score += 1

        # Known limitations acknowledged
        score += min(len(ev.known_limitations), 1)  # Cap at 1

        # Bonus for backtest
        if ev.backtest_summary:
            score += 2

        return min(score, 20)

    def _score_committee(self, c: TradeCandidate) -> int:
        """Multi-agent committee consensus (0-20)."""
        score = 0
        ev = c.evidence

        # Both research tools ran
        if ev.vibe_summary and ev.tradingagents_summary:
            score += 8

        # Non-empty market data timestamp suggests real data was used
        if ev.market_data_timestamp:
            score += 4

        # Bull and bear cases both present (balanced analysis)
        if ev.bull_case and ev.bear_case:
            score += 4

        # Risk case present
        if ev.risk_case:
            score += 4

        return min(score, 20)

    def _score_liquidity(self, c: TradeCandidate) -> int:
        """Liquidity assessment (0-15)."""
        score = 0

        # SPY/QQQ/VOO are highly liquid ETFs
        if c.underlying in ("SPY", "QQQ"):
            base = 10
        elif c.underlying == "VOO":
            base = 8
        else:
            base = 4

        # For options, check liquidity metrics
        if c.strategy in ("long_call", "long_put", "debit_spread_paper"):
            od = c.option_details

            # Volume bonus
            if od.volume >= 5000:
                volume_bonus = 3
            elif od.volume >= 1000:
                volume_bonus = 2
            elif od.volume >= 100:
                volume_bonus = 1
            else:
                volume_bonus = -3  # Penalty for low volume

            # Open interest bonus
            if od.open_interest >= 10000:
                oi_bonus = 3
            elif od.open_interest >= 1000:
                oi_bonus = 2
            elif od.open_interest >= 100:
                oi_bonus = 1
            else:
                oi_bonus = -2  # Penalty for low OI

            score = base + volume_bonus + oi_bonus
        else:
            score = base

        return max(0, min(score, 15))

    def _score_risk(self, c: TradeCandidate) -> int:
        """Risk assessment (0-15)."""
        score = 15  # Start with full points, deduct for risks
        risk = c.risk

        # Deduct for high max loss
        if risk.max_loss_usd > 2.0:
            score -= 3
        elif risk.max_loss_usd > 1.0:
            score -= 1

        # Deduct for poor risk/reward
        if risk.risk_reward_ratio < 0.5:
            score -= 3
        elif risk.risk_reward_ratio < 1.0:
            score -= 1

        # Deduct for high position size
        if risk.position_notional_usd > 8.0:
            score -= 3
        elif risk.position_notional_usd > 5.0:
            score -= 1

        # Deduct for high expected loss
        if risk.expected_loss_usd > 0.5:
            score -= 2
        elif risk.expected_loss_usd > 0.25:
            score -= 1

        return max(0, score)

    def _score_operational(self, c: TradeCandidate) -> int:
        """Operational feasibility (0-10)."""
        score = 10  # Start full, deduct for issues

        # Deduct if not in market hours (if required)
        # This is handled in policy gate, but we can note it here

        # Deduct for complex strategies if not allowed
        if c.strategy not in ["no_trade", "fractional_etf", "long_call", "long_put", "debit_spread_paper"]:
            score -= 3

        # Deduct for missing order details
        if not c.order.side or not c.order.order_type:
            score -= 2

        # Deduct for zero quantity/notional
        if c.order.quantity <= 0 and c.order.notional_usd <= 0:
            score -= 2

        return max(0, score)

    def _score_technical(self, c: TradeCandidate) -> int:
        """Technical analysis score (0-20)."""
        try:
            score, details = self.technical_scanner.scan(c.underlying)
            # The technical scanner returns 0-15, we want to map to 0-20
            # Scale up: score * (20/15) = score * 4/3
            scaled_score = int(round(score * 20 / 15))
            return max(0, min(scaled_score, 20))
        except Exception as e:
            # If technical scan fails, return middle score
            return 10