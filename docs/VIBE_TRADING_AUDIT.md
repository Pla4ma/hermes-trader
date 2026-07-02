# Vibe-Trading Vendor Audit

## Installation
```bash
cd /opt/vendor/Vibe-Trading/agent
pip install -e .  # Requires: rich, pyyaml, langchain, langchain-core, httpx, pandas, etc.
```

## Entry Points
| Entry Point | Command | Purpose |
|---|---|---|
| `vibe-trading` | `python -m cli` | Main CLI entry point |
| `vibe-trading-mcp` | `python -m mcp_server` | MCP server mode |
| `vibe-trading research --symbol <ticker>` | CLI | Market regime analysis |
| `vibe-trading backtest --symbol <ticker>` | CLI | Backtesting |

## Key Modules
| Module | Purpose |
|---|---|
| `agent/src/market_data.py` | Price/action data fetching |
| `agent/src/preflight.py` | Sanity checks before trading |
| `agent/backtest/` | Backtesting engine |
| `agent/cli/main.py` | CLI with slash commands |
| `agent/mcp_server.py` | MCP server implementation |

## Environment Variables (.env.example)
| Variable | Purpose |
|---|---|
| `LANGCHAIN_PROVIDER` | LLM provider (openrouter/openai/deepseek) |
| `LANGCHAIN_MODEL_NAME` | Model name |
| `OPENROUTER_API_KEY` | Multi-model gateway key |

## Integration Plan for Hermes
1. Call `vibe-trading research --symbol SPY` via subprocess
2. Parse stdout for market summary, regime, technical signals
3. Use output to build `TradeCandidate.evidence.vibe_summary`

## Dependencies
- langchain>=1.0.0,<2
- langchain-openai>=1.0.0,<2
- langgraph>=1.0.10,<1.1
- yfinance>=0.2.30
- pandas>=2.0.0
- duckdb>=1.2.0
- scikit-learn>=1.3.0

## Notes
- Python package name: `vibe-trading-ai`
- MCP support via `fastmcp` library
- Has its own broker integrations (Alpaca, IBKR, etc.)