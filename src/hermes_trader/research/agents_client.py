"""TradingAgents research wrapper.

Invokes TradingAgents for multi-agent debate/committee analysis,
trade idea generation, and consensus scoring.
"""

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes_trader.research.agents")


class TradingAgentsClient:
    """Wraps TradingAgents CLI for multi-agent debate analysis.

    TradingAgents entry point: tradingagents (from pyproject.toml)
    Key command: python -c "from tradingagents...; ta.propagate('NVDA', '2024-05-10')"
    """

    def __init__(self, agents_path: str = "/opt/vendor/TradingAgents"):
        self.agents_path = Path(agents_path)
        # Ensure CMDDD env vars are set for subprocess/sys.path import
        import os
        if "OPENAI_API_KEY" not in os.environ:
            os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
        if "OPENAI_BASE_URL" not in os.environ:
            os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "https://api.commandcode.ai/provider/v1")

    @property
    def available(self) -> bool:
        return self.agents_path.exists()

    def run_analysis(self, symbol: str, date: Optional[str] = None) -> dict:
        """Run TradingAgents multi-agent analysis.

        Returns structured output for scoring engine.
        """
        if not self.available:
            return {"status": "SKIPPED", "reason": "TradingAgents path not found"}

        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        try:
            cmd = [
                "python3", "-c",
                f"import os, sys; os.environ.update({{'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY',''), 'OPENAI_BASE_URL': os.getenv('OPENAI_BASE_URL','https://api.commandcode.ai/provider/v1')}}); sys.path.insert(0, '{self.agents_path}'); "
                f"from tradingagents.graph.trading_graph import TradingAgentsGraph; "
                f"from tradingagents.default_config import DEFAULT_CONFIG; "
                f"ta = TradingAgentsGraph(config=DEFAULT_CONFIG); "
                f"result = ta.propagate('{symbol}', '{date}'); "
                f"print(result[1] if len(result) > 1 else result)"
            ]
            r = subprocess.run(
                cmd,
                cwd=str(self.agents_path),
                capture_output=True,
                text=True,
                timeout=180,
            )
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "date": date,
                "decision": r.stdout[-2000:] if r.stdout else "",
                "error": r.stderr[-500:] if r.stderr else "",
                "exit_code": r.returncode,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "symbol": symbol}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    def get_committee_signal(self, symbol: str) -> dict:
        """Return bullish/bearish/neutral + confidence."""
        result = self.run_analysis(symbol)
        if result["status"] != "COMPLETED":
            return result

        output = result.get("decision", "").lower()
        if "bullish" in output:
            signal = "bullish"
        elif "bearish" in output:
            signal = "bearish"
        else:
            signal = "neutral"

        confidence = 0
        if "strong" in output:
            confidence = 85
        elif "moderate" in output:
            confidence = 65
        elif "cautious" in output:
            confidence = 45

        return {**result, "signal": signal, "confidence": confidence}