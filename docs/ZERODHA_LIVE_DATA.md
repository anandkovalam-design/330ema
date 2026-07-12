# Zerodha Kite Connect Live-Data Setup (Paper-Trading Safe)

This setup is intentionally live-data only. Live order placement is disabled in code.

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

## One-Command Safe Workflow

This command runs all steps in sequence without placing orders:

1. Check connection
2. Fetch live NIFTY LTP
3. Fetch historical NIFTY 5-minute candles
4. Run EXP-001 report only if historical CSV exists

```powershell
py -m aadithya_quantlab.trading.zerodha_live_data_workflow --from "2026-07-10T09:15:00+05:30" --to "2026-07-10T15:30:00+05:30"
```
