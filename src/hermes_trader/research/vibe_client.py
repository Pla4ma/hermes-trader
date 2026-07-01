"""Vibe-Trading research wrapper.

Invokes Vibe-Trading for market regime analysis, strategy research,
and backtesting. All calls are wrapped in timeout and error handling.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import config

logger = logging.getLogger("hermes_trader.research.vibe")


class VibeTradingClient:
    """Wraps Vibe-Trading CLI for research operations."""

    def __init__(self):
        self.vibe_path = Path(config.vibe_trading_path)

    @property
    def available(self) -> bool:
        return config.vibe_trading_enabled and self.vibe_path.exists()

    def run_research(self, symbols: Optional[list[str]] = None) -> dict:
        """Run Vibe-Trading research cycle and return structured output.

        Phase 1: calls vibe CLI and captures stdout.
        Phase 1.5+: structured JSON output from Vibe-Trading.
        """
        if not self.available:
            return {"status": "SKIPPED", "reason": "Vibe-Trading not available", "output": "", "symbols": symbols or []}

        target = symbols or list(config.allowed_underlyings)
        results = {}
        for symbol in target:
            try:
                cmd = ["python3", "-m", "vibe_trading.cli", "research", "--symbol", symbol]
                r = subprocess.run(cmd, cwd=self.vibe_path, capture_output=True, text=True, timeout=60)
                results[symbol] = {
                    "stdout": r.stdout[-2000:] if r.stdout else "",
                    "stderr": r.stderr[-500:] if r.stderr else "",
                    "exit_code": r.returncode,
                }
            except subprocess.TimeoutExpired:
                results[symbol] = {"error": "TIMEOUT", "stdout": "", "stderr": ""}
            except FileNotFoundError:
                self._vibe_path = None
                return {"status": "SKIPPED", "reason": "vibe_trading module not installed", "output": "", "symbols": target}

        return {"status": "COMPLETED", "symbols": target, "results": results, "timestamp": datetime.utcnow().isoformat()}

    def run_backtest(self, symbol: str, strategy: str = "momentum") -> dict:
        """Run a backtest on a specific symbol+strategy."""
        if not self.available:
            return {"status": "SKIPPED", "reason": "Vibe-Trading not available"}

        try:
            cmd = ["python3", "-m", "vibe_trading.cli", "backtest", "--symbol", symbol, "--strategy", strategy]
            r = subprocess.run(cmd, cwd=self.vibe_path, capture_output=True, text=True, timeout=120)
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "strategy": strategy,
                "stdout": r.stdout[-3000:],
                "stderr": r.stderr[-500:],
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "symbol": symbol, "strategy": strategy}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}