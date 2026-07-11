"""Paper-trading ledger with no live order execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class PaperOrder:
    """A simulated order record."""

    tradingsymbol: str
    exchange: str
    transaction_type: str
    quantity: int
    order_type: str = "MARKET"
    product: str = "MIS"
    validity: str = "DAY"
    price: float | None = None
    tag: str = "aadithya-quantlab-paper"
    created_at: str = ""

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["created_at"] = self.created_at or datetime.now(timezone.utc).isoformat()
        record["mode"] = "PAPER"
        return record


class PaperLedger:
    """Append-only CSV ledger for paper orders."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record_order(self, order: PaperOrder) -> dict[str, object]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = order.to_record()
        frame = pd.DataFrame([record])
        header = not self.path.exists()
        frame.to_csv(self.path, mode="a", header=header, index=False)
        return record

    def read_orders(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        return pd.read_csv(self.path)

