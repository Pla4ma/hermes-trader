# TRADINGAGENTS_INTEGRATION.md

## Role
TradingAgents is the **multi-agent debate/committee layer** for Phase 1.

## Responsibilities
- Independent market analysis (multiple agent perspectives)
- Bull thesis development
- Bear thesis development
- Technical analysis
- Sentiment analysis
- News analysis
- Risk manager critique
- Portfolio manager final recommendation
- Structured decision output
- Persistent decision log (if supported)

## Integration Method
Hermes calls TradingAgents via:
1. Direct CLI invocation if TradingAgents supports command-line mode
2. Python SDK/subprocess if available
3. API server if TradingAgents exposes HTTP endpoints

## Required Output Fields
Every TradingAgents committee output must include:
```
timestamp
ticker
proposed_action
bullish_arguments
bearish_arguments
risk_manager_objections
portfolio_manager_decision
confidence_level
key_uncertainty
invalidation_condition
recommended_position_size
whether_to_trade_or_no_trade
suitability_for_20_dollar_tiny_account
```

## Prompt Template for First Committee Cycle
```
You are a multi-agent trading committee reviewing a candidate trade 
for a $20 autonomous trading experiment. The approved underlyings are 
SPY, QQQ, and VOO. The system can trade fractional ETFs and, only under 
strict conditions, tiny long options. No naked options, no short options, 
no 0DTE, no margin, no more than one open position, max expected loss 
$1.50. Debate whether the proposed trade is better than no-trade. 
Provide bullish thesis, bearish thesis, risk manager objections, 
portfolio manager decision, confidence, invalidation conditions, and 
suitability for a tiny $20 account.
```

## Safety
- TradingAgents output is advisory only
- Cannot place orders directly
- Must route through TradeCandidate JSON → Policy Engine
- If committee disagrees strongly, that reduces confidence score
- No trade is a valid committee recommendation