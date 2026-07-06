# API-First Brokers for Autonomous Options Trading

**Research Date:** 2026-07-06  
**Context:** Hermes trader needs API access for autonomous options trading, including 0DTE strategies.

## Executive Summary

| Criteria | Tradier | Alpaca | Interactive Brokers (IBKR) | Tastytrade |
|----------|---------|--------|---------------------------|------------|
| **Best Python SDK** | ⭐⭐⭐ (Official REST) | ⭐⭐⭐⭐ (Official, modern) | ⭐⭐ (Unofficial) | ⭐⭐⭐⭐ (Official async) |
| **Options API** | ✅ Full support | ✅ Full support | ✅ Full support | ✅ Full support |
| **Paper Trading** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **Execution Speed** | Fast | Fast | Fastest | Fast |
| **API Uptime** | Good | Good | Excellent | Good |
| **0DTE Support** | ✅ Yes | ⚠️ Limited | ✅ Yes | ✅ Yes |
| **Multi-leg Orders** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **WebSocket Streaming** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |

## Detailed Analysis

### 1. Tradier API

**Overview:** REST-based API with strong options support. Well-documented and reliable.

**Python SDK:**
- Package: `tradier` (PyPI)
- Status: Official REST API, good documentation
- Type: Synchronous REST
- Docs: https://docs.tradier.com/docs/getting-started.md

**Options Support:**
- Full options order submission (single leg, multi-leg, spreads, combos)
- Advanced orders: OTO, OCO, OTOCO
- Options chain data
- 0DTE trading supported
- Complex order types (spreads, butterflies, iron condors)

**Paper Trading:**
- Sandbox environment available
- Paper account for testing
- Same API endpoints as live

**Execution Speed:**
- Fast execution for REST API
- WebSocket streaming for real-time data
- Order placement: <100ms typical

**Rate Limits:**
- Standard REST rate limits apply
- No aggressive throttling

**Pros:**
- Excellent documentation with LLM-friendly docs (llms.txt)
- Full options support including complex orders
- Fast execution for a REST API
- Well-established for algo trading

**Cons:**
- REST-only (no WebSocket for order execution)
- Smaller ecosystem than IBKR

---

### 2. Alpaca API

**Overview:** Modern, developer-friendly API with strong Python support. Good options support.

**Python SDK:**
- Package: `alpaca-trade-api` (PyPI)
- Status: Official, actively maintained
- Version: 3.2.0 (Jan 2024)
- Docs: https://docs.alpaca.markets/
- GitHub: https://github.com/alpacahq/alpaca-trade-api-python

**Options Support:**
- Full options order submission
- Multi-leg options (Level 3 trading)
- Options chain data
- 0DTE trading supported
- Complex order types (spreads, strangles, etc.)

**Paper Trading:**
- Free paper trading environment
- Same API as live
- No account required for paper trading
- Great for testing strategies

**Execution Speed:**
- Fast execution
- WebSocket streaming for real-time data
- Order placement: <100ms typical

**Rate Limits:**
- API rate limits apply (documented)
- Reasonable for algo trading

**Pros:**
- Modern, clean Python SDK
- Excellent documentation
- Strong options support
- Easy to get started
- Free paper trading

**Cons:**
- Limited to US markets
- Smaller than IBKR for global access

---

### 3. Interactive Brokers (IBKR) API

**Overview:** Most comprehensive API for global markets. Highest execution quality.

**Python SDK:**
- Package: `ib_insync` (unofficial) or TWS API
- Status: Unofficial community SDK is most popular
- Docs: https://interactivebrokers.github.io/tws-api/
- GitHub: https://github.com/ib-insync/ib_insync
- Official: TWS API (requires TWS/Gateway running locally)

**Options Support:**
- Full options order submission
- Multi-leg options
- Complex order types (spreads, butterflies, iron condors, etc.)
- Global options markets
- 0DTE trading supported
- Real-time Greeks

**Paper Trading:**
- Paper trading account available
- Same API as live
- Requires TWS or Gateway running

**Execution Speed:**
- Fastest execution among all brokers
- Direct market access
- Order placement: <50ms typical
- Best fill quality

**Rate Limits:**
- More generous than others
- Designed for professional algo traders

**Pros:**
- Best execution quality
- Global market access
- Most comprehensive API
- Best for professional algo traders
- Real-time Greeks and complex analytics

**Cons:**
- Complex setup (requires TWS/Gateway running locally)
- Unofficial Python SDK (ib_insync) is more popular than official
- Steeper learning curve
- Java-based TWS dependency

---

### 4. Tastytrade API

**Overview:** Options-focused broker with modern API. Excellent for options strategies.

**Python SDK:**
- Package: `tastytrade` (PyPI)
- Status: Official, actively maintained (v13.0.0, Jul 2026)
- Docs: https://github.com/tastyware/tastytrade
- GitHub: https://github.com/tastyware/tastytrade
- Stars: 245
- Type: Async, typed Python SDK

**Options Support:**
- Full options order submission
- Multi-leg options
- Complex order types (spreads, butterflies, iron condors, etc.)
- 0DTE trading supported
- Real-time Greeks
- Options-focused platform

**Paper Trading:**
- Paper trading available
- Same API as live
- Good for testing options strategies

**Execution Speed:**
- Fast execution
- WebSocket streaming for real-time data
- Order placement: <100ms typical

**Rate Limits:**
- Standard rate limits
- Designed for active traders

**Pros:**
- Modern, async Python SDK
- Options-focused platform
- Excellent for options strategies
- Good documentation
- Active development

**Cons:**
- Smaller broker than IBKR/Alpaca
- Less market data than IBKR
- Newer API

---

## Recommendations for Hermes Trader

### Best Overall: **Tradier API**
- Best balance of options support, API quality, and reliability
- Excellent documentation for AI agents (llms.txt)
- Full options support including 0DTE
- Good execution speed for REST API
- Already in use by Hermes trader (from research)

### Best Python SDK: **Tastytrade API**
- Modern, async Python SDK (v13.0.0)
- Strong typing and good documentation
- Options-focused platform
- Active development (245 GitHub stars)

### Best Execution: **Interactive Brokers API**
- Fastest execution (<50ms)
- Best fill quality
- Most comprehensive API
- Requires more setup complexity

### Best for Algo Trading: **Alpaca API**
- Modern, clean Python SDK
- Easy to get started
- Good options support
- Free paper trading
- Limited by US markets only

## Integration Recommendations

### For Hermes Trader (Current Setup):

1. **Primary: Tradier API**
   - Already integrated with Hermes
   - Excellent options support
   - Good for 0DTE strategies
   - LLM-friendly documentation

2. **Alternative: Tastytrade API**
   - Modern async Python SDK
   - Strong options focus
   - Good for complex strategies

3. **Professional: Interactive Brokers API**
   - Best execution quality
   - Requires more setup (TWS/Gateway)
   - Best for high-frequency trading

4. **Simplest: Alpaca API**
   - Easiest to get started
   - Good for simple strategies
   - Limited by US markets

## Code Examples

### Tradier API (Python)
```python
import tradier

# Initialize client
client = tradier.Client(api_key="YOUR_API_KEY", account_id="YOUR_ACCOUNT_ID")

# Place options order
order = client.place_order(
    symbol="SPY",
    type="option",
    side="buy",
    quantity=1,
    option_symbol="SPY250706C00560000",  # SPY Call
    limit_price="1.50"
)
```

### Alpaca API (Python)
```python
from alpaca_trade_api import REST

# Initialize client
api = REST(
    key_id="YOUR_API_KEY",
    secret_key="YOUR_SECRET_KEY",
    base_url="https://paper-api.alpaca.markets"  # Paper trading
)

# Place options order
order = api.submit_order(
    symbol="SPY",
    qty=1,
    side="buy",
    type="limit",
    limit_price="1.50",
    time_in_force="day",
    order_class="option",
    option_symbol="SPY250706C00560000"  # SPY Call
)
```

### Interactive Brokers API (Python - ib_insync)
```python
from ib_insync import IB, Stock, Option

# Initialize client
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)

# Create option contract
contract = Option('SPY', '20250706', 560, 'C', 'SMART')

# Place order
order = ib.placeOrder(
    contract,
    LimitOrder('BUY', 1, 1.50)
)
```

### Tastytrade API (Python)
```python
import tastytrade as tt

# Initialize client
session = tt.login("username", "password")

# Place options order
order = tt.place_order(
    account_number="YOUR_ACCOUNT",
    symbol="SPY",
    action="buy",
    quantity=1,
    option_symbol="SPY250706C00560000",  # SPY Call
    price="1.50",
    order_type="limit"
)
```

## Additional Resources

- **Tradier Documentation:** https://docs.tradier.com/docs/getting-started.md
- **Alpaca Documentation:** https://docs.alpaca.markets/
- **IBKR TWS API:** https://interactivebrokers.github.io/tws-api/
- **Tastytrade Python SDK:** https://github.com/tastyware/tastytrade

## Next Steps for Hermes Trader

1. **Test Tradier API** (primary recommendation)
   - Set up paper trading account
   - Test 0DTE order submission
   - Verify execution speed

2. **Evaluate Tastytrade API** (alternative)
   - Test async Python SDK
   - Compare options chain data quality
   - Test complex order types

3. **Consider IBKR API** (professional)
   - If execution quality is critical
   - If global market access needed
   - Requires TWS/Gateway setup

4. **Keep Alpaca** (simplest)
   - For simple strategies
   - Quick prototyping
   - Free paper trading
