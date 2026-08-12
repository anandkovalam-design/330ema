from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from .models import OpenPosition, TradeRecord


class TradeStorage:
    def __init__(self, sqlite_path: str | Path, csv_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.csv_path = Path(csv_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.sqlite_path)

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_records (
                    trade_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    expiry TEXT,
                    strike REAL,
                    option_type TEXT,
                    signal_id TEXT,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    requested_price REAL NOT NULL,
                    fill_price REAL NOT NULL,
                    stop_loss REAL,
                    target REAL,
                    status TEXT NOT NULL,
                    exit_reason TEXT,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    broker_fee REAL NOT NULL,
                    taxes REAL NOT NULL,
                    slippage_cost REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS open_positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    expiry TEXT,
                    strike REAL,
                    option_type TEXT,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL,
                    target REAL,
                    signal_id TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS engine_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def append_trade(self, trade: TradeRecord) -> None:
        payload = asdict(trade)
        payload["timestamp"] = trade.timestamp.isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trade_records (
                    trade_id, mode, timestamp, symbol, instrument, expiry, strike,
                    option_type, signal_id, side, quantity, requested_price, fill_price,
                    stop_loss, target, status, exit_reason, realized_pnl,
                    unrealized_pnl, broker_fee, taxes, slippage_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["trade_id"],
                    payload["mode"],
                    payload["timestamp"],
                    payload["symbol"],
                    payload["instrument"],
                    payload["expiry"],
                    payload["strike"],
                    payload["option_type"],
                    payload["signal_id"],
                    payload["side"],
                    payload["quantity"],
                    payload["requested_price"],
                    payload["fill_price"],
                    payload["stop_loss"],
                    payload["target"],
                    payload["status"],
                    payload["exit_reason"],
                    payload["realized_pnl"],
                    payload["unrealized_pnl"],
                    payload["broker_fee"],
                    payload["taxes"],
                    payload["slippage_cost"],
                ),
            )
        frame = pd.DataFrame([payload])
        frame.to_csv(self.csv_path, mode="a", header=not self.csv_path.exists(), index=False)

    def upsert_open_position(self, position: OpenPosition) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO open_positions (
                    position_id, symbol, instrument, expiry, strike, option_type,
                    side, quantity, entry_time, entry_price, stop_loss, target, signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.position_id,
                    position.symbol,
                    position.instrument,
                    position.expiry,
                    position.strike,
                    position.option_type,
                    position.side,
                    position.quantity,
                    position.entry_time.isoformat(),
                    position.entry_price,
                    position.stop_loss,
                    position.target,
                    position.signal_id,
                ),
            )

    def remove_open_position(self, position_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM open_positions WHERE position_id = ?", (position_id,))

    def load_open_positions(self) -> list[OpenPosition]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT position_id, symbol, instrument, expiry, strike, option_type,
                       side, quantity, entry_time, entry_price, stop_loss, target, signal_id
                FROM open_positions
                """
            ).fetchall()
        positions: list[OpenPosition] = []
        for row in rows:
            positions.append(
                OpenPosition(
                    position_id=str(row[0]),
                    symbol=str(row[1]),
                    instrument=str(row[2]),
                    expiry=row[3],
                    strike=row[4],
                    option_type=row[5],
                    side=str(row[6]),
                    quantity=int(row[7]),
                    entry_time=datetime.fromisoformat(str(row[8])),
                    entry_price=float(row[9]),
                    stop_loss=float(row[10]) if row[10] is not None else None,
                    target=float(row[11]) if row[11] is not None else None,
                    signal_id=str(row[12]),
                )
            )
        return positions

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO engine_state (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_state(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM engine_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row[0])

    def list_trade_records(self) -> pd.DataFrame:
        with self._conn() as conn:
            frame = pd.read_sql_query("SELECT * FROM trade_records ORDER BY timestamp ASC", conn)
        return frame
