"""One-command live-data workflow with paper-trading-only safety."""

from __future__ import annotations

import argparse
from pathlib import Path

from aadithya_quantlab.experiments.run_exp001 import main as run_exp001_main
from aadithya_quantlab.trading.zerodha_check_connection import main as check_connection_main
from aadithya_quantlab.trading.zerodha_fetch_historical import main as fetch_historical_main
from aadithya_quantlab.trading.zerodha_fetch_live_data import main as fetch_live_data_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run safe Zerodha live-data workflow and EXP-001 without live orders."
    )
    parser.add_argument("--instrument", default="NSE:NIFTY 50")
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--instrument-token", default="256265")
    parser.add_argument("--interval", default="5minute")
    parser.add_argument("--from", dest="from_date", required=True)
    parser.add_argument("--to", dest="to_date", required=True)
    parser.add_argument(
        "--historical-output",
        default="outputs/exp001/nifty_5minute_ohlcv.csv",
    )
    parser.add_argument(
        "--report-output",
        default="validation/reports/EXP-001_nifty_live_history.md",
    )
    parser.add_argument(
        "--labeled-output",
        default="validation/reports/EXP-001_nifty_live_history_labeled.csv",
    )
    args = parser.parse_args(argv)

    print("Step 1/4: Checking Zerodha connection...")
    if check_connection_main() != 0:
        return 1

    print("Step 2/4: Fetching live NIFTY LTP...")
    if fetch_live_data_main(["--instrument", args.instrument, "--mode", "ltp"]) != 0:
        return 1

    print("Step 3/4: Fetching historical NIFTY candles...")
    historical_exit = fetch_historical_main(
        [
            "--instrument-token",
            args.instrument_token,
            "--symbol",
            args.symbol,
            "--interval",
            args.interval,
            "--from",
            args.from_date,
            "--to",
            args.to_date,
            "--output",
            args.historical_output,
        ]
    )
    if historical_exit != 0:
        print("Historical fetch failed, skipping EXP-001.")
        return historical_exit

    historical_path = Path(args.historical_output)
    if not historical_path.exists():
        print("Historical CSV was not created, skipping EXP-001.")
        return 3

    print("Step 4/4: Running EXP-001 report...")
    return run_exp001_main(
        [
            "--input",
            str(historical_path),
            "--output",
            args.report_output,
            "--labeled-output",
            args.labeled_output,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
