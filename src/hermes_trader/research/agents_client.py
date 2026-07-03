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
            script = (
                f"import os, sys; "
                f"sys.path.insert(0, '{self.agents_path}'); "
                f"from tradingagents.graph.trading_graph import TradingAgentsGraph; "
                f"from tradingagents.default_config import DEFAULT_CONFIG; "
                f"config = dict(DEFAULT_CONFIG); "
                f"config['llm_provider'] = 'openai_compatible'; "
                f"config['backend_url'] = os.environ.get('OPENAI_BASE_URL', 'https://api.commandcode.ai/provider/v1'); "
                f"config['deep_think_llm'] = os.environ.get('TRADINGAGENTS_DEEP_THINK_LLM', 'xiaomi/mimo-v2.5-pro'); "
                f"config['quick_think_llm'] = os.environ.get('TRADINGAGENTS_QUICK_THINK_LLM', 'xiaomi/mimo-v2.5-pro'); "
                f"ta = TradingAgentsGraph(config=config); "
                f"result = ta.propagate('{symbol}', '{date}'); "
                f"print(result[1] if len(result) > 1 else result)"
            )
            cmd = ["/opt/hermes-trader/.venv/bin/python", "-c", script]
            # Pass CMDDD env vars through subprocess env parameter
            sub_env = {**os.environ}
            sub_env["OPENAI_COMPATIBLE_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")
            r = subprocess.run(
                cmd,
                cwd=str(self.agents_path),
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes for TradingAgents (slow LLM calls)
                env=sub_env,
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