"""Telegram reporter — formats structured reports for delivery via Hermes.

This module formats the daily workflow report into a concise
Telegram-compatible message.
"""

from typing import Optional


EMOJI = {
    "APPROVED": "✅",
    "REJECTED": "🚫",
    "NO_TRADE": "⏸️",
    "PAUSED": "⏸️",
    "KILL_SWITCH_ACTIVE": "🛑",
    "paper_order": "📝",
    "live_order": "🔴",
    "close_order": "🔒",
    "cancel_order": "❌",
    "none": "–",
    "bullish": "📈",
    "bearish": "📉",
    "neutral": "↔️",
}


def format_report(report: dict) -> str:
    """Format a workflow report dict into a Telegram message."""
    mode = report.get("mode", "UNKNOWN")
    status = report.get("policy_status", "UNKNOWN")
    underlying = report.get("underlying", "N/A")
    strategy = report.get("strategy", "N/A")
    action = report.get("action", "N/A")
    direction = report.get("direction", "N/A")
    score_total = report.get("score_total")
    score_tier = report.get("score_tier", "N/A")
    ks = report.get("kill_switch_active", False)
    lu = report.get("live_unlocked", False)
    confidence = report.get("confidence", "N/A")
    reasons = report.get("policy_reasons", [])
    order_result = report.get("order_result")

    e_status = EMOJI.get(status, "❓")
    e_dir = EMOJI.get(direction, "↔️")

    lines = [
        f"**📊 Hermes Trader Daily Report**",
        f"",
        f"**Mode:** `{mode}` | **Status:** {e_status} `{status}`",
        f"**Underlying:** {e_dir} `{underlying}` | **Strategy:** `{strategy}`",
        f"**Action:** `{action}` | **Confidence:** `{confidence}/100`",
    ]

    if score_total is not None:
        lines.append(f"**Score:** `{score_total}/100` ({score_tier})")

    lines.append(f"")
    lines.append(f"**Kill Switch:** {'🛑 ACTIVE' if ks else '✅ Inactive'}")
    lines.append(f"**Live Unlock:** {'🔴 UNLOCKED' if lu else '🔒 Locked (paper only)'}")

    if reasons:
        lines.append(f"")
        lines.append(f"**Policy Reasons:**")
        for r in reasons[:5]:
            lines.append(f"  • {r}")

    if order_result:
        lines.append(f"")
        oid = order_result.get("order_id", "N/A")
        ostatus = order_result.get("status", "N/A")
        omode = order_result.get("mode", "N/A")
        lines.append(f"**Order:** `{oid}` ({ostatus}, {omode})")

    # Account info
    equity = report.get("account_equity")
    cash = report.get("account_cash")
    if equity is not None:
        lines.append(f"")
        lines.append(f"**Account:** `${equity:.2f}` equity | `${cash:.2f}` cash")

    return "\n".join(lines)


def format_kill_switch_alert() -> str:
    return "🛑 **KILL SWITCH ACTIVE** 🛑\n\nAll trading halted. No new orders will be placed.\nMonitoring continues. Remove KILL_SWITCH file to resume."


def format_live_unlock_confirmation() -> str:
    return "🔴 **LIVE TRADING UNLOCKED** 🔴\n\nAll 4 conditions met:\n• ROBINHOOD_MCP=true\n• ENABLE_LIVE_TRADING=true\n• LIVE_AUTONOMY_MODE=TINY_LIVE_AUTONOMOUS\n• Confirmation phrase set\n\n⚠️ This $20 experiment CAN lose money."


def format_error_alert(error: str) -> str:
    return f"⚠️ **Hermes Trader Error**\n\n```\n{error[:500]}\n```"
