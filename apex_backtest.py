from __future__ import annotations

import argparse
from pathlib import Path

from quantlab.apex.backtest.engine import ApexBacktestConfig, ApexProBacktestEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="APEX Pro NIFTY options backtester (paper-only).")
    parser.add_argument("--underlying", default="data/nifty_5m.csv", help="Path to underlying 5-minute OHLCV CSV.")
    parser.add_argument("--options-dir", default="data/options", help="Directory containing historical options CSV files.")
    parser.add_argument("--no-proxy-options", action="store_true", help="Reject trades when option candles are unavailable.")
    parser.add_argument("--max-trades-per-day", type=int, default=3, help="Daily paper-trade cap.")
    parser.add_argument("--start-date", default=None, help="Start date filter YYYY-MM-DD.")
    parser.add_argument("--end-date", default=None, help="End date filter YYYY-MM-DD.")
    parser.add_argument("--audit-dir", default="reports/apex_audit_backtest", help="Directory for APEX audit JSONL files.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = ApexBacktestConfig(
        underlying_csv=Path(args.underlying),
        options_dir=Path(args.options_dir),
        no_proxy_options=bool(args.no_proxy_options),
        max_trades_per_day=int(args.max_trades_per_day),
        start_date=args.start_date,
        end_date=args.end_date,
        audit_dir=Path(args.audit_dir),
        trades_csv=Path("reports/apex_backtest_trades.csv"),
        summary_md=Path("reports/apex_backtest_summary.md"),
    )

    engine = ApexProBacktestEngine(config)
    result = engine.run()

    print(f"audit_run_id={result.audit_run_id}")
    print(f"trades_csv={config.trades_csv}")
    print(f"summary_md={config.summary_md}")
    print(f"total_trades={len(result.trades)}")
    if result.stopped_early:
        print(f"stopped_early=True reason={result.stop_reason}")
    else:
        print("stopped_early=False")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
