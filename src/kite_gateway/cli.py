from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .config import Settings
from .factory import LIVE_CONFIRMATION_PHRASE, create_broker
from .kite_session import exchange_request_token, login_url
from .models import OrderRequest
from .token_store import KeyringTokenStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe Zerodha Kite gateway")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login-url", help="Print the official Kite login URL")
    exchange = subparsers.add_parser("exchange-token")
    exchange.add_argument("--request-token", required=True)
    subparsers.add_parser("orders", help="List today's orders")

    paper_demo = subparsers.add_parser("paper-demo")
    paper_demo.add_argument("--symbol", default="INFY")
    paper_demo.add_argument("--quantity", type=int, default=1)

    args = parser.parse_args()
    settings = Settings.from_env()
    store = KeyringTokenStore(settings.account)

    if args.command == "login-url":
        print(login_url(settings))
        return
    if args.command == "exchange-token":
        result = exchange_request_token(settings, args.request_token, store)
        print(json.dumps(result, indent=2, default=str))
        return
    if args.command == "orders":
        broker = create_broker(settings)
        print(json.dumps([asdict(item) for item in broker.list_orders()], indent=2, default=str))
        return
    if args.command == "paper-demo":
        broker = create_broker(settings)
        result = broker.place_order(
            OrderRequest(
                exchange="NSE",
                tradingsymbol=args.symbol,
                side="BUY",
                quantity=args.quantity,
                product="CNC",
            )
        )
        print(json.dumps(asdict(result), indent=2, default=str))
        return

    raise SystemExit(
        f"Unsupported command. Live order placement is intentionally not exposed by "
        f"this CLI. Application code must pass: {LIVE_CONFIRMATION_PHRASE}"
    )
