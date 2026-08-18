"""Transactional SQLite services for dashboard security and trade history."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from aadithya_quantlab.zerodha_live_trading.persistence import runtime_path


SCHEMA_VERSION = 2
DATABASE_PATH = runtime_path("quantlab.db")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Small service layer that owns all dashboard SQL and migrations."""

    def __init__(self, path: Path | str = DATABASE_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.transaction() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version < 1:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('OWNER', 'USER')),
                        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                        created_at TEXT NOT NULL,
                        password_changed_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        created_at TEXT NOT NULL,
                        last_activity TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
                        ip_address TEXT,
                        country TEXT,
                        region TEXT,
                        city TEXT,
                        user_agent TEXT,
                        device_type TEXT,
                        browser TEXT,
                        operating_system TEXT
                    );
                    CREATE INDEX IF NOT EXISTS ix_sessions_user_active
                        ON sessions(user_id, revoked, expires_at);
                    CREATE TABLE IF NOT EXISTS security_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        user_id INTEGER REFERENCES users(id),
                        event_type TEXT NOT NULL,
                        ip_address TEXT,
                        approximate_location TEXT,
                        user_agent TEXT,
                        details TEXT
                    );
                    CREATE INDEX IF NOT EXISTS ix_security_events_time
                        ON security_events(timestamp DESC);
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_key TEXT NOT NULL UNIQUE,
                        trade_date TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK (mode IN ('PAPER', 'REAL')),
                        underlying TEXT NOT NULL CHECK (underlying IN ('NIFTY', 'SENSEX')),
                        option_symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        entry_time TEXT NOT NULL,
                        exit_time TEXT,
                        entry_price REAL NOT NULL,
                        exit_price REAL,
                        realized_pnl REAL,
                        entry_order_id TEXT,
                        exit_order_id TEXT,
                        exit_reason TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_trades_date_mode_underlying
                        ON trades(trade_date, mode, underlying);
                    CREATE TABLE IF NOT EXISTS daily_pnl (
                        trade_date TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK (mode IN ('PAPER', 'REAL')),
                        nifty_pnl REAL NOT NULL DEFAULT 0,
                        sensex_pnl REAL NOT NULL DEFAULT 0,
                        total_pnl REAL NOT NULL DEFAULT 0,
                        trade_count INTEGER NOT NULL DEFAULT 0,
                        winning_trades INTEGER NOT NULL DEFAULT 0,
                        losing_trades INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (trade_date, mode)
                    );
                    CREATE TABLE IF NOT EXISTS app_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        updated_by INTEGER REFERENCES users(id)
                    );
                    """
                )
                connection.execute("PRAGMA user_version = 1")
                version = 1
            if version < 2:
                connection.executescript(
                    """
                    ALTER TABLE trades RENAME TO trades_v1;
                    CREATE TABLE trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_key TEXT NOT NULL UNIQUE,
                        trade_date TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK (mode IN ('PAPER', 'REAL')),
                        underlying TEXT NOT NULL,
                        option_symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        entry_time TEXT NOT NULL,
                        exit_time TEXT,
                        entry_price REAL NOT NULL,
                        exit_price REAL,
                        realized_pnl REAL,
                        entry_order_id TEXT,
                        exit_order_id TEXT,
                        exit_reason TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO trades SELECT * FROM trades_v1;
                    DROP TABLE trades_v1;
                    CREATE INDEX ix_trades_date_mode_underlying
                        ON trades(trade_date, mode, underlying);
                    ALTER TABLE daily_pnl ADD COLUMN crudeoil_pnl REAL NOT NULL DEFAULT 0;
                    ALTER TABLE daily_pnl ADD COLUMN naturalgas_pnl REAL NOT NULL DEFAULT 0;
                    ALTER TABLE daily_pnl ADD COLUMN gold_pnl REAL NOT NULL DEFAULT 0;
                    ALTER TABLE daily_pnl ADD COLUMN silver_pnl REAL NOT NULL DEFAULT 0;
                    """
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def fetch_one(self, query: str, parameters: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(query, tuple(parameters)).fetchone()
        return dict(row) if row else None

    def fetch_all(self, query: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]

    def create_user(self, username: str, password_hash: str, role: str = "USER") -> int:
        now = utc_now_iso()
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO users(username,password_hash,role,active,created_at,password_changed_at) "
                "VALUES(?,?,?,?,?,?)",
                (username.strip(), password_hash, role.upper(), 1, now, now),
            )
            return int(cursor.lastrowid)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM users WHERE username = ?", (username.strip(),))

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

    def list_users(self, role: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id,username,role,active,created_at,password_changed_at FROM users"
        parameters: Sequence[Any] = ()
        if role:
            query += " WHERE role=?"
            parameters = (role.upper(),)
        return self.fetch_all(f"{query} ORDER BY username COLLATE NOCASE", parameters)

    def owner_exists(self) -> bool:
        return self.fetch_one("SELECT id FROM users WHERE role='OWNER' LIMIT 1") is not None

    def update_password(self, user_id: int, password_hash: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
                (password_hash, utc_now_iso(), user_id),
            )

    def set_user_active(self, user_id: int, active: bool) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE users SET active=? WHERE id=?", (int(active), user_id))

    def revoke_user_sessions(self, user_id: int) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET revoked=1 WHERE user_id=? AND revoked=0",
                (user_id,),
            )
            return int(cursor.rowcount)

    def create_session(self, values: Mapping[str, Any]) -> None:
        columns = (
            "session_id", "user_id", "created_at", "last_activity", "expires_at", "revoked",
            "ip_address", "country", "region", "city", "user_agent", "device_type", "browser",
            "operating_system",
        )
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO sessions({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                tuple(values.get(column) for column in columns),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT s.*,u.username,u.role,u.active FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.session_id=?",
            (session_id,),
        )

    def touch_session(self, session_id: str, timestamp: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE sessions SET last_activity=? WHERE session_id=?", (timestamp, session_id))

    def revoke_session(self, session_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE sessions SET revoked=1 WHERE session_id=?", (session_id,))

    def revoke_other_sessions(self, user_id: int, current_session_id: str) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET revoked=1 WHERE user_id=? AND session_id<>? AND revoked=0",
                (user_id, current_session_id),
            )
            return int(cursor.rowcount)

    def active_sessions(self) -> list[dict[str, Any]]:
        idle_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        return self.fetch_all(
            "SELECT s.*,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.revoked=0 AND s.expires_at>? AND s.last_activity>? ORDER BY s.last_activity DESC",
            (utc_now_iso(), idle_cutoff),
        )

    def log_security_event(
        self,
        event_type: str,
        *,
        user_id: int | None = None,
        ip_address: str | None = None,
        approximate_location: str | None = None,
        user_agent: str | None = None,
        details: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO security_events(timestamp,user_id,event_type,ip_address,"
                "approximate_location,user_agent,details) VALUES(?,?,?,?,?,?,?)",
                (utc_now_iso(), user_id, event_type, ip_address, approximate_location, user_agent, details),
            )

    def recent_failed_logins(self, username: str, ip_address: str, since: str) -> int:
        if ip_address and ip_address != "Unavailable":
            query = (
                "SELECT COUNT(*) AS count FROM security_events WHERE event_type='LOGIN_FAILED' "
                "AND timestamp>=? AND (details=? OR ip_address=?)"
            )
            parameters = (since, f"username={username.strip().lower()}", ip_address)
        else:
            query = (
                "SELECT COUNT(*) AS count FROM security_events WHERE event_type='LOGIN_FAILED' "
                "AND timestamp>=? AND details=?"
            )
            parameters = (since, f"username={username.strip().lower()}")
        row = self.fetch_one(query, parameters)
        return int((row or {}).get("count", 0))

    def security_events(self, limit: int = 250) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT e.id,e.timestamp,u.username,e.event_type,e.ip_address,e.approximate_location,"
            "e.user_agent,e.details FROM security_events e LEFT JOIN users u ON u.id=e.user_id "
            "ORDER BY e.timestamp DESC LIMIT ?",
            (int(limit),),
        )

    def upsert_open_trade(self, trade: Mapping[str, Any]) -> int:
        now = utc_now_iso()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO trades(
                    trade_key,trade_date,mode,underlying,option_symbol,side,quantity,entry_time,
                    entry_price,entry_order_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(trade_key) DO UPDATE SET
                    entry_order_id=COALESCE(excluded.entry_order_id,trades.entry_order_id), updated_at=excluded.updated_at""",
                (
                    trade["trade_key"], trade["trade_date"], trade["mode"], trade["underlying"],
                    trade["option_symbol"], trade["side"], int(trade["quantity"]), trade["entry_time"],
                    float(trade["entry_price"]), trade.get("entry_order_id"), now, now,
                ),
            )
            row = connection.execute("SELECT id FROM trades WHERE trade_key=?", (trade["trade_key"],)).fetchone()
            return int(row["id"])

    def complete_trade(
        self,
        trade_id: int,
        *,
        exit_time: str,
        exit_price: float,
        realized_pnl: float,
        exit_order_id: str | None,
        exit_reason: str,
    ) -> None:
        now = utc_now_iso()
        with self.transaction() as connection:
            current = connection.execute("SELECT trade_date,mode,exit_time FROM trades WHERE id=?", (trade_id,)).fetchone()
            if current is None:
                raise ValueError(f"Unknown trade id {trade_id}")
            if current["exit_time"] is None:
                connection.execute(
                    "UPDATE trades SET exit_time=?,exit_price=?,realized_pnl=?,exit_order_id=?,"
                    "exit_reason=?,updated_at=? WHERE id=?",
                    (exit_time, float(exit_price), float(realized_pnl), exit_order_id, exit_reason, now, trade_id),
                )
            self._refresh_daily_pnl(connection, str(current["trade_date"]), str(current["mode"]), now)

    @staticmethod
    def _refresh_daily_pnl(connection: sqlite3.Connection, trade_date: str, mode: str, now: str) -> None:
        row = connection.execute(
            """SELECT
                COALESCE(SUM(CASE WHEN underlying='NIFTY' THEN realized_pnl ELSE 0 END),0) nifty_pnl,
                COALESCE(SUM(CASE WHEN underlying='SENSEX' THEN realized_pnl ELSE 0 END),0) sensex_pnl,
                COALESCE(SUM(CASE WHEN underlying='CRUDEOIL' THEN realized_pnl ELSE 0 END),0) crudeoil_pnl,
                COALESCE(SUM(CASE WHEN underlying='NATURALGAS' THEN realized_pnl ELSE 0 END),0) naturalgas_pnl,
                COALESCE(SUM(CASE WHEN underlying='GOLD' THEN realized_pnl ELSE 0 END),0) gold_pnl,
                COALESCE(SUM(CASE WHEN underlying='SILVER' THEN realized_pnl ELSE 0 END),0) silver_pnl,
                COALESCE(SUM(realized_pnl),0) total_pnl,
                COUNT(*) trade_count,
                SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) winning_trades,
                SUM(CASE WHEN realized_pnl<0 THEN 1 ELSE 0 END) losing_trades
                FROM trades WHERE trade_date=? AND mode=? AND exit_time IS NOT NULL""",
            (trade_date, mode),
        ).fetchone()
        connection.execute(
            """INSERT INTO daily_pnl(trade_date,mode,nifty_pnl,sensex_pnl,total_pnl,trade_count,
                winning_trades,losing_trades,updated_at,crudeoil_pnl,naturalgas_pnl,gold_pnl,silver_pnl)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(trade_date,mode) DO UPDATE SET nifty_pnl=excluded.nifty_pnl,
                sensex_pnl=excluded.sensex_pnl,total_pnl=excluded.total_pnl,
                trade_count=excluded.trade_count,winning_trades=excluded.winning_trades,
                losing_trades=excluded.losing_trades,updated_at=excluded.updated_at,
                crudeoil_pnl=excluded.crudeoil_pnl,naturalgas_pnl=excluded.naturalgas_pnl,
                gold_pnl=excluded.gold_pnl,silver_pnl=excluded.silver_pnl""",
            (trade_date, mode, row["nifty_pnl"], row["sensex_pnl"], row["total_pnl"],
             row["trade_count"], row["winning_trades"], row["losing_trades"], now,
             row["crudeoil_pnl"], row["naturalgas_pnl"], row["gold_pnl"], row["silver_pnl"]),
        )

    def list_trades(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        mode: str = "ALL",
        underlying: str = "ALL",
    ) -> list[dict[str, Any]]:
        clauses, parameters = ["1=1"], []
        if start_date:
            clauses.append("trade_date>=?")
            parameters.append(start_date)
        if end_date:
            clauses.append("trade_date<=?")
            parameters.append(end_date)
        if mode.upper() != "ALL":
            clauses.append("mode=?")
            parameters.append(mode.upper())
        if underlying.upper() != "ALL":
            clauses.append("underlying=?")
            parameters.append(underlying.upper())
        return self.fetch_all(
            f"SELECT * FROM trades WHERE {' AND '.join(clauses)} ORDER BY entry_time DESC",
            parameters,
        )

    def daily_pnl(self, start_date: str, end_date: str, mode: str = "ALL", underlying: str = "ALL") -> list[dict[str, Any]]:
        pnl_expression = "total_pnl"
        if underlying.upper() == "NIFTY":
            pnl_expression = "nifty_pnl"
        elif underlying.upper() == "SENSEX":
            pnl_expression = "sensex_pnl"
        elif underlying.upper() in {"CRUDEOIL", "NATURALGAS", "GOLD", "SILVER"}:
            pnl_expression = f"{underlying.lower()}_pnl"
        clauses, parameters = ["trade_date BETWEEN ? AND ?"], [start_date, end_date]
        if mode.upper() != "ALL":
            clauses.append("mode=?")
            parameters.append(mode.upper())
        return self.fetch_all(
            f"""SELECT trade_date,SUM(nifty_pnl) nifty_pnl,SUM(sensex_pnl) sensex_pnl,
                SUM(crudeoil_pnl) crudeoil_pnl,SUM(naturalgas_pnl) naturalgas_pnl,
                SUM(gold_pnl) gold_pnl,SUM(silver_pnl) silver_pnl,
                SUM({pnl_expression}) total_pnl,SUM(trade_count) trade_count,
                SUM(winning_trades) winning_trades,SUM(losing_trades) losing_trades
                FROM daily_pnl WHERE {' AND '.join(clauses)} GROUP BY trade_date ORDER BY trade_date""",
            parameters,
        )

    def get_setting(self, key: str, default: str) -> str:
        row = self.fetch_one("SELECT setting_value FROM app_settings WHERE setting_key=?", (key,))
        return str(row["setting_value"]) if row else default

    def set_setting(self, key: str, value: str, user_id: int) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO app_settings(setting_key,setting_value,updated_at,updated_by) VALUES(?,?,?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,"
                "updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                (key, value, utc_now_iso(), user_id),
            )
