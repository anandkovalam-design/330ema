# Aadithya QuantLab

Aadithya QuantLab is a structured R&D repository for intraday index research, beginning with NIFTY market-state taxonomy and expanding into feature engineering, experiment design, validation, and implementation planning.

## Repository Map

- `research/papers/` - version-controlled research papers and appendices.
- `research/mathematical_specifications/` - formal definitions, notation, and derivations.
- `research/knowledge_base/` - reusable notes, assumptions, references, and decisions.
- `research/data_schema/` - canonical data contracts and dataset documentation.
- `research/architecture/` - system design notes for research and production integration.
- `research/implementation_roadmap/` - staged delivery plans and acceptance criteria.
- `features/library/` - feature specifications, lineage, and implementation status.
- `experiments/library/` - experiment protocols, run manifests, and outcomes.
- `src/aadithya_quantlab/` - Python research modules.
- `validation/` - validation framework, reports, and evidence packs.
- `tests/` - automated tests for research code and validation utilities.

## Research Program

The project treats each paper as an executable research unit:

1. Define a market problem and formal vocabulary.
2. Convert concepts into mathematical specifications.
3. Register derived features in the feature library.
4. Register experiments with datasets, metrics, and falsification tests.
5. Promote validated outputs into the architecture and implementation roadmap.

Paper 001 starts the program with a taxonomy for intraday NIFTY market states.

## Quick Commands

Launch the green Zerodha live-trading control dashboard:

```powershell
$env:PYTHONPATH="src"
streamlit run src/aadithya_quantlab/zerodha_live_trading/app.py
```

This dashboard is isolated in `aadithya_quantlab.zerodha_live_trading`. It
starts in PAPER mode; REAL orders require selecting REAL and separately arming
the live-order confirmation.

Run daily Zerodha login setup once per trading day:

```powershell
py -m aadithya_quantlab.trading.zerodha_daily_login
```

Run EXP-001 on a canonical intraday CSV:

```powershell
py -m aadithya_quantlab.experiments.run_exp001 --input data/raw/nifty_intraday.csv --output validation/reports/EXP-001_real_nifty_report.md --labeled-output validation/reports/EXP-001_real_nifty_labeled.csv
```

Record a Zerodha-shaped paper order:

```powershell
py -m aadithya_quantlab.trading.run_paper_order --symbol NIFTY26JUL24000CE --side BUY --qty 65
```

Paper-order commands write to a local ledger only; they do not place live broker orders. NIFTY paper orders are currently locked to quantity `65`.

## Groww Cloud Trading Engine (Safe Default)

This repository now includes a separate non-Streamlit trading engine for cloud
execution:

- `groww_cloud_main.py` - cloud entrypoint for scheduled runs.
- `src/groww_engine/config.py` - central mode/risk/cost configuration.
- `src/groww_engine/engine.py` - orchestration loop and safety checks.
- `src/groww_engine/paper_broker.py` - persistent simulated execution only.
- `src/groww_engine/live_broker.py` - isolated real-order API surface.
- `src/groww_engine/groww_client.py` - market-data adapter.

The Zerodha Streamlit dashboard remains separate and does not need to stay open
for the engine to run.

### 1. Install dependencies

```powershell
py -m pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and set values. Keep real credentials only in
environment variables or Groww Cloud secrets:

- `GROWW_API_KEY`
- `GROWW_API_SECRET`
- `GROWW_ACCESS_TOKEN`

Do not commit credentials, tokens, or account secrets to Git.

### 3. Run locally in PAPER mode (default)

```powershell
set TRADING_MODE=PAPER
set ENABLE_LIVE_ORDERS=false
set I_UNDERSTAND_LIVE_ORDER_RISK=NO
py groww_cloud_main.py --max-cycles 3
```

### 4. Verify no real orders are sent

- In PAPER mode, the engine writes only to local storage:
	- `outputs/groww/paper_trades.db`
	- `outputs/groww/paper_trades.csv`
- No `create_order`, `modify_order`, or `cancel_order` calls are made.
- Run safety tests:

```powershell
py -m pytest tests/test_groww_engine_safety.py -q
```

### 5. Upload trading engine code to Groww Cloud

Upload this repository (or engine subset) with at least:

- `groww_cloud_main.py`
- `src/groww_engine/`
- `src/quantlab/strategy_ema3_30_staged.py`
- `requirements.txt`

### 6. Configure Groww Cloud schedule

Set scheduler frequency to your desired poll cadence (for example every
1-5 minutes), then let `groww_cloud_main.py` run the cycle logic.

### 7. Daily Groww authentication/approval

Groww access tokens expire daily at **06:00 AM**. Refresh token/approval before
market hours and update `GROWW_ACCESS_TOKEN` in secret storage.

### 8. Review logs and paper-trade results

- Runtime logs: `outputs/groww/logs/engine.log`
- Paper trade DB: `outputs/groww/paper_trades.db`
- Paper trade CSV: `outputs/groww/paper_trades.csv`

### 9. Emergency kill switch

Set `EMERGENCY_KILL_SWITCH=true` to halt new entries immediately.

### 10. LIVE mode warning

LIVE mode can cause real financial loss.

LIVE is enabled only if all 3 checks pass simultaneously:

1. `TRADING_MODE=LIVE`
2. `ENABLE_LIVE_ORDERS=true`
3. `I_UNDERSTAND_LIVE_ORDER_RISK=YES`

If any one is missing, the engine automatically falls back to PAPER mode.
