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
    """Wraps Vibe-Trading CLI for research operations.

    FIX (July 7, 2026):
    - Reduced timeout from 120s to 45s (CLI often hangs on LLM calls)
    - Non-zero exit codes now return FAILED status, not COMPLETED
    - Added retry logic (2 attempts with backoff)
    - Better error logging with stderr capture
    """

    def __init__(self, vibe_trading_path: str = "/opt/vendor/Vibe-Trading"):
        self.vibe_path = Path(vibe_trading_path)
        self._python_bin = self._find_python()
        self._max_retries = 2
        self._timeout = 45  # seconds per attempt (was 120, CLI hangs on LLM calls)

    def _find_python(self) -> str:
        """Find the best Python interpreter for Vibe-Trading."""
        # Prefer the hermes-trader venv (has Vibe-Trading deps installed)
        hermes_python = "/opt/hermes-trader/.venv/bin/python"
        if Path(hermes_python).exists():
            return hermes_python
        # Fallback to system python3
        return "python3"

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
        # Reduce LLM timeout inside Vibe-Trading itself
        env["LANGCHAIN_TIMEOUT"] = "30"
        return env

    def _run_cli(self, prompt: str, symbol: str, context: str = "") -> dict:
        """Run the Vibe-Trading CLI with retry logic.

        Returns a standardized result dict with correct status mapping.
        """
        if not self.available:
            return {"status": "SKIPPED", "reason": "Vibe-Trading path not found"}

        cmd = [self._python_bin, "-m", "cli", "run", "--json", "-p", prompt]
        env = self._get_env()
        cwd = str(self.vibe_path / "agent")
        last_error = None

        for attempt in range(1, self._max_retries + 1):
            try:
                logger.info(
                    "Vibe-Trading attempt %d/%d for %s %s",
                    attempt, self._max_retries, symbol, context,
                )
                r = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )

                # FIX: Non-zero exit code = FAILED, not COMPLETED
                if r.returncode != 0:
                    error_msg = (r.stderr or r.stdout or "no output")[-2000:]
                    last_error = f"exit_code={r.returncode}: {error_msg[:500]}"
                    logger.warning(
                        "Vibe-Trading failed (attempt %d): exit_code=%d, stderr=%s",
                        attempt, r.returncode, error_msg[:200],
                    )
                    if attempt < self._max_retries:
                        import time
                        time.sleep(2 * attempt)  # backoff: 2s, 4s
                        continue
                    # Final attempt failed
                    return {
                        "status": "FAILED",
                        "symbol": symbol,
                        "output": error_msg[-3000:],
                        "exit_code": r.returncode,
                        "error": last_error,
                        "attempts": attempt,
                        "timestamp": datetime.utcnow().isoformat(),
                    }

                # Success — exit_code == 0
                output = r.stdout if r.stdout else r.stderr
                return {
                    "status": "COMPLETED",
                    "symbol": symbol,
                    "output": output[-3000:] if output else "",
                    "exit_code": 0,
                    "attempts": attempt,
                    "timestamp": datetime.utcnow().isoformat(),
                }

            except subprocess.TimeoutExpired:
                logger.warning(
                    "Vibe-Trading timeout (attempt %d/%d) for %s",
                    attempt, self._max_retries, symbol,
                )
                last_error = f"timeout after {self._timeout}s"
                if attempt < self._max_retries:
                    import time
                    time.sleep(2 * attempt)
                    continue
                return {
                    "status": "TIMEOUT",
                    "symbol": symbol,
                    "error": last_error,
                    "attempts": attempt,
                    "timestamp": datetime.utcnow().isoformat(),
                }

            except Exception as e:
                logger.error("Vibe-Trading exception: %s", e)
                return {
                    "status": "ERROR",
                    "symbol": symbol,
                    "error": str(e),
                    "attempts": attempt,
                    "timestamp": datetime.utcnow().isoformat(),
                }

        # Should not reach here, but safety fallback
        return {"status": "ERROR", "symbol": symbol, "error": "all retries exhausted"}

    def run_market_regime_analysis(self, symbol: str) -> dict:
        """Run Vibe-Trading market regime analysis for a symbol."""
        prompt = (
            f"Analyze the current market regime for {symbol}. "
            f"What is the trend, momentum, and best strategy? "
            f"Reply with: signal (bullish/bearish/neutral), confidence (0-100), reasoning."
        )
        return self._run_cli(prompt, symbol, context="regime_analysis")

    def run_backtest(self, symbol: str, strategy: str = "momentum") -> dict:
        """Run a backtest on a specific symbol+strategy."""
        prompt = (
            f"Backtest the {strategy} strategy on {symbol}. "
            f"What are the results? Reply with: signal, win_rate, sharpe, reasoning."
        )
        return self._run_cli(prompt, symbol, context=f"backtest_{strategy}")
