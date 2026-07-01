# SECURITY.md

## Secrets Management
- All secrets in `.env` only
- `.env` chmod 600
- `.env` in `.gitignore`
- Never print secrets in logs, Telegram, or any output
- Secret redaction enabled in Hermes config

## API Binding
- All local APIs bind to `127.0.0.1` only
- No public exposure of broker tools
- No public exposure of MCP servers
- API authentication required for non-local endpoints

## Telegram Security
- Restrict allowed Telegram user IDs
- Verify sender identity before processing commands
- Never send secrets via Telegram
- Report redaction before sending

## Third-Party Repos
- Audit ALL third-party repos before installation
- Inspect: README, LICENSE, scripts, Dockerfiles, network calls, credential handling
- Never run `curl|bash` patterns
- Pin versions where practical
- Log dependency versions and repo commit hashes

## File Permissions
```
.env          → 600 (owner read/write only)
logs/         → 700 (owner only)
project root  → owned by Hermes/VPS user
```

## Incident Response
If secret leak is detected:
1. Stop all trading immediately
2. Alert Telegram
3. Recommend key rotation
4. Do not continue live execution

If kill switch is triggered:
1. Stop all new orders
2. Monitor open positions
3. Report status
4. Only close positions to reduce risk

## Credential Handling in Code
- Use `python-dotenv` to load secrets
- Never hardcode credentials
- Never pass raw secrets to LLM context
- Redact secrets from log output using `structlog` processors