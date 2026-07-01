# RUNBOOK.md — Hermes Autonomous Trading System

## Daily Operations

### Check System Status
```bash
cd /opt/hermes-trader && make healthcheck
```

### Manual Research Cycle
```bash
cd /opt/hermes-trader && make research
```

### Manual Paper Trading Cycle
```bash
cd /opt/hermes-trader && make paper-cycle
```

### End-of-Day Report
```bash
cd /opt/hermes-trader && make eod-report
```

### Run Test Suite
```bash
cd /opt/hermes-trader && make test
```

## Cron Schedule (America/New_York)

| Time (ET) | Action | Script |
|-----------|--------|--------|
| 9:20 AM | Premarket healthcheck | `run_healthcheck.sh` |
| 9:45 AM | Market context research | `run_research_cycle.sh` |
| 10:15 AM | Candidate generation | (part of research cycle) |
| 10:30 AM | Committee debate | (part of research cycle) |
| 10:45 AM | Policy evaluation | (part of research cycle) |
| 10:50 AM | Execution | `run_paper_cycle.sh` |
| 12:30 PM | Midday check | (part of healthcheck) |
| 3:15 PM | Risk/exit check | (part of monitoring) |
| 4:20 PM | End-of-day report | `run_eod_report.sh` |

## Monitoring

### View Audit Logs
```bash
ls -la /opt/hermes-trader/logs/audit/
cat /opt/hermes-trader/logs/decisions/*.jsonl | tail -20
```

### View Trade Journal
```bash
cat /opt/hermes-trader/data/journals/trade_journal.jsonl | tail -20
```

### View No-Trade Journal
```bash
cat /opt/hermes-trader/data/journals/no_trade_journal.jsonl | tail -20
```

### Check Open Positions (via Alpaca)
```bash
# After installing alpaca-py
python -c "
from hermes_trader.integrations.alpaca_direct_client import AlpacaClient
client = AlpacaClient()
print(client.get_positions())
"
```

## Kill Switch

### Activate (User Only)
```bash
touch /opt/hermes-trader/KILL_SWITCH
```

### Deactivate (User Only)
```bash
rm /opt/hermes-trader/KILL_SWITCH
```

### Status Check
```bash
test -f /opt/hermes-trader/KILL_SWITCH && echo "KILL SWITCH ACTIVE" || echo "KILL SWITCH INACTIVE"
```

## Error Recovery

### Stale Lock File
```bash
# If RUNNING.lock is stuck, check if process is actually running first
ps aux | grep hermes-trader
# Only remove if no process is actually running
rm /opt/hermes-trader/RUNNING.lock
```

### Failed Vibe-Trading
1. Check /opt/Vibe-Trading is installed
2. Check logs for error details
3. Retry once, then fall back to research-only or no-trade

### Failed TradingAgents
1. Check /opt/TradingAgents is installed
2. Check logs for error details
3. Retry once, then reject trade (no trade without debate layer)

### Failed Alpaca Connection
1. Check network connectivity
2. Check API credentials
3. Do not trade until connection restored

## Mode Changes

### Switch to Research Only
```bash
export TRADER_MODE=RESEARCH_ONLY
# Restart Hermes or next cycle picks up new mode
```

### Switch to Paper (Default)
```bash
export TRADER_MODE=PAPER_AUTONOMOUS
```

### Pause Trading
```bash
export TRADER_MODE=PAUSED
```