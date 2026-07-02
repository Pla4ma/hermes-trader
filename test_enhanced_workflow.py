#!/usr/bin/env python3
"""
Test script for enhanced workflow with aggressive settings.
"""
import os
import sys
sys.path.insert(0, '/opt/hermes-trader/src')

from hermes_trader.workflow.enhanced_daily_workflow import EnhancedDailyWorkflow

def test_enhanced_workflow():
    wf = EnhancedDailyWorkflow()
    
    # Mock research result (as would come from Vibe-Trading + TradingAgents)
    research = {
        "underlying": "SPY",
        "symbol": "SPY",
        "asset_class": "equity",
        "strategy": "fractional_etf",
        "direction": "bullish",
        "action": "open",
        "order_side": "buy",
        "order_type": "limit",
        "order_qty": 0.01,
        "order_notional": 5.50,
        "limit_price": 550.0,
        "max_loss": 0.50,
        "expected_loss": 0.30,
        "max_profit": 2.00,
        "risk_reward": 4.0,
        "notional": 5.50,
        "data_timestamp": "2026-07-01T14:00:00Z",
        "vibe_summary": "Vibe analysis suggests bullish momentum. SPY in uptrend.",
        "agents_summary": "TradingAgents committee votes 4-1 bullish.",
        "bull_case": "Macro tailwinds, strong earnings",
        "bear_case": "Overbought RSI, profit-taking risk",
        "risk_case": "Controlled risk with limit order",
        "exit_profit_take": "Take profit at +3%",
        "exit_stop_loss": "Stop loss at -1%",
        "exit_time": "Close by Friday EOD",
        "confidence_score": 75,
        "confidence_label": "medium",
        "confidence_reason": "Good setup but mixed signals",
        "limitations": ["Small sample"],
        "backtest_summary": "Backtest returns 12% win rate",
        "transaction_cost_assumption": "$0.01/share",
        "slippage_assumption": "1bps",
        "known_limitations": ["Low volume on options", "Earnings next week"]
    }
    
    report = wf.run(research_result=research)
    
    print("=== Enhanced Workflow Test Report ===")
    print(f"Run ID: {report['run_id']}")
    print(f"Timestamp: {report['timestamp']}")
    print(f"Policy Status: {report['policy_status']}")
    print(f"Policy Reasons: {report['policy_reasons']}")
    print(f"Allowed Action: {report['allowed_action']}")
    print(f"Actions Taken: {len(report['actions_taken'])}")
    for i, action in enumerate(report['actions_taken']):
        print(f"  Action {i+1}: {action}")
    print(f"Momentum Scan: {report.get('momentum_scan', 'N/A')}")
    print(f"Backtest Result: {report.get('backtest_result', 'N/A')}")
    print(f"Candidate: {report.get('candidate', {}).get('symbol') if report.get('candidate') else 'None'}")
    if report.get('score'):
        print(f"Score: {report['score']['total']} (tier: {report['score']['tier']})")
    
    return report

if __name__ == "__main__":
    test_enhanced_workflow()