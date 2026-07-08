"""TradingAgents research — direct LLM multi-agent analysis.

Old approach: subprocess → TradingAgents CLI → LLM (broken/timeout)
New approach: direct OpenAI API call → structured multi-perspective analysis

Simulates the TradingAgents committee debate with a single LLM call
that analyzes from both bull and bear perspectives.
"""

import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("hermes_trader.research.agents")


class TradingAgentsClient:
    """Multi-agent committee analysis via direct LLM call.

    Replaces the broken TradingAgents CLI with a direct API call
    that simulates bull/bear debate in a single prompt.
    """

    def __init__(self, agents_path: str = "/opt/vendor/TradingAgents"):
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

    def get_committee_signal(self, symbol: str) -> dict:
        """Get multi-perspective committee signal for a symbol.

        Simulates TradingAgents' bull/bear debate with a structured prompt.
        Returns signal (bullish/bearish/neutral) and confidence.
        """
        prompt = f"""You are a trading committee analyzing {symbol} for 0DTE options.

BULL ANALYST: Argue why {symbol} will go UP today. Consider:
- Technical setup, support levels, momentum
- Sector rotation, institutional flow
- Any catalysts or positive news

BEAR ANALYST: Argue why {symbol} will go DOWN today. Consider:
- Resistance levels, overbought conditions
- Market weakness, sector headwinds
- Any negative catalysts or risk factors

COMMITTEE CHAIR: Based on both arguments, provide:
1. SIGNAL: "bullish", "bearish", or "neutral"
2. CONFIDENCE: 0-100 (how strong is the conviction)
3. REASONING: 2-3 sentences explaining the decision
4. KEY LEVELS: Support and resistance prices

Format your response as:
SIGNAL: [bullish/bearish/neutral]
CONFIDENCE: [0-100]
REASONING: [2-3 sentences]
SUPPORT: [price]
RESISTANCE: [price]"""

        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are a professional trading committee. Analyze from both bull and bear perspectives, then make a data-driven consensus decision."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,
            )
            output = resp.choices[0].message.content or ""

            # Parse signal and confidence from output
            signal = "neutral"
            confidence = 50
            for line in output.split("\n"):
                line_lower = line.lower().strip()
                if "signal:" in line_lower:
                    if "bullish" in line_lower:
                        signal = "bullish"
                    elif "bearish" in line_lower:
                        signal = "bearish"
                    else:
                        signal = "neutral"
                if "confidence:" in line_lower:
                    try:
                        confidence = int("".join(c for c in line.split(":")[-1] if c.isdigit()) or "50")
                    except ValueError:
                        confidence = 50

            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "signal": signal,
                "confidence": confidence,
                "decision": output,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"TradingAgents analysis failed for {symbol}: {e}")
            return {
                "status": "FAILED",
                "symbol": symbol,
                "signal": "neutral",
                "confidence": 0,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }

    def run_analysis(self, symbol: str, date: Optional[str] = None) -> dict:
        """Run full analysis for a symbol."""
        return self.get_committee_signal(symbol)
