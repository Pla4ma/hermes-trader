"""TradingAgents research wrapper.

Invokes TradingAgents for multi-agent debate/committee analysis,
trade idea generation, and consensus scoring.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import config

logger = logging.getLogger("hermes_trader.research.tradingagents")


class TradingAgentsClient:
    """Wraps TradingAgents CLI for multi-agent analysis."""

    def __init__(self):
        self.agents_path = Path(config.tradingagents_path)

    @property
    def available(self) -> bool:
        return config.tradingagents_enabled and self.agents_path.exists()

    def run_analysis(self, symbols: Optional[list[str]] = None) -> dict:
        """Run TradingAgents multi-agent analysis and capture output.

        Phase 1: calls main.py or entry script, captures stdout.
        Phase 1.5+: structured JSON output.
        """
        if not self.available:
            return {"status": "SKIPPED", "reason": "TradingAgents not available"}

        target = symbols or list(config.allowed_underlyings)
        results = {}
        for symbol in target:
            try:
                cmd = ["python3", "main.py", "--symbol", symbol, "--mode", "research"]
                r = subprocess.run(cmd, cwd=self.agents_path, capture_output=True, text=True, timeout=120)
                results[symbol] = {
                    "stdout": r.stdout[-3000:],
                    "stderr": r.stderr[-500:],
                    "exit_code": r.returncode,
                }
            except subprocess.TimeoutExpired:
                results[symbol] = {"error": "TIMEOUT", "stdout": "", "stderr": ""}
            except FileNotFoundError:
                return {"status": "ERROR", "reason": "TradingAgents entry point not found"}
            except Exception as e:
                results[symbol] = {"error": str(e)}

        return {"status": "COMPLETED", "symbols": target, "results": results, "timestamp": datetime.utcnow().isoformat()}

    def debate(self, symbol: str, direction: str = "bullish") -> dict:
        """Run an agent debate on a specific symbol and direction."""
        if not self.available:
            return {"status": "SKIPPED", "reason": "TradingAgents not available"}

        try:
            cmd = ["python3", "main.py", "--symbol", symbol, "--mode", "debate", "--direction", direction]
            r = subprocess.run(cmd, cwd=self.agents_path, capture_output=True, text=True, timeout=180)
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "direction": direction,
                "stdout": r.stdout[-3000:],
                "stderr": r.stderr[-500:],
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "symbol": symbol, "direction": direction}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}