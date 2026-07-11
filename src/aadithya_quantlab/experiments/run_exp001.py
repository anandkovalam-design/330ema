"""Command-line runner for EXP-001."""

from __future__ import annotations

import argparse
from pathlib import Path

from aadithya_quantlab.data.loaders import load_intraday_ohlcv_csv
from aadithya_quantlab.experiments.exp001_baseline_labeler import run_exp001
from aadithya_quantlab.validation.market_state_reports import (
    write_market_state_report_markdown,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run EXP-001 on intraday OHLCV CSV data.")
    parser.add_argument("--input", required=True, help="Path to canonical intraday OHLCV CSV.")
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the Markdown validation report.",
    )
    parser.add_argument(
        "--labeled-output",
        help="Optional path to write labeled bars as CSV.",
    )
    args = parser.parse_args(argv)

    bars = load_intraday_ohlcv_csv(args.input)
    result = run_exp001(bars)
    report_path = write_market_state_report_markdown(
        result.report,
        args.output,
        title="EXP-001 Market State Report",
    )
    if args.labeled_output:
        labeled_path = Path(args.labeled_output)
        labeled_path.parent.mkdir(parents=True, exist_ok=True)
        result.labeled_bars.to_csv(labeled_path, index=False)

    print(f"Wrote report: {report_path}")
    if args.labeled_output:
        print(f"Wrote labeled bars: {args.labeled_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

