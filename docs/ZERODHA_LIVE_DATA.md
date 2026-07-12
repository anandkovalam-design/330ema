# Zerodha Kite Connect Live-Data Setup (Paper-Trading Safe)

This setup is intentionally live-data only. Live order placement is disabled in code.

## 1) Set credentials in PowerShell

```powershell
$env:ZERODHA_API_KEY = "<your_api_key>"
$env:ZERODHA_API_SECRET = "<your_api_secret>"
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

- Credentials are read from environment variables only.
- API secret and full access token are never printed by CLI commands.
- Live order placement is not available; `place_order` is blocked.
