"""Fetch Zerodha historical intraday candles into canonical EXP-001 CSV."""

from __future__ import annotations

import argparse
from datetime import datetime

from aadithya_quantlab.trading.zerodha_live import (
    NoHistoricalCandlesError,
    fetch_historical_to_canonical_csv,
)


def _parse_iso_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Use ISO datetime format, for example 2026-07-10T09:15:00+05:30"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch canonical intraday OHLCV CSV from Zerodha.")
    parser.add_argument("--instrument-token", required=True, type=int)
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--interval", default="minute")
    parser.add_argument("--from", dest="from_date", required=True, type=_parse_iso_datetime)
    parser.add_argument("--to", dest="to_date", required=True, type=_parse_iso_datetime)
    parser.add_argument(
        "--output",
        default="outputs/exp001/nifty_intraday_ohlcv.csv",
        help="Output CSV path matching the canonical intraday schema.",
    )
    args = parser.parse_args(argv)

    try:
        output_path = fetch_historical_to_canonical_csv(
            instrument_token=args.instrument_token,
            symbol=args.symbol,
            interval=args.interval,
            from_date=args.from_date,
            to_date=args.to_date,
            output_csv=args.output,
        )
    except NoHistoricalCandlesError as error:
        print(f"No candles found: {error}")
        return 2

    print(f"Saved canonical intraday CSV: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
