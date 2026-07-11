# Data Schema: Intraday OHLCV

Status: Draft v0.2  
Owner: Data research

## Canonical Fields

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `symbol` | string | yes | Instrument identifier, for example `NIFTY`. |
| `timestamp` | datetime | yes | Bar close timestamp in exchange timezone. |
| `session_date` | date | yes | Trading session date. |
| `open` | float | yes | Bar open. |
| `high` | float | yes | Bar high. |
| `low` | float | yes | Bar low. |
| `close` | float | yes | Bar close. |
| `volume` | float | conditional | Required when available for tradable proxy. |
| `source` | string | yes | Data vendor or ingestion source. |
| `adjustment_flag` | string | no | Adjustment or cleaning marker. |

## Invariants

- `high >= max(open, close)`
- `low <= min(open, close)`
- `timestamp` must be monotonic within `symbol` and `session_date`.
- No future session-level value may be joined into an intraday decision row.

## Code Contract

The executable schema contract lives in:

- `aadithya_quantlab.data.contracts.validate_intraday_ohlcv_schema`
- `aadithya_quantlab.data.contracts.normalize_intraday_ohlcv`
- `aadithya_quantlab.data.loaders.load_intraday_ohlcv_csv`

## CSV Loading Requirements

CSV files must include the canonical fields above. The loader normalizes:

- `timestamp` to datetime.
- `session_date` to date.
- OHLCV columns to numeric values.
- `symbol`, `source`, and `adjustment_flag` to trimmed strings.

The loader rejects missing required columns, invalid OHLC relationships, duplicate bar keys, null identity fields, null OHLCV fields, negative volume, and non-monotonic timestamps.

## Replay Safety

EXP-001 also applies replay-safety checks through:

- `aadithya_quantlab.validation.leakage.check_replay_safe_intraday_bars`

Forbidden label-input columns include:

- `forward_return`
- `future_close`
- `mfe`
- `mae`
- `target`
- `label`

Full-session columns with prefixes such as `session_final_`, `day_final_`, or `full_session_` are blocked because they can leak information from later in the session.
