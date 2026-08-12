"""Command-line utility to place an options order through kite_gateway.

TRADING_MODE (env var) controls execution:
  PAPER   — in-memory only, no network (default)
  SANDBOX — Zerodha official sandbox
  LIVE    — real orders, requires KITE_LIVE_TRADING_ENABLED=true and the
            operator confirmation phrase at runtime
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from kite_gateway import OrderRequest, Settings, create_broker
from aadithya_quantlab.trading.zerodha import NIFTY_PAPER_QUANTITY, SENSEX_PAPER_QUANTITY

_UNDERLYING_MAP = {
    "NIFTY": {"exchange": "NFO", "quantity": NIFTY_PAPER_QUANTITY},
    "SENSEX": {"exchange": "BFO", "quantity": SENSEX_PAPER_QUANTITY},
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Place an options order via kite_gateway (PAPER/SANDBOX/LIVE)."
    )
    parser.add_argument("--symbol", required=True, help="Trading symbol, e.g. NIFTY2572524000CE.")
    parser.add_argument("--side", required=True, choices=("BUY", "SELL"))
    parser.add_argument(
        "--underlying",
        required=True,
        choices=tuple(_UNDERLYING_MAP),
        help="NIFTY (qty=65, NFO) or SENSEX (qty=20, BFO).",
    )
    parser.add_argument(
        "--lots",
        type=int,
        default=1,
        help="Number of lots (default 1). Quantity = lots × lot size.",
    )
    parser.add_argument("--price", type=float, help="Limit price; omit for MARKET order.")
    parser.add_argument("--tag", default="apexpaper", help="Order tag (alphanumeric, max 20 chars).")
    args = parser.parse_args(argv)

    cfg = _UNDERLYING_MAP[args.underlying]
    quantity = cfg["quantity"] * args.lots

    settings = Settings.from_env()
    broker = create_broker(settings)

    result = broker.place_order(
        OrderRequest(
            exchange=cfg["exchange"],
            tradingsymbol=args.symbol,
            side=args.side,
            quantity=quantity,
            product="MIS",
            order_type="MARKET" if args.price is None else "LIMIT",
            price=args.price,
            tag=args.tag,
        )
    )

    print(json.dumps(asdict(result), indent=2, default=str))
    print(
        f"\n[{settings.mode.value}] {result.status}  "
        f"{args.side} {quantity} {args.symbol}  order_id={result.order_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

