"""Fetch safe live market data from Zerodha without order capabilities."""

from __future__ import annotations

import argparse
import json

from aadithya_quantlab.trading.zerodha_live import fetch_live_data_from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch live Zerodha market data.")
    parser.add_argument(
        "--instrument",
        default="NSE:NIFTY 50",
        help="Instrument in exchange:symbol format, for example NSE:NIFTY 50.",
    )
    parser.add_argument(
        "--mode",
        choices=("ltp", "quote"),
        default="ltp",
        help="Live data mode to fetch.",
    )
    args = parser.parse_args(argv)

    response = fetch_live_data_from_env(instrument=args.instrument, mode=args.mode)
    print(json.dumps(response, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
