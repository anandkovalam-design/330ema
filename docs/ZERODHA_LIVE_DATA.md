# Zerodha Kite Connect Live-Data Setup (Paper-Trading Safe)

This setup is intentionally live-data only. Live order placement is disabled in code.

## App Mode (Recommended for Testing)

Run the interactive app:

```powershell
py -m streamlit run src/aadithya_quantlab/trading/zerodha_live_data_app.py
```

In the app, run steps in order:

1. Optional: If you already have today's access token, use Step 0 to apply existing token
2. Generate login URL (Step 1)
3. Recommended: Generate today's access token using request token + API secret (Step 2)
4. Check connection and fetch live NIFTY LTP (Step 3)
5. Fetch historical candles and run EXP-001 report (Step 4)

All app actions are live-data only and do not place orders.

## 1) Set credentials in PowerShell

```powershell
$env:ZERODHA_API_KEY = "<your_api_key>"
$env:ZERODHA_API_SECRET = "<your_api_secret>"
```

## Daily Morning Login

Use this once at the start of each trading day. It prompts for anything not already set in PowerShell, prints the login URL, exchanges the fresh request token, and saves the day access token locally.

```powershell
py -m aadithya_quantlab.trading.zerodha_daily_login
```

Typical daily flow:

1. Run the command above.
2. Paste `ZERODHA_API_KEY` if prompted.
3. Open the printed Zerodha login URL.
4. Copy only the `request_token` from the redirected browser URL.
5. Paste the request token.
6. Paste `ZERODHA_API_SECRET` when prompted. It will not be echoed.
7. The command saves `outputs/zerodha/access_token.txt`, which is ignored by Git.

After this, no more login/token setup is needed for the same day unless Zerodha expires the session.

Then run:

```powershell
py -m aadithya_quantlab.trading.zerodha_check_connection
py -m aadithya_quantlab.trading.zerodha_live_data_workflow --from "2026-07-09T09:15:00+05:30" --to "2026-07-09T15:30:00+05:30"
```

## 2) Print login URL

```powershell
py -m aadithya_quantlab.trading.zerodha_login_url
```

Open the URL, complete login, and copy the request token from the redirect URL.

## 3) Set request token and generate access token

```powershell
$env:ZERODHA_REQUEST_TOKEN = "<request_token_from_redirect>"
py -m aadithya_quantlab.trading.zerodha_generate_access_token --token-output outputs/zerodha/access_token.txt
```

The command writes the full token to the file and prints only a masked token preview.

## 4) Load access token from file into environment

```powershell
$env:ZERODHA_ACCESS_TOKEN = (Get-Content outputs/zerodha/access_token.txt -Raw).Trim()
```

Note: Commands now load token safely from `ZERODHA_ACCESS_TOKEN` first, and fall back to `outputs/zerodha/access_token.txt` automatically when the env var is absent.

## 5) Verify connection with kite.profile()

```powershell
py -m aadithya_quantlab.trading.zerodha_check_connection
```

## 6) Fetch live quote/LTP for NIFTY (or configured instrument)

LTP mode:

```powershell
py -m aadithya_quantlab.trading.zerodha_fetch_live_data --instrument "NSE:NIFTY 50" --mode ltp
```

Quote mode:

```powershell
py -m aadithya_quantlab.trading.zerodha_fetch_live_data --instrument "NSE:NIFTY 50" --mode quote
```

## 7) Fetch historical intraday candles and save canonical CSV for EXP-001

```powershell
py -m aadithya_quantlab.trading.zerodha_fetch_historical --instrument-token 256265 --symbol NIFTY --interval minute --from "2026-07-10T09:15:00+05:30" --to "2026-07-10T15:30:00+05:30" --output outputs/exp001/nifty_intraday_ohlcv.csv
```

If no candles are returned, the command exits cleanly with a friendly suggestion for a recent weekday market session.

The CSV columns are written in canonical schema order:

- symbol
- timestamp
- session_date
- open
- high
- low
- close
- volume
- source
- adjustment_flag

## Safety Guarantees

- Credentials are read from environment variables, with access-token fallback to `outputs/zerodha/access_token.txt`.
- API secret and full access token are never printed by CLI commands.
- Live order placement is not available; `place_order` is blocked.
- Shadow paper trading uses live LTP only and records virtual fills/exits. It does not require broker order execution.

## Shadow Paper Trade Test

Use this when you want to test NIFTY option trade management for a few days without placing real orders.

1. Complete the daily morning login.
2. Choose the live NIFTY option contract from Kite, for example `NFO:NIFTY26JUL24200CE`.
3. Run the shadow tester:

```powershell
py -m aadithya_quantlab.trading.zerodha_shadow_paper --instrument "NFO:NIFTY26JUL24200CE" --side CALL --quantity 65 --poll-seconds 30
```

The first tick opens a virtual paper position at live LTP. The runner then watches the same instrument and applies:

- 10% premium stop loss
- target of 25 premium points
- trailing stop after 10 premium points profit
- quantity 65 by default

If the terminal closes, the open paper position is saved in `outputs/zerodha/shadow_paper_position.json`. Running the same command again resumes it. Closed trades are written to `outputs/zerodha/shadow_paper_trades.csv`.

### Automatic ATM Start After Broker Login

Once the daily Zerodha login is complete, this command waits until 09:15 IST if needed, auto-selects the nearest-expiry ATM NIFTY call option, and starts shadow paper tracking:

```powershell
py -m aadithya_quantlab.trading.zerodha_shadow_paper --side CALL --auto-nifty-atm --wait-for-market-open --quantity 65 --poll-seconds 30 --reset
```

For ATM put:

```powershell
py -m aadithya_quantlab.trading.zerodha_shadow_paper --side PUT --auto-nifty-atm --wait-for-market-open --quantity 65 --poll-seconds 30 --reset
```

For a calmer first test, wait until 09:20 IST before entry:

```powershell
py -m aadithya_quantlab.trading.zerodha_shadow_paper --side CALL --auto-nifty-atm --wait-for-market-open --wait-after-open-minutes 5 --quantity 65 --poll-seconds 30 --reset
```

To let the previous strategy logic decide CALL or PUT automatically, use `--side AUTO`:

```powershell
py -m aadithya_quantlab.trading.zerodha_shadow_paper --side AUTO --auto-nifty-atm --wait-for-market-open --wait-after-open-minutes 5 --quantity 65 --poll-seconds 30 --signal-poll-seconds 60 --reset
```

For a full day-style paper test that keeps looking for new opportunities after a trade closes:

```powershell
py -m aadithya_quantlab.trading.zerodha_shadow_paper --side AUTO --auto-nifty-atm --wait-for-market-open --wait-after-open-minutes 5 --quantity 65 --poll-seconds 30 --signal-poll-seconds 60 --max-trades 3 --reentry-wait-seconds 300 --stop-new-entries-at 15:00 --reset
```

This closes each paper trade by stop/target/trailing rules, waits 5 minutes, then searches for the next AUTO signal until either 3 trades are closed or 15:00 IST is reached.

AUTO mode fetches live NIFTY 5-minute candles and uses the earlier breakout/SMA rule:

- CALL when the latest close breaks above the prior 5-candle high and the 5-candle SMA is rising
- PUT when the latest close breaks below the prior 5-candle low and the 5-candle SMA is falling

Use `--max-ticks 3` for a short test run:

```powershell
py -m aadithya_quantlab.trading.zerodha_shadow_paper --side CALL --auto-nifty-atm --wait-for-market-open --quantity 65 --poll-seconds 10 --max-ticks 3 --reset
```

## One-Command Safe Workflow

This command runs all steps in sequence without placing orders:

1. Check connection
2. Fetch live NIFTY LTP
3. Fetch historical NIFTY 5-minute candles
4. Run EXP-001 report only if historical CSV exists

```powershell
py -m aadithya_quantlab.trading.zerodha_live_data_workflow --from "2026-07-10T09:15:00+05:30" --to "2026-07-10T15:30:00+05:30"
```
