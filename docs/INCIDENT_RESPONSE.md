# INCIDENT_RESPONSE.md

## Severity Levels

### CRITICAL — Stop All Trading Immediately
- Secret leak detected
- Unexpected live mode activation
- Kill switch bypass detected
- Duplicate orders submitted
- Position mismatch between broker and journal
- Account equity outside mandate
- Failed kill switch check

**Response:**
1. Activate kill switch if not already active
2. Send Telegram CRITICAL alert immediately
3. Cancel all open orders
4. Close positions only if reducing risk
5. Do not resume until root cause is identified

### HIGH — Pause New Orders, Continue Monitoring
- Daily loss cap hit
- Weekly loss cap hit
- Consecutive loss cap hit
- Option near expiration danger window
- Market data source failure during live mode
- Broker API returning errors
- Failed exit order on open position

**Response:**
1. Stop opening new positions
2. Send Telegram alert
3. Monitor existing positions closely
4. Prepare exit if policy requires
5. Create postmortem report

### MEDIUM — Log and Report
- Vibe-Trading failure (retried, failed again)
- TradingAgents failure (retried, failed again)
- Stale quotes
- Order timeout without fill
- Telegram reporting failure
- Lock file collision

**Response:**
1. Log error with full context
2. Fall back to no-trade or research-only
3. Send Telegram if affects main cycle
4. Continue paper mode only if safe

### LOW — Note and Continue
- Minor data quality issues
- Backtest not available for candidate
- Committee mild disagreement
- Non-critical tool warnings

**Response:**
1. Log note
2. Continue with reduced confidence
3. No Telegram alert needed

## Emergency Contact
User must be notified via Telegram for any CRITICAL or HIGH incident.
The agent must report honestly and not downplay severity.