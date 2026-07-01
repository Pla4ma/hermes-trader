# VIBE_TRADING_INTEGRATION.md

## Role
Vibe-Trading is the primary **research and backtesting workbench** for Phase 1.

## Responsibilities
- Research market context (regime, volatility, trends)
- Analyze SPY, QQQ, VOO
- Analyze options chains when relevant
- Run backtests (historical sanity checks) when possible
- Run factor analysis when useful
- Generate strategy candidate reports
- Produce structured research notes
- Produce no-trade recommendations when conditions are bad
- Create evidence packs suitable for the deterministic policy engine

## Integration Method
Hermes calls Vibe-Trading via:
1. Direct CLI invocation if Vibe-Trading supports command-line mode
2. MCP server if Vibe-Trading exposes MCP tools
3. Python SDK/subprocess if available

## Required Output Fields
Every Vibe-Trading research output must include:
```
timestamp
data_source
market_regime
symbols_analyzed
strategy_candidates
backtest_availability
backtest_result_summary
transaction_cost_assumption
slippage_assumption
sample_size
drawdown
win_rate (if available)
expectancy (if available)
limitations
reasons_to_trade
reasons_not_to_trade
confidence_level
final_recommendation
```

## Prompt Template for First Research Cycle
```
Analyze SPY, QQQ, and VOO for a $20 autonomous trading experiment. 
The account is extremely small. The goal is not aggressive profit but 
risk-bounded autonomous execution testing. Evaluate whether fractional 
ETF exposure, a tiny long call, a tiny long put, or no-trade is most 
appropriate today. Include market regime, volatility context, liquidity, 
transaction cost concerns, whether options are appropriate for such a 
small account, and whether no-trade is better. Provide a structured 
evidence pack suitable for a deterministic risk policy engine. Do not 
assume live order execution. Do not fabricate unavailable data.
```

## Safety
- Vibe-Trading output is advisory only
- Cannot place orders directly
- Must be converted to TradeCandidate JSON before policy evaluation
- If Vibe-Trading cannot provide reliable evidence, confidence must be reduced