"""Durable, secret-aware storage for the Zerodha Streamlit dashboard."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


STATE_SCHEMA_VERSION = 1
PERSISTED_ENGINE_FIELDS = (
    "enabled",
    "hard_stop_requested",
    "last_signal_ts",
    "open_trade",
    "realized_pnl",
    "events",
    "order_logs",
    "quantity",
    "lots",
    "lot_size",
    "signal_symbol",
    "sl_points",
    "sl_to_cost_profit_pct",
    "trail_after_profit_pct",
    "mfe_giveback_pct",
    "trail_trigger_points",
    "trail_step_points",
    "max_trades",
    "trades_taken",
    "entry_status",
    "reconciliation_required",
    "reconciliation_status",
)


def data_directory() -> Path:
    """Return the local or mounted cloud directory used for sensitive runtime data."""

    return Path(os.getenv("ZERODHA_DATA_DIR", "outputs/zerodha")).expanduser()


def runtime_path(filename: str) -> Path:
    return data_directory() / filename


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        temporary_path.chmod(0o600)
    except OSError:
        # Windows ACLs and some Azure Files mounts do not implement POSIX modes.
        pass
    os.replace(temporary_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def save_daily_connection(
    path: Path,
    *,
    session_date: str,
    access_token: str,
    api_key: str | None = None,
) -> None:
    """Persist the daily access token without ever logging or returning its value."""

    payload: dict[str, Any] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "date": session_date,
        "access_token": access_token.strip(),
    }
    if api_key:
        payload["api_key"] = api_key.strip()
    _atomic_write_json(path, payload)


def load_daily_connection(path: Path, *, session_date: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(payload.get("date", "")).strip() != session_date:
        return None
    access_token = str(payload.get("access_token", "")).strip()
    if not access_token:
        return None
    result = {"access_token": access_token}
    api_key = str(payload.get("api_key", "")).strip()
    if api_key:
        result["api_key"] = api_key
    return result


def save_engine_state(
    path: Path,
    *,
    session_date: str,
    engine_state: Mapping[str, Mapping[str, Any]],
    saved_at: datetime,
) -> None:
    """Atomically persist only JSON-safe fields required for restart recovery."""

    underlyings: dict[str, dict[str, Any]] = {}
    for name, state in engine_state.items():
        underlyings[name] = {
            field: state[field]
            for field in PERSISTED_ENGINE_FIELDS
            if field in state
        }
    _atomic_write_json(
        path,
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "session_date": session_date,
            "saved_at": saved_at.isoformat(),
            "underlyings": underlyings,
        },
    )


def load_engine_state(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not path.exists():
        return {}, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        return {}, {}
    raw_underlyings = payload.get("underlyings")
    if not isinstance(raw_underlyings, dict):
        return {}, {}
    underlyings = {
        str(name): dict(state)
        for name, state in raw_underlyings.items()
        if isinstance(state, dict)
    }
    metadata = {
        "session_date": str(payload.get("session_date", "")),
        "saved_at": str(payload.get("saved_at", "")),
    }
    return underlyings, metadata
