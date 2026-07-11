"""Command-line utility to append a Zerodha-shaped paper order."""

from __future__ import annotations

import argparse

from aadithya_quantlab.trading.paper import PaperLedger
from aadithya_quantlab.trading.zerodha import build_nifty_option_paper_order


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a Zerodha-shaped paper order.")
    parser.add_argument("--symbol", required=True, help="NFO trading symbol.")
    parser.add_argument("--side", required=True, choices=("BUY", "SELL"))
    parser.add_argument("--qty", required=True, type=int)
    parser.add_argument("--price", type=float)
    parser.add_argument(
        "--ledger",
        default="paper_trades/paper_orders.csv",
        help="Output CSV paper ledger path.",
    )
    args = parser.parse_args(argv)

    order = build_nifty_option_paper_order(
        tradingsymbol=args.symbol,
        transaction_type=args.side,
        quantity=args.qty,
        price=args.price,
    )
    record = PaperLedger(args.ledger).record_order(order)
    print(f"Recorded PAPER order: {record['transaction_type']} {record['quantity']} {record['tradingsymbol']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

