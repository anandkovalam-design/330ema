from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from aadithya_quantlab.trading.zerodha_check_connection import main as check_connection_main
from aadithya_quantlab.trading.zerodha_fetch_historical import main as fetch_historical_main
from aadithya_quantlab.trading.zerodha_fetch_live_data import main as fetch_live_data_main
from aadithya_quantlab.trading.zerodha_generate_access_token import main as generate_access_token_main
from aadithya_quantlab.trading.zerodha_live import (
    access_token_preview,
    build_safe_kite_client_from_env,
    load_access_token,
)
from aadithya_quantlab.trading.zerodha_login_url import main as login_url_main


class FakeKiteConnect:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.access_token: str | None = None

    def set_access_token(self, access_token: str) -> None:
        self.access_token = access_token

    def generate_session(self, request_token: str, api_secret: str) -> dict[str, str]:
        assert request_token == "request-token-1234"
        assert api_secret == "super-secret-value"
        return {"access_token": "abcd1234wxyz5678"}

    def profile(self) -> dict[str, str]:
        return {"user_id": "AB1234", "user_name": "Test User"}

    def ltp(self, instrument: str) -> dict[str, dict[str, float]]:
        return {instrument: {"last_price": 25000.5}}

    def quote(self, instrument: str) -> dict[str, dict[str, float]]:
        return {instrument: {"last_price": 25000.5, "volume": 1000.0}}

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        *,
        continuous: bool,
        oi: bool,
    ) -> list[dict[str, object]]:
        assert instrument_token == 256265
        assert interval == "minute"
        assert not continuous
        assert not oi
        assert from_date < to_date
        ist = timezone(timedelta(hours=5, minutes=30))
        return [
            {
                "date": datetime(2026, 7, 10, 9, 15, tzinfo=ist),
                "open": 25000.0,
                "high": 25020.0,
                "low": 24995.0,
                "close": 25010.0,
                "volume": 1200.0,
            },
            {
                "date": datetime(2026, 7, 10, 9, 16, tzinfo=ist),
                "open": 25010.0,
                "high": 25025.0,
                "low": 25005.0,
                "close": 25015.0,
                "volume": 1100.0,
            },
        ]


class EmptyHistoryKiteConnect(FakeKiteConnect):
    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        *,
        continuous: bool,
        oi: bool,
    ) -> list[dict[str, object]]:
        return []


@pytest.fixture()
def zerodha_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZERODHA_API_KEY", "kite-api-key")
    monkeypatch.setenv("ZERODHA_API_SECRET", "super-secret-value")
    monkeypatch.setenv("ZERODHA_REQUEST_TOKEN", "request-token-1234")
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "live-access-token-abcdef")


@pytest.fixture()
def fake_kiteconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("kiteconnect")
    module.KiteConnect = FakeKiteConnect
    monkeypatch.setitem(sys.modules, "kiteconnect", module)


@pytest.fixture()
def empty_history_kiteconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("kiteconnect")
    module.KiteConnect = EmptyHistoryKiteConnect
    monkeypatch.setitem(sys.modules, "kiteconnect", module)


def test_login_url_cli_prints_kite_url(zerodha_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = login_url_main()
    output = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert output == "https://kite.zerodha.com/connect/login?api_key=kite-api-key&v=3"


def test_generate_access_token_cli_masks_console_output(
    zerodha_env: None,
    fake_kiteconnect: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token_output = tmp_path / "access_token.txt"
    exit_code = generate_access_token_main(["--token-output", str(token_output)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert token_output.read_text(encoding="utf-8") == "abcd1234wxyz5678"
    assert "abcd...5678" in output
    assert "abcd1234wxyz5678" not in output
    assert "super-secret-value" not in output


def test_check_connection_cli_uses_profile(
    zerodha_env: None,
    fake_kiteconnect: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = check_connection_main()
    output = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert "user_id=AB1234" in output
    assert "user_name=Test User" in output


def test_fetch_live_data_cli_ltp(
    zerodha_env: None,
    fake_kiteconnect: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = fetch_live_data_main(["--instrument", "NSE:NIFTY 50", "--mode", "ltp"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "NSE:NIFTY 50" in output
    assert "last_price" in output


def test_fetch_historical_cli_writes_canonical_csv(
    zerodha_env: None,
    fake_kiteconnect: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "exp001_intraday.csv"
    exit_code = fetch_historical_main(
        [
            "--instrument-token",
            "256265",
            "--symbol",
            "NIFTY",
            "--interval",
            "minute",
            "--from",
            "2026-07-10T09:15:00+05:30",
            "--to",
            "2026-07-10T09:16:00+05:30",
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    bars = pd.read_csv(output_path)
    assert exit_code == 0
    assert "Saved canonical intraday CSV" in output
    assert list(bars.columns) == [
        "symbol",
        "timestamp",
        "session_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
        "adjustment_flag",
    ]


def test_place_order_is_blocked(zerodha_env: None, fake_kiteconnect: None) -> None:
    client = build_safe_kite_client_from_env()

    with pytest.raises(RuntimeError, match="disabled"):
        client.place_order(variety="regular")


def test_no_runtime_place_order_execution_call_exists() -> None:
    trading_dir = Path(__file__).resolve().parents[1] / "src" / "aadithya_quantlab" / "trading"

    for path in trading_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "zerodha_live.py":
            continue
        assert "kite.place_order(" not in text


def test_access_token_loader_uses_file_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "access.token"
    token_file.write_text("fallback-token-123456", encoding="utf-8")
    monkeypatch.delenv("ZERODHA_ACCESS_TOKEN", raising=False)

    token = load_access_token(fallback_path=token_file)

    assert token == "fallback-token-123456"


def test_access_token_loader_prefers_env_over_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "access.token"
    token_file.write_text("fallback-token-123456", encoding="utf-8")
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "env-token-abcdef")

    token = load_access_token(fallback_path=token_file)

    assert token == "env-token-abcdef"


def test_access_token_redaction() -> None:
    assert access_token_preview("abcd1234wxyz5678") == "abcd...5678"


def test_no_candle_historical_response_is_handled_cleanly(
    zerodha_env: None,
    empty_history_kiteconnect: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "exp001_intraday.csv"
    exit_code = fetch_historical_main(
        [
            "--instrument-token",
            "256265",
            "--symbol",
            "NIFTY",
            "--interval",
            "minute",
            "--from",
            "2026-07-10T09:15:00+05:30",
            "--to",
            "2026-07-10T09:16:00+05:30",
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "No candles found" in output
    assert "Try " in output
    assert not output_path.exists()


def test_gitignore_blocks_secret_patterns() -> None:
    project_root = Path(__file__).resolve().parents[1]
    gitignore = (project_root / ".gitignore").read_text(encoding="utf-8")

    assert "outputs/zerodha/" in gitignore
    assert "*.token" in gitignore
    assert "*.secret" in gitignore
    assert ".env" in gitignore
