# ALPACA_MCP_INTEGRATION.md

## Two Paths

### Path A: Alpaca MCP Server V2
Install `alpacahq/alpaca-mcp-server` and expose tools to Hermes.

**Required audit before use:**
- List all exposed MCP tools
- Verify paper/live mode isolation
- Verify tools cannot bypass mode
- Configure least-privilege toolsets (account read, market data read, trading through wrapper)
- Test dry-run order validation
- Test paper order with tiny amount only after policy engine passes

**Risks:**
- MCP server may expose too many tools
- Tool descriptions may encourage the LLM to trade aggressively
- May not enforce paper/live separation cleanly

### Path B: Direct alpaca-py Wrapper (Preferred for Safety)
Install `alpaca-py` and build local wrapper functions.

**Advantages:**
- Full control over what the agent can call
- Policy engine can be mandatory middleware
- All requests can be logged with secret redaction
- No MCP tool exposure concerns

**Implementation:**
```python
# src/hermes_trader/integrations/alpaca_direct_client.py
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
# ... wrappers for get_account, get_positions, get_orders, submit_order, etc.
# Every submit_order MUST pass through policy engine first
```

## Setup
1. Create Alpaca account at https://alpaca.markets
2. Generate paper API keys first
3. Set in .env: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER=true

## Least-Privilege Tool Access
```
Allowed for agent:  account_read, positions_read, market_data_read, order_read
Allowed only through controlled wrapper:  order_submit, order_cancel
Never exposed to agent:  credential_management, account_settings
```