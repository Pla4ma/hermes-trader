"""Vibe-Trading research wrapper.

Invokes Vibe-Trading for market regime analysis, strategy research,
and backtesting. All calls are wrapped in timeout and error handling.
"""

import os

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import logging



class VibeTradingClient:
    """Wraps Vibe-Trading CLI for research operations.

    Vibe-Trading entry point: vibe-trading (from pyproject.toml)
    Key command: vibe-trading research --symbol <ticker>
    """

    def __init__(self, vibe_trading_path: str = "/opt/vendor/Vibe-Trading"):
        self.vibe_path = Path(vibe_trading_path)

    @property
    def available(self) -> bool:
        return self.vibe_path.exists()

    def run_market_regime_analysis(self, symbol: str) -> dict:
        """Run Vibe-Trading market regime analysis for a symbol.

        Returns structured output for scoring engine.
        """
        if not self.available:
            return {"status": "SKIPPED", "reason": "Vibe-Trading path not found"}

        try:
            cmd = ["python3", "-m", "cli", "research", "--symbol", symbol]
            r = subprocess.run(
                cmd,
                cwd=str(self.vibe_path / "agent"),
                env={"LANGCHAIN_PROVIDER": "openai", "LANGCHAIN_MODEL_NAME": "auto", "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""), "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:3001/v1"), **os.environ},
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "output": r.stdout[-3000:] if r.stdout else "",
                "error": r.stderr[-500:] if r.stderr else "",
                "exit_code": r.returncode,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "symbol": symbol}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    def run_backtest(self, symbol: str, strategy: str = "momentum") -> dict:
        """Run a backtest on a specific symbol+strategy."""
        if not self.available:
            return {"status": "SKIPPED", "reason": "Vibe-Trading path not found"}

        try:
            cmd = ["python3", "-m", "cli", "backtest", "--symbol", symbol, "--strategy", strategy]
            r = subprocess.run(
                cmd,
                cwd=str(self.vibe_path / "agent"),
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "strategy": strategy,
                "output": r.stdout[-3000:],
                "error": r.stderr[-500:],
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "symbol": symbol, "strategy": strategy}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}