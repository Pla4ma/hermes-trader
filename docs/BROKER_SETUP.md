# BROKER_SETUP.md

## Alpaca Integration (Phase 1)

Two possible paths:

### Path A: Alpaca MCP Server V2
- Repository: `alpacahq/alpaca-mcp-server`
- Provides MCP tools for account, market data, and trading
- Must be audited for tool exposure and safety
- Must restrict tools to least privilege (account read, market data read, order placement through controlled wrapper)

### Path B: Direct alpaca-py SDK
- Package: `alpaca-py`
- Provides TradingClient, BrokerClient, etc.
- Use behind local wrapper functions
- All orders pass through policy engine first

## Setup

1. Get Alpaca API keys (paper first):
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`

2. Paper trading URL: `https://paper-api.alpaca.markets`
3. Live trading URL: `https://api.alpaca.markets`

4. Set in `.env`:
   ```
   ALPACA_API_KEY=your_paper_key
   ALPACA_SECRET_KEY=your_paper_secret
   ALPACA_PAPER=true
   ALPACA_BASE_URL=https://paper-api.alpaca.markets
   ```

## Order Types

### Paper Mode (Default)
- All order types allowed for workflow validation
- Must still pass policy engine

### Live Mode (Requires Global Unlock)
- Limit orders preferred
- Market orders only for fractional ETFs with tiny spread in regular hours
- No option market orders ever

## Safety

- Credentials ONLY in `.env` (chmod 600)
- Never print API keys in logs or Telegram
- Redact all secrets from audit logs
- Policy engine validates EVERY order before submission
- Kill switch prevents ALL orders when active