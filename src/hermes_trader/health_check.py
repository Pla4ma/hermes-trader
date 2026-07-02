"""System health check — verifies all components are working."""

import json
import os
from datetime import datetime


def health_check() -> dict:
    """Run comprehensive system health check."""
    checks = {}

    # 1. Alpaca API connection
    try:
        import alpaca_trade_api as tradeapi
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        api = tradeapi.REST(api_key, secret_key, base_url)
        acct = api.get_account()
        checks["alpaca"] = {
            "status": "OK",
            "equity": float(acct.portfolio_value),
            "cash": float(acct.cash),
            "account_status": acct.status,
        }
    except Exception as e:
        checks["alpaca"] = {"status": "FAIL", "error": str(e)}

    # 2. yfinance market data
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        price = spy.fast_info.get("lastPrice", 0)
        checks["yfinance"] = {
            "status": "OK" if price > 0 else "WARN",
            "spy_price": round(price, 2),
        }
    except Exception as e:
        checks["yfinance"] = {"status": "FAIL", "error": str(e)}

    # 3. .env file
    env_path = "/opt/hermes-trader/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            content = f.read()
        checks["env"] = {
            "status": "OK",
            "has_alpaca_key": "ALPACA_API_KEY=AKG" in content,
            "has_live_trading": "ENABLE_LIVE_TRADING=true" in content,
            "has_cyrillic": any(ord(c) > 127 for c in content),
        }
    else:
        checks["env"] = {"status": "FAIL", "error": ".env not found"}

    # 4. Journal files
    journals = {
        "decisions": "/opt/hermes-trader/data/journals/decisions.jsonl",
        "orders": "/opt/hermes-trader/data/journals/paper_orders.jsonl",
    }
    for name, path in journals.items():
        if os.path.exists(path):
            with open(path) as f:
                lines = f.readlines()
            checks[f"journal_{name}"] = {"status": "OK", "entries": len(lines)}
        else:
            checks[f"journal_{name}"] = {"status": "MISSING"}

    # 5. Tests
    try:
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
            cwd="/opt/hermes-trader",
            capture_output=True, text=True, timeout=30,
        )
        checks["tests"] = {
            "status": "OK" if result.returncode == 0 else "FAIL",
            "output": result.stdout.strip()[-100:],
        }
    except Exception as e:
        checks["tests"] = {"status": "ERROR", "error": str(e)}

    # 6. Git status
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd="/opt/hermes-trader",
            capture_output=True, text=True,
        )
        checks["git"] = {
            "status": "OK",
            "recent_commits": result.stdout.strip().split("\n"),
        }
    except Exception as e:
        checks["git"] = {"status": "ERROR", "error": str(e)}

    # Overall status
    failures = [k for k, v in checks.items() if v.get("status") == "FAIL"]
    overall = "HEALTHY" if not failures else f"DEGRADED ({len(failures)} failures)"

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "overall": overall,
        "failures": failures,
        "checks": checks,
    }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/opt/hermes-trader/.env")
    result = health_check()
    print(f"Overall: {result['overall']}")
    for name, check in result["checks"].items():
        emoji = "✅" if check["status"] == "OK" else "❌" if check["status"] == "FAIL" else "⚠️"
        print(f"  {emoji} {name}: {check['status']}")
