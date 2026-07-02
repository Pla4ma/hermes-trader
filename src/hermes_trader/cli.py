"""Hermes Trader CLI - unified command interface."""

import argparse
import json
import sys
from datetime import datetime

from .workflow import DailyWorkflow
from .config import config
from .integrations.alpaca_broker import PaperBrokerAdapter
from .monitoring.telegram_reporter import format_report


def cmd_status(_args):
    """Show current system status."""
    broker = PaperBrokerAdapter()
    account = broker.get_account()

    report = {
        "cycle_timestamp": "",
        "mode": config.trader_mode,
        "underlying": "N/A",
        "strategy": "STATUS",
        "action": "status",
        "direction": "neutral",
        "policy_status": "STATUS_CHECK",
        "kill_switch_active": config.is_kill_switch_active,
        "live_unlocked": config.is_live_unlocked,
        "account_equity": account.equity,
        "account_cash": account.cash,
    }
    print(format_report(report))


def cmd_run(_args):
    """Run one daily workflow cycle."""
    wf = DailyWorkflow()
    report = wf.run()
    print(format_report(report))


def cmd_research(_args):
    """Run research cycle only (no trading)."""
    wf = DailyWorkflow()
    result = wf.run_research_cycle()
    print(json.dumps(result, indent=2))


def cmd_kill_on(_args):
    """Activate kill switch."""
    kill_path = config.project_root / "KILL_SWITCH"
    kill_path.touch()
    print("Kill switch activated. Remove file to deactivate.")


def cmd_kill_off(_args):
    """Deactivate kill switch."""
    kill_path = config.project_root / "KILL_SWITCH"
    if kill_path.exists():
        kill_path.unlink()
    print("Kill switch deactivated.")


def main():
    p = argparse.ArgumentParser(prog="hermes-trader", description="Hermes Autonomous Trading System")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Show system status")
    sub.add_parser("run", help="Run daily workflow")
    sub.add_parser("research", help="Run research cycle only")
    sub.add_parser("kill-on", help="Activate kill switch")
    sub.add_parser("kill-off", help="Deactivate kill switch")

    args = p.parse_args()

    if args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "research":
        cmd_research(args)
    elif args.cmd == "kill-on":
        cmd_kill_on(args)
    elif args.cmd == "kill-off":
        cmd_kill_off(args)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()