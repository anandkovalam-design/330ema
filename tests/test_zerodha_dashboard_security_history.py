from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aadithya_quantlab.zerodha_live_trading.auth import (
    AuthenticationService,
    ClientContext,
    hash_password,
    resolve_client_context,
    verify_password,
)
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
    assert daily["nifty_pnl"] == 250.0
    assert daily["sensex_pnl"] == -500.0
    assert daily["total_pnl"] == -250.0
    assert daily["trade_count"] == 2


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
