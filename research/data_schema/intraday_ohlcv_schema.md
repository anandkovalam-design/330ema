# Data Schema: Intraday OHLCV

Status: Draft stub  
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

