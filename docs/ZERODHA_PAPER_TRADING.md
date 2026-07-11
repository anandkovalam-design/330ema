# Zerodha Paper-Trading Sync

Status: Draft v0.1

This project supports Zerodha-shaped paper trading only. It does not place live broker orders.

## Why Paper First

Paper trading lets the research system produce order payloads and ledgers without touching live capital. Live execution should only be added after validation, audit logging, risk limits, and manual kill switches exist.

## Zerodha Kite Connect Notes

Zerodha Kite Connect uses an API key, login URL, request token, and access token flow. The official Python client exposes `KiteConnect`, `login_url()`, `generate_session()`, `set_access_token()`, historical data, market data, and order methods.

Environment variables expected by this project:

```powershell
$env:ZERODHA_API_KEY="your_api_key"
$env:ZERODHA_ACCESS_TOKEN="your_access_token"
```

## Run EXP-001 On Real CSV Data

Your CSV must match `research/data_schema/intraday_ohlcv_schema.md`.

```powershell
py -m aadithya_quantlab.experiments.run_exp001 `
  --input data/raw/nifty_intraday.csv `
  --output validation/reports/EXP-001_real_nifty_report.md `
  --labeled-output validation/reports/EXP-001_real_nifty_labeled.csv
```

## Record A Paper Order

This writes a CSV row only. It does not call Zerodha order placement.

```powershell
py -m aadithya_quantlab.trading.run_paper_order `
  --symbol NIFTY26JUL24000CE `
  --side BUY `
  --qty 75 `
  --ledger paper_trades/paper_orders.csv
```

## Live Trading Guardrail

No module in this repository places live orders. When live execution is eventually added, it should require:

- explicit `LIVE_TRADING_ENABLED=true`
- maximum quantity checks
- maximum daily loss checks
- instrument allowlist
- manual confirmation mode
- append-only audit log
- dry-run test parity

