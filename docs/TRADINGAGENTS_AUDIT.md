# TradingAgents Vendor Audit

## Installation
```bash
cd /opt/vendor/TradingAgents
pip install -e .  # Requires: langchain-core, backtrader, langchain-anthropic, pandas, yfinance, etc.
```

## Entry Points
| Entry Point | Command | Purpose |
|---|---|---|
| `tradingagents` | CLI via typer | Interactive terminal UI |
| `python -c "...from tradingagents.graph.trading_graph import TradingAgentsGraph...; ta.propagate('NVDA', '2024-05-10')"` | Direct API | Multi-agent analysis |

## Key Modules
| Module | Purpose |
|---|---|
| `tradingagents/graph/trading_graph.py` | Main orchestrator, `propagate()` method |
| `tradingagents/graph/propagation.py` | Agent debate propagation |
| `tradingagents/graph/analyst_execution.py` | Analyst node execution |
| `tradingagents/agents/` | Analyst implementations (market, social, news, fundamentals) |
| `tradingagents/dataflows/` | Market data fetching (yfinance, alpha_vantage) |
| `tradingagents/llm_clients/` | LLM provider clients |

## Key API: TradingAgentsGraph
```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG)
result = ta.propagate("NVDA", "2024-05-10")
# Returns: (final_state, decision_string)
```

## Environment Variables (.env.example)
| Variable | Purpose |
|---|---|
| `TRADINGAGENTS_LLM_PROVIDER` | LLM provider (openai/google/anthropic/etc.) |
| `TRADINGAGENTS_DEEP_THINK_LLM` | Deep think model name |
| `TRADINGAGENTS_QUICK_THINK_FLM` | Quick think model name |
| `OPENAI_API_KEY` | OpenAI key |
| `GOOGLE_API_KEY` | Gemini key |
| `ANTHROPIC_API_KEY` | Claude key |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | Debate rounds (default: 1) |

## Integration Plan for Hermes
1. Import TradingAgentsGraph at runtime
2. Call `propagate(symbol, date)` for committee signal
3. Parse decision output for bull/bear/neutral + confidence
4. Use to populate `TradeCandidate.evidence.tradingagents_summary` and `confidence`

## Dependencies
- langchain-core>=0.3.81
- langchain-anthropic>=0.3.15
- langchain-openai>=0.3.23
- langgraph>=0.4.8
- yfinance>=1.4.1
- backtrader>=1.9.78.123
- redis>=6.2.0 (for checkpointing)

## Notes
- Paper: "TradingAgents: Multi-Agent LLM Financial Trading Framework" (arXiv:2412.20138)
- Supports checkpoint/resume via SQLite
- Has its own config system with env-var overrides