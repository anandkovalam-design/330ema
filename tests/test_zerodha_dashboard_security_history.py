from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from aadithya_quantlab.zerodha_live_trading.auth import (
    AuthenticationService,
    ClientContext,
    hash_password,
    resolve_client_context,
    verify_password,
)
from aadithya_quantlab.zerodha_live_trading.app import _navigation_pages_for_role
from aadithya_quantlab.zerodha_live_trading.database import Database
from aadithya_quantlab.zerodha_live_trading.pnl import PnlThresholds, classify_pnl, monthly_summary


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "quantlab.db")


@pytest.fixture()
def owner(database: Database) -> tuple[AuthenticationService, int]:
    service = AuthenticationService(database)
    user_id = database.create_user("owner@example.com", hash_password("Correct horse battery 1!"), "OWNER")
    return service, user_id


def test_password_hashing_verification_and_plaintext_never_stored(database: Database) -> None:
    plaintext = "Correct horse battery 1!"
    password_hash = hash_password(plaintext)
    database.create_user("owner@example.com", password_hash, "OWNER")

    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, plaintext)
    assert not verify_password(password_hash, "wrong password")
    assert plaintext.encode() not in database.path.read_bytes()


def test_successful_and_failed_login_are_audited(owner: tuple[AuthenticationService, int], database: Database) -> None:
    service, _ = owner
    client = ClientContext(ip_address="127.0.0.1", user_agent="pytest")

    assert service.login("owner@example.com", "wrong password", client) is None
    token = service.login("owner@example.com", "Correct horse battery 1!", client)

    assert token
    assert service.validate_session(token)["role"] == "OWNER"
    event_types = [row["event_type"] for row in database.security_events()]
    assert "LOGIN_FAILED" in event_types
    assert "LOGIN_SUCCESS" in event_types


def test_failed_login_rate_limit_is_account_scoped_when_ip_unavailable(
    owner: tuple[AuthenticationService, int], database: Database
) -> None:
    service, _ = owner
    client = ClientContext()
    for _ in range(5):
        assert service.login("owner@example.com", "wrong password", client) is None
    assert service.login("owner@example.com", "Correct horse battery 1!", client) is None
    assert "LOGIN_RATE_LIMITED" in [row["event_type"] for row in database.security_events()]

    other_id = database.create_user("other@example.com", hash_password("Another safe password 3!"), "USER")
    assert other_id
    assert service.login("other@example.com", "Another safe password 3!", client)


def test_revoked_and_expired_sessions_are_denied(owner: tuple[AuthenticationService, int], database: Database) -> None:
    service, user_id = owner
    token = service.login("owner@example.com", "Correct horse battery 1!", ClientContext())
    session = service.validate_session(token)
    database.revoke_session(str(session["session_id"]))
    assert service.validate_session(token) is None

    expired_token = "expired-token"
    import hashlib
    session_id = hashlib.sha256(expired_token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    database.create_session(
        {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "last_activity": (now - timedelta(hours=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "revoked": 0,
        }
    )
    assert service.validate_session(expired_token) is None


def test_owner_only_permissions_and_password_change_revokes_other_sessions(
    owner: tuple[AuthenticationService, int], database: Database
) -> None:
    service, _ = owner
    client = ClientContext()
    current_token = service.login("owner@example.com", "Correct horse battery 1!", client)
    other_token = service.login("owner@example.com", "Correct horse battery 1!", client)
    current = service.validate_session(current_token)

    with pytest.raises(PermissionError):
        service.revoke_all_other_sessions({"role": "USER", "user_id": 999, "session_id": "x"})

    revoked = service.change_owner_password(
        current,
        "Correct horse battery 1!",
        "A completely new password 2!",
        "A completely new password 2!",
    )
    assert revoked == 1
    assert service.validate_session(current_token)
    assert service.validate_session(other_token) is None
    assert service.login("owner@example.com", "A completely new password 2!", client)
    assert "PASSWORD_CHANGED" in [row["event_type"] for row in database.security_events()]


def test_owner_can_create_and_disable_viewer(
    owner: tuple[AuthenticationService, int], database: Database
) -> None:
    service, owner_id = owner
    owner_session = {"role": "OWNER", "user_id": owner_id, "session_id": "owner-session"}

    viewer_id = service.create_viewer(
        owner_session,
        "Viewer@Example.com",
        "Viewer password 4!",
        "Viewer password 4!",
    )
    token = service.login("viewer@example.com", "Viewer password 4!", ClientContext())

    assert token
    assert service.validate_session(token)["role"] == "USER"
    assert database.list_users("USER")[0]["username"] == "viewer@example.com"
    assert "password_hash" not in database.list_users("USER")[0]
    assert _navigation_pages_for_role("USER") == [
        "Positions", "Trade history", "Index P&L", "Commodity P&L"
    ]
    assert "Trade controls" in _navigation_pages_for_role("OWNER")
    assert "Security" in _navigation_pages_for_role("OWNER")

    revoked = service.set_viewer_active(owner_session, viewer_id, False)
    assert revoked == 1
    assert service.validate_session(token) is None
    assert service.login("viewer@example.com", "Viewer password 4!", ClientContext()) is None
    assert service.set_viewer_active(owner_session, viewer_id, True) == 0
    assert service.login("viewer@example.com", "Viewer password 4!", ClientContext())
    assert {row["event_type"] for row in database.security_events()} >= {
        "VIEWER_CREATED",
        "VIEWER_DISABLED",
        "VIEWER_ENABLED",
    }


def test_non_owner_cannot_create_or_enable_viewers(
    owner: tuple[AuthenticationService, int], database: Database
) -> None:
    service, _ = owner
    viewer_id = database.create_user("viewer@example.com", hash_password("Viewer password 4!"), "USER")
    actor = {"role": "USER", "user_id": viewer_id, "session_id": "viewer-session"}

    with pytest.raises(PermissionError):
        service.create_viewer(actor, "other@example.com", "Another password 5!", "Another password 5!")
    with pytest.raises(PermissionError):
        service.set_viewer_active(actor, viewer_id, True)


def test_viewer_creation_validates_confirmation_and_unique_username(
    owner: tuple[AuthenticationService, int]
) -> None:
    service, owner_id = owner
    actor = {"role": "OWNER", "user_id": owner_id, "session_id": "owner-session"}

    with pytest.raises(ValueError, match="confirmation"):
        service.create_viewer(actor, "viewer@example.com", "Viewer password 4!", "Different password 5!")

    service.create_viewer(actor, "viewer@example.com", "Viewer password 4!", "Viewer password 4!")
    with pytest.raises(ValueError, match="already exists"):
        service.create_viewer(actor, "VIEWER@example.com", "Viewer password 4!", "Viewer password 4!")


def test_first_local_owner_can_be_created_interactively(tmp_path: Path) -> None:
    service = AuthenticationService(Database(tmp_path / "local-owner.db"))

    owner_id = service.bootstrap_local_owner(
        "Owner@Example.com", "Strong owner password 7!", "Strong owner password 7!"
    )

    owner = service.database.get_user(owner_id)
    assert owner is not None
    assert owner["username"] == "owner@example.com"
    assert owner["role"] == "OWNER"
    assert owner["password_hash"] != "Strong owner password 7!"
    with pytest.raises(ValueError, match="already exists"):
        service.bootstrap_local_owner("second-owner", "Another password 8!", "Another password 8!")


def _trade(key: str, underlying: str = "NIFTY", pnl_date: str = "2026-08-14") -> dict[str, object]:
    return {
        "trade_key": key,
        "trade_date": pnl_date,
        "mode": "PAPER",
        "underlying": underlying,
        "option_symbol": f"NFO:{underlying}TESTCE",
        "side": "CALL",
        "quantity": 10,
        "entry_time": f"{pnl_date}T09:30:00+05:30",
        "entry_price": 100.0,
        "entry_order_id": "PAPER",
        "strategy": "EMA_3_30",
    }


def test_database_and_trade_persistence_daily_aggregation_and_duplicate_prevention(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quantlab.db"
    first = Database(path)
    trade_id = first.upsert_open_trade(_trade("paper-1"))
    duplicate_id = first.upsert_open_trade(_trade("paper-1"))
    assert duplicate_id == trade_id
    first.complete_trade(
        trade_id,
        exit_time="2026-08-14T10:00:00+05:30",
        exit_price=125.0,
        realized_pnl=250.0,
        exit_order_id=None,
        exit_reason="EOD_CLOSE",
    )
    sensex_id = first.upsert_open_trade(_trade("paper-2", "SENSEX"))
    first.complete_trade(
        sensex_id,
        exit_time="2026-08-14T11:00:00+05:30",
        exit_price=50.0,
        realized_pnl=-500.0,
        exit_order_id=None,
        exit_reason="STOP_LOSS",
    )
    # A restart and repeated completion are idempotent.
    reopened = Database(path)
    reopened.complete_trade(
        trade_id,
        exit_time="2026-08-14T10:00:00+05:30",
        exit_price=125.0,
        realized_pnl=250.0,
        exit_order_id=None,
        exit_reason="EOD_CLOSE",
    )
    trades = reopened.list_trades(start_date="2026-08-14", end_date="2026-08-14")
    daily = reopened.daily_pnl("2026-08-01", "2026-08-31")[0]
    assert len(trades) == 2
    assert {trade["strategy"] for trade in trades} == {"EMA_3_30"}
    assert daily["nifty_pnl"] == 250.0
    assert daily["sensex_pnl"] == -500.0
    assert daily["total_pnl"] == -250.0
    assert daily["trade_count"] == 2


def test_database_accepts_and_aggregates_mcx_commodity_trades(tmp_path: Path) -> None:
    database = Database(tmp_path / "commodity.db")
    trade_id = database.upsert_open_trade(_trade("gold-1", "GOLD"))
    database.complete_trade(
        trade_id,
        exit_time="2026-08-14T22:50:00+05:30",
        exit_price=150.0,
        realized_pnl=500.0,
        exit_order_id=None,
        exit_reason="EOD_CLOSE",
    )

    daily = database.daily_pnl("2026-08-14", "2026-08-14", underlying="GOLD")[0]
    assert daily["gold_pnl"] == 500.0
    assert daily["total_pnl"] == 500.0


def test_version_two_database_migrates_trade_strategy_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "version-two.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_key TEXT NOT NULL UNIQUE,
                trade_date TEXT NOT NULL,
                mode TEXT NOT NULL,
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
            INSERT INTO trades(
                trade_key,trade_date,mode,underlying,option_symbol,side,quantity,
                entry_time,entry_price,created_at,updated_at
            ) VALUES(
                'legacy','2026-08-14','PAPER','NIFTY','NFO:NIFTYTESTCE','CALL',10,
                '2026-08-14T09:30:00+05:30',100,'2026-08-14T04:00:00+00:00','2026-08-14T04:00:00+00:00'
            );
            PRAGMA user_version = 2;
            """
        )

    database = Database(path)
    assert database.list_trades()[0]["strategy"] == "LEGACY_UNKNOWN"
    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_index_and_commodity_pnl_are_aggregated_separately(tmp_path: Path) -> None:
    database = Database(tmp_path / "split-pnl.db")
    for key, underlying, realized_pnl in (
        ("nifty-1", "NIFTY", 250.0),
        ("gold-1", "GOLD", -500.0),
    ):
        trade_id = database.upsert_open_trade(_trade(key, underlying))
        database.complete_trade(
            trade_id,
            exit_time="2026-08-14T15:00:00+05:30",
            exit_price=100.0,
            realized_pnl=realized_pnl,
            exit_order_id=None,
            exit_reason="TEST",
        )

    index = database.daily_pnl("2026-08-14", "2026-08-14", underlying="INDEX")[0]
    commodity = database.daily_pnl("2026-08-14", "2026-08-14", underlying="COMMODITY")[0]
    assert index["total_pnl"] == 250.0
    assert index["trade_count"] == 1
    assert commodity["total_pnl"] == -500.0
    assert commodity["trade_count"] == 1


@pytest.mark.parametrize(
    ("count", "pnl", "expected"),
    [(0, 9999, "no-trade"), (1, 3000, "strong-profit"), (1, 1, "profit"),
     (1, -1, "loss"), (1, -3000, "strong-loss"), (1, 0, "flat")],
)
def test_calendar_threshold_classification(count: int, pnl: float, expected: str) -> None:
    assert classify_pnl(count, pnl, PnlThresholds()) == expected


def test_calendar_monthly_summary() -> None:
    summary = monthly_summary(
        [
            {"trade_date": "2026-08-01", "total_pnl": 1000, "trade_count": 2, "winning_trades": 1, "losing_trades": 1},
            {"trade_date": "2026-08-02", "total_pnl": -200, "trade_count": 1, "winning_trades": 0, "losing_trades": 1},
        ],
        2026,
        8,
    )
    assert summary["net_pnl"] == 800
    assert summary["trading_days"] == 2
    assert summary["no_trade_days"] == 29
    assert summary["win_rate"] == pytest.approx(100 / 3)


def test_forwarded_ip_is_only_used_when_proxy_trust_is_enabled() -> None:
    headers = {"X-Forwarded-For": "203.0.113.10, 10.0.0.2", "User-Agent": "Chrome/1 Windows"}
    assert resolve_client_context(headers).ip_address == "Unavailable"
    trusted = resolve_client_context(headers, trust_proxy_headers=True)
    assert trusted.ip_address == "203.0.113.10"
    assert trusted.browser == "Chrome"
    assert trusted.operating_system == "Windows"
