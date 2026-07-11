"""Zerodha Kite Connect helpers.

This module deliberately supports authentication helpers and paper-order payload
creation only. It does not place live broker orders.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from aadithya_quantlab.trading.paper import PaperOrder


@dataclass(frozen=True)
class ZerodhaConfig:
    """Configuration loaded from environment variables."""

    api_key: str
    access_token: str | None = None


def load_zerodha_config_from_env() -> ZerodhaConfig:
    """Load Zerodha Kite credentials from environment variables."""

    api_key = os.getenv("ZERODHA_API_KEY", "").strip()
    access_token = os.getenv("ZERODHA_ACCESS_TOKEN", "").strip() or None
    if not api_key:
        raise ValueError("Set ZERODHA_API_KEY before connecting to Zerodha.")
    return ZerodhaConfig(api_key=api_key, access_token=access_token)


def build_kite_login_url(api_key: str) -> str:
    """Build the Kite Connect login URL."""

    return f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"


def build_nifty_option_paper_order(
    tradingsymbol: str,
    transaction_type: str,
    quantity: int,
    price: float | None = None,
) -> PaperOrder:
    """Create a safe NFO paper-order payload for NIFTY options."""

    transaction = transaction_type.upper()
    if transaction not in {"BUY", "SELL"}:
        raise ValueError("transaction_type must be BUY or SELL.")
    if quantity <= 0:
        raise ValueError("quantity must be positive.")
    return PaperOrder(
        tradingsymbol=tradingsymbol,
        exchange="NFO",
        transaction_type=transaction,
        quantity=quantity,
        order_type="MARKET" if price is None else "LIMIT",
        product="MIS",
        validity="DAY",
        price=price,
    )

