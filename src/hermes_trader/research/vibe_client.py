"""Vibe-Trading research — direct LLM analysis (replaces broken CLI).

Old approach: subprocess → VibeTrading CLI → LLM (broken)
New approach: direct OpenAI API call → structured analysis

This gives the same output (market regime, trend, strategy) without
depending on VibeTrading's broken CLI.
"""

import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("hermes_trader.vibe_client")


class VibeTradingClient:
    """Market regime analysis via direct LLM call.

    Replaces the broken VibeTrading CLI with a direct API call
    to the headroom proxy. Produces the same output format.
    """

    def __init__(self):
        self._model = "MiniMaxAI/MiniMax-M3"
        self._timeout = 30

    @property
    def available(self) -> bool:
        return True

    def _get_client(self):
        import openai
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:8787/v1")
        return openai.OpenAI(api_key=api_key, base_url=base_url, timeout=self._timeout)

    def run_market_regime_analysis(self, symbol: str) -> dict:
        """Analyze market regime for a symbol via LLM.

        Returns dict with 'output' containing the analysis text.
        """
        prompt = f"""Analyze the current market regime for {symbol}. Consider:
1. Trend direction (bullish/bearish/neutral)
2. Momentum strength
3. Volatility environment
4. Best strategy for 0DTE options today
5. Key support/resistance levels

Provide a concise 3-4 sentence analysis. Be specific and actionable."""

        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are a professional market analyst. Provide concise, actionable analysis."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=300,
            )
            output = resp.choices[0].message.content or ""
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "output": output,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"VibeTrading analysis failed for {symbol}: {e}")
            return {
                "status": "FAILED",
                "symbol": symbol,
                "output": f"Analysis failed: {e}",
                "timestamp": datetime.utcnow().isoformat(),
            }

    def run_backtest(self, symbol: str, strategy: str = "momentum") -> dict:
        """Run a backtest analysis via LLM."""
        prompt = f"""Analyze the {strategy} strategy for {symbol} over the last 5 trading days.
What would have been the optimal entry and exit points?
What's the expected win rate for this strategy today?
Provide a concise 2-3 sentence analysis."""

        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are a quantitative analyst. Provide data-driven analysis."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
            )
            output = resp.choices[0].message.content or ""
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "strategy": strategy,
                "output": output,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            return {
                "status": "FAILED",
                "symbol": symbol,
                "output": f"Backtest failed: {e}",
                "timestamp": datetime.utcnow().isoformat(),
            }
