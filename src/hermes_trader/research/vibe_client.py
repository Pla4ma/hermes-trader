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

logger = logging.getLogger("hermes_trader.vibe_client")


class VibeTradingClient:
    """Wraps Vibe-Trading CLI for research operations."""

    def __init__(self, vibe_trading_path: str = "/opt/vendor/Vibe-Trading"):
        self.vibe_path = Path(vibe_trading_path)

    @property
    def available(self) -> bool:
        return self.vibe_path.exists()

    def _get_env(self) -> dict:
        """Get environment variables for Vibe-Trading."""
        env = {**os.environ}
        env["LANGCHAIN_PROVIDER"] = "openai"
        env["LANGCHAIN_MODEL_NAME"] = "xiaomi/mimo-v2.5-pro"
        env["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
        env["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "https://api.commandcode.ai/provider/v1")
        return env

    def run_market_regime_analysis(self, symbol: str) -> dict:
        """Run Vibe-Trading market regime analysis for a symbol."""
        if not self.available:
            return {"status": "SKIPPED", "reason": "Vibe-Trading path not found"}

        try:
            prompt = f"Analyze the current market regime for {symbol}. What is the trend, momentum, and best strategy?"
            cmd = ["/opt/hermes-trader/.venv/bin/python", "-m", "cli", "run", "-p", prompt]
            r = subprocess.run(
                cmd,
                cwd=str(self.vibe_path / "agent"),
                env=self._get_env(),
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = r.stdout if r.stdout else r.stderr
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "output": output[-3000:] if output else "",
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
            prompt = f"Backtest the {strategy} strategy on {symbol}. What are the results?"
            cmd = ["/opt/hermes-trader/.venv/bin/python", "-m", "cli", "run", "-p", prompt]
            r = subprocess.run(
                cmd,
                cwd=str(self.vibe_path / "agent"),
                env=self._get_env(),
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = r.stdout if r.stdout else r.stderr
            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "strategy": strategy,
                "output": output[-3000:] if output else "",
                "exit_code": r.returncode,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "symbol": symbol, "strategy": strategy}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}
