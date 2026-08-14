from __future__ import annotations

import json
import os
import calendar
from dataclasses import dataclass
from datetime import date, datetime, time as wall_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from aadithya_quantlab.trading.zerodha import build_kite_login_url
from aadithya_quantlab.zerodha_live_trading.auth import (
    AuthenticationService,
    ClientContext,
    resolve_client_context,
)
from aadithya_quantlab.zerodha_live_trading.database import Database
from aadithya_quantlab.zerodha_live_trading.persistence import (
    load_daily_connection,
    load_engine_state,
    runtime_path,
    save_daily_connection,
    save_engine_state,
)
from aadithya_quantlab.zerodha_live_trading.pnl import (
    PnlThresholds,
    calendar_html,
    month_bounds,
    monthly_summary,
)


IST = ZoneInfo("Asia/Kolkata")
CONNECTION_STORE_PATH = runtime_path("streamlit_dashboard_connection.json")
RISK_SETTINGS_PATH = runtime_path("live_trading_risk_settings.json")
TRADING_STATE_PATH = runtime_path("streamlit_dashboard_state.json")
CREDENTIAL_SERVICE_NAME = "aadithya-zerodha-live-trading"
API_KEY_ACCOUNT = "api-key"
API_SECRET_ACCOUNT = "api-secret"


@st.cache_resource
def _database() -> Database:
    return Database()


@st.cache_resource
def _authentication() -> AuthenticationService:
    return AuthenticationService(_database())


@dataclass(frozen=True)
class UnderlyingConfig:
    name: str
    spot_instrument_token: int
    option_symbol_prefix: str
    option_exchange: str
    expiry_weekday: int
    strike_step: int
    default_qty: int
    default_sl_points: float


UNDERLYINGS: dict[str, UnderlyingConfig] = {
    "NIFTY": UnderlyingConfig(
        name="NIFTY",
        spot_instrument_token=256265,
        option_symbol_prefix="NIFTY",
        option_exchange="NFO",
        expiry_weekday=1,
        strike_step=50,
        default_qty=130,
        default_sl_points=20.0,
    ),
    "SENSEX": UnderlyingConfig(
        name="SENSEX",
        spot_instrument_token=265,
        option_symbol_prefix="SENSEX",
        option_exchange="BFO",
        expiry_weekday=3,
        strike_step=100,
        default_qty=40,
        default_sl_points=50.0,
    ),
}


QTY_BLOCKS: dict[str, int] = {
    "NIFTY": 130,
    "SENSEX": 40,
}

LOT_SIZES: dict[str, int] = {
    "NIFTY": 65,
    "SENSEX": 20,
}

RISK_SETTING_KEYS = (
    "sl_points",
    "sl_to_cost_profit_pct",
    "trail_after_profit_pct",
    "mfe_giveback_pct",
    "trail_trigger_points",
    "trail_step_points",
)


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _today_ist_iso() -> str:
    return datetime.now(IST).date().isoformat()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_cloud_runtime() -> bool:
    return _env_flag("ZERODHA_CLOUD_MODE")


def _runtime_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def _credential_keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise RuntimeError("Install the 'keyring' package to remember Zerodha credentials securely.") from exc
    return keyring, KeyringError


def _save_login_credentials(api_key: str, api_secret: str) -> None:
    if _is_cloud_runtime():
        raise RuntimeError(
            "Cloud credentials are read-only. Configure ZERODHA_API_KEY and "
            "ZERODHA_API_SECRET as Container Apps secret references."
        )
    keyring, keyring_error = _credential_keyring()
    try:
        keyring.set_password(CREDENTIAL_SERVICE_NAME, API_KEY_ACCOUNT, api_key.strip())
        keyring.set_password(CREDENTIAL_SERVICE_NAME, API_SECRET_ACCOUNT, api_secret.strip())
    except keyring_error as exc:
        raise RuntimeError("Could not save Zerodha credentials in Windows Credential Manager.") from exc


def _load_login_credentials() -> dict[str, str] | None:
    api_key_env = _runtime_secret("ZERODHA_API_KEY")
    api_secret_env = _runtime_secret("ZERODHA_API_SECRET")
    if api_key_env or api_secret_env:
        if not api_key_env or not api_secret_env:
            raise RuntimeError(
                "ZERODHA_API_KEY and ZERODHA_API_SECRET must both be configured."
            )
        return {"api_key": api_key_env, "api_secret": api_secret_env}
    if _is_cloud_runtime():
        return None
    keyring, keyring_error = _credential_keyring()
    try:
        api_key = str(keyring.get_password(CREDENTIAL_SERVICE_NAME, API_KEY_ACCOUNT) or "").strip()
        api_secret = str(keyring.get_password(CREDENTIAL_SERVICE_NAME, API_SECRET_ACCOUNT) or "").strip()
    except keyring_error as exc:
        raise RuntimeError("Could not read Zerodha credentials from Windows Credential Manager.") from exc
    if not api_key or not api_secret:
        return None
    return {"api_key": api_key, "api_secret": api_secret}


def _clear_login_credentials() -> None:
    if _is_cloud_runtime():
        raise RuntimeError("Cloud secrets must be removed through Azure, not from the dashboard.")
    keyring, keyring_error = _credential_keyring()
    try:
        for account in (API_KEY_ACCOUNT, API_SECRET_ACCOUNT):
            if keyring.get_password(CREDENTIAL_SERVICE_NAME, account):
                keyring.delete_password(CREDENTIAL_SERVICE_NAME, account)
    except keyring_error as exc:
        raise RuntimeError("Could not remove Zerodha credentials from Windows Credential Manager.") from exc


def _credential_preview(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= 6:
        return "saved (hidden)"
    return f"{stripped[:4]}...{stripped[-2:]}"


def _load_risk_settings() -> dict[str, dict[str, float]]:
    if not RISK_SETTINGS_PATH.exists():
        return {}
    try:
        payload = json.loads(RISK_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    settings: dict[str, dict[str, float]] = {}
    for name in UNDERLYINGS:
        row = payload.get(name, {})
        if isinstance(row, dict):
            settings[name] = {
                key: _to_float(row.get(key), 0.0)
                for key in RISK_SETTING_KEYS
                if key in row
            }
    return settings


def _save_risk_settings(engine_state: dict[str, dict[str, Any]]) -> None:
    payload = {
        name: {key: _to_float(engine_state[name].get(key), 0.0) for key in RISK_SETTING_KEYS}
        for name in UNDERLYINGS
    }
    RISK_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RISK_SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _save_connection_for_today(api_key: str, access_token: str) -> None:
    save_daily_connection(
        CONNECTION_STORE_PATH,
        session_date=_today_ist_iso(),
        access_token=access_token,
        api_key=None if _is_cloud_runtime() else api_key,
    )


def _load_connection_for_today() -> dict[str, str] | None:
    api_key = _runtime_secret("ZERODHA_API_KEY")
    access_token = _runtime_secret("ZERODHA_ACCESS_TOKEN")
    if api_key and access_token:
        return {"api_key": api_key, "access_token": access_token}
    saved = load_daily_connection(CONNECTION_STORE_PATH, session_date=_today_ist_iso())
    if not saved:
        return None
    api_key = api_key or saved.get("api_key", "")
    access_token = saved.get("access_token", "")
    if not api_key or not access_token:
        return None
    return {"api_key": api_key, "access_token": access_token}


def _clear_saved_connection() -> None:
    try:
        if CONNECTION_STORE_PATH.exists():
            CONNECTION_STORE_PATH.unlink()
    except Exception:
        pass


def _apply_visual_style() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: radial-gradient(1200px 700px at 50% -20%, rgba(0, 255, 128, 0.08), transparent 60%), #050805;
            color: #6dff9b;
        }
        h1, h2, h3, p, label, span, div {
            color: #6dff9b;
            letter-spacing: 0.2px;
        }
        div[data-testid="stVerticalBlock"] div[data-testid="stContainer"] {
            border-radius: 14px;
            background: rgba(8, 16, 10, 0.9);
            border: 1px solid rgba(93, 255, 154, 0.2);
        }
        div[data-testid="stMetric"] {
            background: rgba(6, 14, 9, 0.95);
            border: 1px solid rgba(93, 255, 154, 0.28);
            border-radius: 12px;
            padding: 10px 12px;
        }
        div[data-testid="stMetricLabel"] {
            color: #9cffbd;
        }
        div[data-testid="stMetricValue"] {
            color: #3fff86;
        }
        div.stButton > button {
            border-radius: 10px;
            border: 1px solid rgba(93, 255, 154, 0.45);
            background: linear-gradient(135deg, #0b140d 0%, #102617 100%);
            color: #8effb5;
            font-weight: 600;
        }
        div.stButton > button:hover {
            border-color: #4dff94;
            box-shadow: 0 0 0 1px rgba(77, 255, 148, 0.25), 0 0 18px rgba(40, 190, 95, 0.25);
        }
        button[data-testid="stBaseButton-primary"] {
            background: #d92d3f !important;
            border: 1px solid #ff7582 !important;
            color: #ffffff !important;
            box-shadow: 0 0 14px rgba(217, 45, 63, 0.28) !important;
        }
        button[data-testid="stBaseButton-primary"] p {
            color: #ffffff !important;
            font-weight: 700 !important;
        }
        button[data-testid="stBaseButton-primary"]:hover {
            background: #f04455 !important;
            border-color: #ff9aa3 !important;
            box-shadow: 0 0 20px rgba(240, 68, 85, 0.42) !important;
        }
        button[data-testid="stBaseButton-primary"]:focus-visible {
            outline: 2px solid #ff9aa3 !important;
            outline-offset: 2px !important;
        }
        button[data-variant="segmented_control"][role="radio"] {
            background: #07100a !important;
            border: 1px solid rgba(142, 255, 181, 0.45) !important;
            box-shadow: none !important;
        }
        button[data-variant="segmented_control"][role="radio"] p {
            color: #9cffbd !important;
            font-weight: 600 !important;
        }
        button[data-variant="segmented_control"][role="radio"][aria-checked="true"] {
            background: #35e57d !important;
            border: 2px solid #b7ffce !important;
            box-shadow: 0 0 16px rgba(53, 229, 125, 0.35) !important;
        }
        button[data-variant="segmented_control"][role="radio"][aria-checked="true"] p {
            color: #031208 !important;
            font-weight: 800 !important;
        }
        button[data-variant="segmented_control"][role="radio"][aria-checked="true"] p::before {
            content: "\\2713";
            margin-right: 7px;
        }
        button[data-variant="segmented_control"][role="radio"]:focus,
        button[data-variant="segmented_control"][role="radio"]:focus-visible {
            outline: 2px solid #8effb5 !important;
            outline-offset: 2px !important;
        }
        div[data-baseweb="input"] {
            background: #0b140d;
            border: 1px solid rgba(93, 255, 154, 0.35);
            border-radius: 10px;
        }
        div[data-testid="stTextInputRootElement"] input {
            color: #9cffbd;
            background: #0b140d;
            border-radius: 10px;
        }
        div[data-baseweb="select"] > div {
            background: #0b140d;
            color: #9cffbd;
            border: 1px solid rgba(93, 255, 154, 0.35);
        }
        div[data-testid="stCodeBlock"] {
            border: 1px solid rgba(93, 255, 154, 0.22);
            border-radius: 10px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _normalize_qty(value: object, block: int) -> int:
    qty = int(_to_float(value, float(block)))
    if qty < block:
        return block
    # Keep quantity aligned to strategy lot blocks.
    return ((qty + block - 1) // block) * block


def _normalize_lots(value: object, min_lots: int = 2, lot_step: int = 2) -> int:
    lots = int(_to_float(value, float(min_lots)))
    if lots < min_lots:
        return min_lots
    if lots % lot_step != 0:
        lots += lot_step - (lots % lot_step)
    return lots


def _parse_hhmm(value: str) -> wall_time:
    parsed = datetime.strptime(value.strip(), "%H:%M")
    return wall_time(parsed.hour, parsed.minute)


def _get_kite(api_key: str, access_token: str) -> KiteConnect:
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def _fetch_spot_5m(kite: KiteConnect, cfg: UnderlyingConfig) -> pd.DataFrame:
    now_ist = datetime.now(IST)
    start_dt = datetime.combine(now_ist.date() - timedelta(days=7), wall_time(9, 15), tzinfo=IST)
    rows = kite.historical_data(
        cfg.spot_instrument_token,
        start_dt,
        now_ist,
        "5minute",
        continuous=False,
        oi=False,
    )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.rename(columns={"date": "timestamp"})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return frame


def _compute_signals(frame: pd.DataFrame) -> pd.DataFrame:
    bars = frame.copy()
    bars["ema_fast"] = bars["close"].ewm(span=3, adjust=False).mean()
    bars["ema_slow"] = bars["close"].ewm(span=30, adjust=False).mean()
    prev_fast = bars["ema_fast"].shift(1)
    prev_slow = bars["ema_slow"].shift(1)
    bars["cross_up"] = (bars["ema_fast"] > bars["ema_slow"]) & (prev_fast <= prev_slow)
    bars["cross_down"] = (bars["ema_fast"] < bars["ema_slow"]) & (prev_fast >= prev_slow)
    return bars


def _build_price_chart(bars: pd.DataFrame, name: str) -> alt.LayerChart:
    chart_data = bars[["timestamp", "open", "high", "low", "close", "ema_fast", "ema_slow"]].copy()
    base = alt.Chart(chart_data).encode(
        x=alt.X("timestamp:T", title="Time (IST)", axis=alt.Axis(format="%H:%M", labelAngle=-45)),
        tooltip=[
            alt.Tooltip("timestamp:T", title="Time", format="%d %b %H:%M"),
            alt.Tooltip("open:Q", title="Open", format=",.2f"),
            alt.Tooltip("high:Q", title="High", format=",.2f"),
            alt.Tooltip("low:Q", title="Low", format=",.2f"),
            alt.Tooltip("close:Q", title="Close", format=",.2f"),
            alt.Tooltip("ema_fast:Q", title="EMA 3", format=",.2f"),
            alt.Tooltip("ema_slow:Q", title="EMA 30", format=",.2f"),
        ],
    )
    candle_color = alt.condition(
        "datum.close >= datum.open",
        alt.value("#3fff86"),
        alt.value("#ff5f6d"),
    )
    wicks = base.mark_rule(strokeWidth=1).encode(
        y=alt.Y("low:Q", title=f"{name} spot", scale=alt.Scale(zero=False)),
        y2="high:Q",
        color=candle_color,
    )
    bodies = base.mark_bar(size=5).encode(
        y=alt.Y("open:Q", scale=alt.Scale(zero=False)),
        y2="close:Q",
        color=candle_color,
    )
    ema_fast = base.mark_line(color="#f8d34a", strokeWidth=2).encode(
        y=alt.Y("ema_fast:Q", scale=alt.Scale(zero=False))
    )
    ema_slow = base.mark_line(color="#4db8ff", strokeWidth=2).encode(
        y=alt.Y("ema_slow:Q", scale=alt.Scale(zero=False))
    )
    return alt.layer(wicks, bodies, ema_fast, ema_slow).properties(height=360).interactive()


def _next_expiry_weekday(day: datetime.date, expiry_weekday: int) -> datetime.date:
    days_ahead = (expiry_weekday - day.weekday()) % 7
    return day + timedelta(days=days_ahead)


def _select_option_contract(
    kite: KiteConnect,
    cfg: UnderlyingConfig,
    side: str,
    spot_price: float,
    expiry_week_offset: int = 0,
) -> str:
    option_rows = kite.instruments(cfg.option_exchange)
    atm_strike = int(round(spot_price / float(cfg.strike_step)) * cfg.strike_step)
    option_type = "CE" if side == "CALL" else "PE"
    session_date = datetime.now(IST).date()
    base_expiry = _next_expiry_weekday(session_date, cfg.expiry_weekday)
    target_expiry = base_expiry + timedelta(days=7 * max(int(expiry_week_offset), 0))

    pool: list[tuple[datetime.date, int, str]] = []
    for row in option_rows:
        symbol = str(row.get("tradingsymbol", ""))
        if not symbol.startswith(cfg.option_symbol_prefix):
            continue
        if str(row.get("instrument_type", "")) != option_type:
            continue
        expiry = pd.to_datetime(row.get("expiry"), errors="coerce")
        if pd.isna(expiry):
            continue
        expiry_date = expiry.date()
        if expiry_date < session_date:
            continue
        strike = int(_to_float(row.get("strike"), 0.0))
        if strike <= 0:
            continue
        pool.append((expiry_date, abs(strike - atm_strike), symbol))

    if not pool:
        raise ValueError(f"No eligible {cfg.name} {option_type} contract found.")

    exact_expiry = [r for r in pool if r[0] == target_expiry]
    if exact_expiry:
        candidates = exact_expiry
    else:
        post_target = [r for r in pool if r[0] >= target_expiry]
        candidates = post_target if post_target else pool
    expiry_date, _, symbol = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
    _ = expiry_date
    return f"{cfg.option_exchange}:{symbol}"


def _extract_ltp(kite: KiteConnect, instrument: str) -> float:
    payload = kite.ltp(instrument)
    info = payload.get(instrument)
    if not isinstance(info, dict):
        raise ValueError(f"No LTP for {instrument}")
    ltp = _to_float(info.get("last_price"), 0.0)
    if ltp <= 0:
        raise ValueError(f"Invalid LTP for {instrument}")
    return ltp


def _split_instrument(instrument: str) -> tuple[str, str]:
    exchange, symbol = instrument.split(":", 1)
    return exchange, symbol


def _push_order_log(
    engine: dict[str, Any],
    *,
    action: str,
    instrument: str,
    side: str,
    quantity: int,
    status: str,
    order_id: str = "",
    message: str = "",
) -> None:
    order_logs: list[dict[str, Any]] = engine.setdefault("order_logs", [])
    order_logs.append(
        {
            "time": datetime.now(IST).strftime("%H:%M:%S"),
            "action": action,
            "instrument": instrument,
            "side": side,
            "quantity": int(quantity),
            "status": status,
            "order_id": order_id,
            "message": message,
        }
    )
    if len(order_logs) > 20:
        del order_logs[:-20]


def _place_market_order(kite: KiteConnect, instrument: str, side: str, quantity: int) -> str:
    exchange, symbol = _split_instrument(instrument)
    txn = kite.TRANSACTION_TYPE_BUY if side == "BUY" else kite.TRANSACTION_TYPE_SELL
    order_id = kite.place_order(
        variety=kite.VARIETY_REGULAR,
        exchange=exchange,
        tradingsymbol=symbol,
        transaction_type=txn,
        quantity=int(quantity),
        order_type=kite.ORDER_TYPE_MARKET,
        product=kite.PRODUCT_MIS,
    )
    return str(order_id)


def _default_state_for(cfg: UnderlyingConfig) -> dict[str, Any]:
    return {
        "enabled": True,
        "hard_stop_requested": False,
        "last_signal_ts": None,
        "open_trade": None,
        "realized_pnl": 0.0,
        "events": [],
        "order_logs": [],
        "quantity": cfg.default_qty,
        "sl_points": cfg.default_sl_points,
        "sl_to_cost_profit_pct": 0.30,
        "trail_after_profit_pct": 0.50,
        "mfe_giveback_pct": 0.275,
        "trail_trigger_points": 15.0,
        "trail_step_points": 5.0,
        "max_trades": 3,
        "trades_taken": 0,
        "entry_status": "Waiting for market data",
        "reconciliation_required": False,
        "reconciliation_status": "NOT_REQUIRED",
    }


def _persist_engine_state(engine_state: dict[str, dict[str, Any]]) -> None:
    save_engine_state(
        TRADING_STATE_PATH,
        session_date=_today_ist_iso(),
        engine_state=engine_state,
        saved_at=datetime.now(IST),
    )


def _has_unverified_broker_activity(state: dict[str, Any]) -> bool:
    open_trade = state.get("open_trade")
    if isinstance(open_trade, dict) and str(open_trade.get("trade_mode", "PAPER")).upper() == "REAL":
        return True
    logs = state.get("order_logs", [])
    return any(
        isinstance(row, dict)
        and bool(str(row.get("order_id", "")).strip())
        and str(row.get("order_id", "")).upper() != "PAPER"
        for row in logs
    )


def _restore_persistent_engine_state() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    persisted, metadata = load_engine_state(TRADING_STATE_PATH)
    restored = {name: _default_state_for(cfg) for name, cfg in UNDERLYINGS.items()}
    for name, saved_state in persisted.items():
        if name not in restored:
            continue
        restored[name].update(saved_state)
        if _has_unverified_broker_activity(restored[name]):
            restored[name]["reconciliation_required"] = True
            restored[name]["reconciliation_status"] = "REQUIRED_AFTER_RESTART"
            restored[name]["enabled"] = False
            restored[name]["entry_status"] = "Recovered state; broker reconciliation required"
    return restored, metadata


def _reconcile_engine_state_with_broker(
    kite: KiteConnect,
    engine_state: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Read broker orders/positions and clear recovery gates only on an exact match."""

    orders = kite.orders()
    position_payload = kite.positions()
    net_positions = position_payload.get("net", []) if isinstance(position_payload, dict) else []
    orders_by_id = {
        str(row.get("order_id", "")): row
        for row in orders
        if isinstance(row, dict) and row.get("order_id")
    }
    positions_by_instrument = {
        f"{row.get('exchange', '')}:{row.get('tradingsymbol', '')}": int(row.get("quantity", 0))
        for row in net_positions
        if isinstance(row, dict)
    }

    issues: list[str] = []
    checked_order_ids: list[str] = []
    expected_open_instruments: set[str] = set()
    tracked_instruments: set[str] = set()
    for name, state in engine_state.items():
        for log in state.get("order_logs", []):
            if not isinstance(log, dict):
                continue
            instrument = str(log.get("instrument", "")).strip()
            if instrument:
                tracked_instruments.add(instrument)
            order_id = str(log.get("order_id", "")).strip()
            if order_id and order_id.upper() != "PAPER":
                checked_order_ids.append(order_id)
                broker_order = orders_by_id.get(order_id)
                if broker_order is None:
                    issues.append(f"{name}: broker order {order_id} was not returned")
                elif str(broker_order.get("status", "")).upper() not in {"COMPLETE", "CANCELLED", "REJECTED"}:
                    issues.append(f"{name}: broker order {order_id} is not terminal")

        open_trade = state.get("open_trade")
        if isinstance(open_trade, dict) and str(open_trade.get("trade_mode", "PAPER")).upper() == "REAL":
            instrument = str(open_trade.get("instrument", "")).strip()
            expected_quantity = int(open_trade.get("quantity", 0))
            expected_open_instruments.add(instrument)
            tracked_instruments.add(instrument)
            if positions_by_instrument.get(instrument, 0) != expected_quantity:
                issues.append(
                    f"{name}: expected {expected_quantity} open at {instrument}, "
                    f"broker reports {positions_by_instrument.get(instrument, 0)}"
                )

    for instrument in sorted(tracked_instruments - expected_open_instruments):
        if positions_by_instrument.get(instrument, 0) != 0:
            issues.append(f"Unexpected broker position for tracked instrument {instrument}")

    status = "MISMATCH" if issues else "MATCHED"
    for state in engine_state.values():
        if state.get("reconciliation_required"):
            state["reconciliation_required"] = bool(issues)
            state["reconciliation_status"] = status
    return {
        "status": status,
        "issues": issues,
        "checked_order_ids": checked_order_ids,
        "broker_position_count": len(net_positions),
    }


def _push_event(engine: dict[str, Any], text: str) -> None:
    events: list[str] = engine.setdefault("events", [])
    events.append(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {text}")
    if len(events) > 20:
        del events[:-20]


def _persist_open_trade(cfg: UnderlyingConfig, open_trade: dict[str, Any]) -> None:
    entry_time = str(open_trade["entry_time"])
    entry_order_id = str(open_trade.get("entry_order_id", ""))
    mode = str(open_trade.get("trade_mode", "PAPER")).upper()
    trade_key = (
        f"REAL:{entry_order_id}"
        if mode == "REAL" and entry_order_id
        else f"PAPER:{cfg.name}:{open_trade['instrument']}:{entry_time}"
    )
    trade_id = _database().upsert_open_trade(
        {
            "trade_key": trade_key,
            "trade_date": datetime.fromisoformat(entry_time).date().isoformat(),
            "mode": mode,
            "underlying": cfg.name,
            "option_symbol": str(open_trade["instrument"]),
            "side": str(open_trade["side"]),
            "quantity": int(open_trade["quantity"]),
            "entry_time": entry_time,
            "entry_price": _to_float(open_trade["entry_option"]),
            "entry_order_id": entry_order_id or None,
        }
    )
    open_trade["persistent_trade_id"] = trade_id
    open_trade["trade_key"] = trade_key


def _complete_persistent_trade(
    cfg: UnderlyingConfig,
    open_trade: dict[str, Any],
    *,
    exit_price: float,
    realized_pnl: float,
    exit_reason: str,
    exit_time: datetime,
) -> None:
    if not open_trade.get("persistent_trade_id"):
        # Older recovery JSON and tests may predate persistent trade fields.
        open_trade.setdefault("entry_time", str(open_trade.get("signal_time") or exit_time.isoformat()))
        open_trade.setdefault("side", "CALL" if str(open_trade.get("instrument", "")).endswith("CE") else "PUT")
        open_trade.setdefault("entry_order_id", "PAPER" if str(open_trade.get("trade_mode", "PAPER")).upper() == "PAPER" else "")
        _persist_open_trade(cfg, open_trade)
    _database().complete_trade(
        int(open_trade["persistent_trade_id"]),
        exit_time=exit_time.isoformat(),
        exit_price=exit_price,
        realized_pnl=realized_pnl,
        exit_order_id=str(open_trade.get("exit_order_id") or "") or None,
        exit_reason=exit_reason,
    )


def _entries_blocked_for_expiry_day(
    current_weekday: int,
    expiry_weekday: int,
    expiry_week_offset: int,
) -> bool:
    return current_weekday == expiry_weekday and expiry_week_offset == 0


def _live_orders_permitted(engine: dict[str, Any], real_mode_armed: bool) -> bool:
    return (
        bool(real_mode_armed)
        and not _is_cloud_runtime()
        and not bool(engine.get("reconciliation_required", False))
    )


def _update_trailing_stop(open_trade: dict[str, Any], engine: dict[str, Any], ltp: float) -> str | None:
    entry = _to_float(open_trade.get("entry_option"), 0.0)
    if entry <= 0 or ltp <= 0:
        return None

    active_stop = _to_float(open_trade.get("stop_price"), 0.0)
    max_ltp = max(_to_float(open_trade.get("max_ltp"), entry), ltp)
    open_trade["max_ltp"] = max_ltp
    sl_to_cost_pct = max(0.0, _to_float(engine.get("sl_to_cost_profit_pct"), 0.30))
    trail_after_pct = max(sl_to_cost_pct, _to_float(engine.get("trail_after_profit_pct"), 0.50))
    giveback_pct = min(max(_to_float(engine.get("mfe_giveback_pct"), 0.275), 0.0), 1.0)
    trigger_points = max(_to_float(engine.get("trail_trigger_points"), 15.0), 0.01)
    step_points = max(_to_float(engine.get("trail_step_points"), 5.0), 0.0)

    stage: str | None = None
    if max_ltp >= entry * (1.0 + sl_to_cost_pct) and active_stop < entry:
        active_stop = entry
        stage = "SL_TO_COST"

    trail_start = entry * (1.0 + trail_after_pct)
    if max_ltp >= trail_start:
        open_trade["trailing_active"] = True
        mfe_stop = entry + (max_ltp - entry) * (1.0 - giveback_pct)
        if mfe_stop > active_stop:
            active_stop = mfe_stop
            stage = "MFE_TRAIL"

        next_trigger = _to_float(open_trade.get("next_trail_trigger"), trail_start + trigger_points)
        while max_ltp >= next_trigger:
            active_stop += step_points
            next_trigger += trigger_points
            stage = "STEP_TRAIL"
        open_trade["next_trail_trigger"] = next_trigger

    previous_stop = _to_float(open_trade.get("stop_price"), 0.0)
    open_trade["stop_price"] = min(max(previous_stop, active_stop), max_ltp)
    return stage if open_trade["stop_price"] > previous_stop else None


def _run_engine_for(
    kite: KiteConnect,
    cfg: UnderlyingConfig,
    engine: dict[str, Any],
    entry_start: wall_time,
    entry_end: wall_time,
    force_exit: wall_time,
    expiry_week_offset: int,
    trade_mode: str,
    real_mode_armed: bool,
) -> dict[str, Any]:
    now_ist = datetime.now(IST)

    bars = _fetch_spot_5m(kite, cfg)
    if bars.empty:
        engine["entry_status"] = "No market data returned"
        return engine

    bars = _compute_signals(bars)
    session_bars = bars.loc[bars["timestamp"].dt.date == now_ist.date()].copy()
    display_bars = session_bars if not session_bars.empty else bars.loc[bars["timestamp"].dt.date == bars["timestamp"].dt.date.max()]
    latest = display_bars.iloc[-1]
    latest_time = pd.Timestamp(latest["timestamp"])
    latest_close = _to_float(latest["close"], 0.0)
    engine["latest_bars"] = display_bars.tail(80).copy()
    engine["latest_spot"] = latest_close
    engine["latest_time"] = latest_time

    open_trade = engine.get("open_trade")
    if isinstance(open_trade, dict) and open_trade:
        ltp = _extract_ltp(kite, str(open_trade["instrument"]))
        qty = int(open_trade["quantity"])
        entry = _to_float(open_trade["entry_option"], 0.0)
        trail_stage = _update_trailing_stop(open_trade, engine, ltp)
        stop = _to_float(open_trade["stop_price"], 0.0)
        if trail_stage:
            _push_event(engine, f"{cfg.name} {trail_stage} raised SL to {stop:.2f} at LTP {ltp:.2f}")

        unrealized = (ltp - entry) * qty
        open_trade["ltp"] = ltp
        open_trade["unrealized"] = unrealized
        open_trade["sl_distance"] = ltp - stop

        def _try_exit_live_if_needed(reason: str) -> bool:
            position_mode = str(open_trade.get("trade_mode", trade_mode)).upper()
            if position_mode != "REAL":
                return True
            if not _live_orders_permitted(engine, real_mode_armed):
                _push_event(
                    engine,
                    f"{cfg.name} REAL exit blocked for {reason}; local arming and reconciliation are required.",
                )
                return False
            try:
                exit_order_id = _place_market_order(kite, str(open_trade["instrument"]), "SELL", qty)
                open_trade["exit_order_id"] = exit_order_id
                _push_order_log(
                    engine,
                    action=f"EXIT_{reason}",
                    instrument=str(open_trade["instrument"]),
                    side="SELL",
                    quantity=qty,
                    status="PLACED",
                    order_id=exit_order_id,
                    message=f"{cfg.name} live exit order placed",
                )
                return True
            except Exception as error:
                _push_order_log(
                    engine,
                    action=f"EXIT_{reason}",
                    instrument=str(open_trade["instrument"]),
                    side="SELL",
                    quantity=qty,
                    status="FAILED",
                    message=f"{type(error).__name__}: {error}",
                )
                _push_event(engine, f"{cfg.name} live exit failed: {type(error).__name__}: {error}")
                return False

        hard_stop_requested = bool(engine.get("hard_stop_requested", False))
        if hard_stop_requested:
            if not _try_exit_live_if_needed("HARD_STOP"):
                return engine
            pnl = (ltp - entry) * qty
            _complete_persistent_trade(
                cfg, open_trade, exit_price=ltp, realized_pnl=pnl,
                exit_reason="HARD_STOP", exit_time=now_ist,
            )
            engine["realized_pnl"] = _to_float(engine.get("realized_pnl"), 0.0) + pnl
            engine["trades_taken"] = int(engine.get("trades_taken", 0)) + 1
            _push_event(engine, f"{cfg.name} EXIT HARD_STOP {open_trade['instrument']} pnl={pnl:.2f}")
            engine["open_trade"] = None
            engine["hard_stop_requested"] = False
            engine["entry_status"] = "Hard stopped; entries disabled"
            return engine
        elif ltp <= stop:
            if not _try_exit_live_if_needed("STOP_LOSS"):
                return engine
            pnl = (stop - entry) * qty
            _complete_persistent_trade(
                cfg, open_trade, exit_price=stop, realized_pnl=pnl,
                exit_reason="STOP_LOSS", exit_time=now_ist,
            )
            engine["realized_pnl"] = _to_float(engine.get("realized_pnl"), 0.0) + pnl
            engine["trades_taken"] = int(engine.get("trades_taken", 0)) + 1
            _push_event(engine, f"{cfg.name} EXIT STOP_LOSS {open_trade['instrument']} pnl={pnl:.2f}")
            engine["open_trade"] = None
        elif now_ist.time() >= force_exit:
            if not _try_exit_live_if_needed("EOD_CLOSE"):
                return engine
            pnl = (ltp - entry) * qty
            _complete_persistent_trade(
                cfg, open_trade, exit_price=ltp, realized_pnl=pnl,
                exit_reason="EOD_CLOSE", exit_time=now_ist,
            )
            engine["realized_pnl"] = _to_float(engine.get("realized_pnl"), 0.0) + pnl
            engine["trades_taken"] = int(engine.get("trades_taken", 0)) + 1
            _push_event(engine, f"{cfg.name} EXIT EOD_CLOSE {open_trade['instrument']} pnl={pnl:.2f}")
            engine["open_trade"] = None

    if engine.get("open_trade") is not None:
        engine["entry_status"] = "Position open; monitoring stop"
        return engine

    if bool(engine.get("hard_stop_requested", False)):
        engine["hard_stop_requested"] = False
        engine["entry_status"] = "Hard stopped; no open position"
        return engine
    if not bool(engine.get("enabled", True)):
        engine["entry_status"] = "Trade logic is OFF"
        return engine
    if _entries_blocked_for_expiry_day(now_ist.weekday(), cfg.expiry_weekday, expiry_week_offset):
        engine["entry_status"] = "Current-week entries blocked on expiry weekday; select Next week to trade"
        return engine
    if now_ist.time() < entry_start or now_ist.time() > entry_end:
        engine["entry_status"] = f"Outside entry window {entry_start.strftime('%H:%M')}-{entry_end.strftime('%H:%M')}"
        return engine
    if int(engine.get("trades_taken", 0)) >= int(engine.get("max_trades", 1)):
        engine["entry_status"] = "Maximum trades reached"
        return engine
    if session_bars.empty:
        engine["entry_status"] = "No bars received for today's session"
        return engine

    signals = session_bars[(session_bars["cross_up"]) | (session_bars["cross_down"])]
    if signals.empty:
        engine["entry_status"] = "No EMA 3/30 crossover today"
        return engine

    sig = signals.iloc[-1]
    signal_time = pd.Timestamp(sig["timestamp"])
    signal_iso = signal_time.isoformat()
    last_signal = engine.get("last_signal_ts")
    if isinstance(last_signal, str) and last_signal and signal_iso <= last_signal:
        engine["entry_status"] = "Latest crossover already processed"
        return engine

    sig_local_time = signal_time.tz_convert(IST).time() if signal_time.tzinfo is not None else signal_time.time()
    if sig_local_time < entry_start or sig_local_time > entry_end:
        engine["last_signal_ts"] = signal_iso
        engine["entry_status"] = "Latest crossover was outside entry window"
        return engine

    side = "CALL" if bool(sig["cross_up"]) else "PUT"
    spot_price = _to_float(sig["close"], 0.0)
    instrument = _select_option_contract(
        kite,
        cfg,
        side,
        spot_price,
        expiry_week_offset=expiry_week_offset,
    )
    entry_option = _extract_ltp(kite, instrument)
    sl_points = _to_float(engine.get("sl_points"), cfg.default_sl_points)
    stop_price = max(entry_option - sl_points, 0.05)
    qty = int(engine.get("quantity", cfg.default_qty))

    entry_order_id = "PAPER"
    if trade_mode == "REAL":
        if not _live_orders_permitted(engine, real_mode_armed):
            engine["last_signal_ts"] = signal_iso
            _push_event(
                engine,
                f"{cfg.name} signal detected but REAL execution is blocked. No live order placed.",
            )
            return engine
        try:
            entry_order_id = _place_market_order(kite, instrument, "BUY", qty)
            _push_order_log(
                engine,
                action="ENTRY",
                instrument=instrument,
                side="BUY",
                quantity=qty,
                status="PLACED",
                order_id=entry_order_id,
                message=f"{cfg.name} live entry order placed",
            )
        except Exception as error:
            engine["last_signal_ts"] = signal_iso
            _push_order_log(
                engine,
                action="ENTRY",
                instrument=instrument,
                side="BUY",
                quantity=qty,
                status="FAILED",
                message=f"{type(error).__name__}: {error}",
            )
            _push_event(engine, f"{cfg.name} live entry failed: {type(error).__name__}: {error}")
            return engine

    engine["open_trade"] = {
        "side": side,
        "instrument": instrument,
        "entry_time": now_ist.isoformat(),
        "signal_time": signal_iso,
        "entry_spot": spot_price,
        "entry_option": entry_option,
        "quantity": qty,
        "stop_price": stop_price,
        "max_ltp": entry_option,
        "trailing_active": False,
        "next_trail_trigger": entry_option * (1.0 + _to_float(engine.get("trail_after_profit_pct"), 0.50))
        + _to_float(engine.get("trail_trigger_points"), 15.0),
        "ltp": entry_option,
        "unrealized": 0.0,
        "sl_distance": entry_option - stop_price,
        "entry_order_id": entry_order_id,
        "trade_mode": trade_mode,
    }
    _persist_open_trade(cfg, engine["open_trade"])
    engine["last_signal_ts"] = signal_iso
    engine["entry_status"] = f"Position opened from {side} crossover"
    _push_event(
        engine,
        f"{cfg.name} ENTRY {side} {instrument} qty={qty} entry={entry_option:.2f} stop={stop_price:.2f}",
    )
    return engine


def _render_underlying_card(name: str, engine: dict[str, Any]) -> None:
    st.subheader(name)
    left, right = st.columns([1.1, 1.9])
    with left:
        st.metric("Realized P&L", f"{_to_float(engine.get('realized_pnl')):.2f}")
        st.metric("Trades Taken", f"{int(engine.get('trades_taken', 0))}/{int(engine.get('max_trades', 1))}")
        st.caption(f"Engine status: {engine.get('entry_status', 'Waiting')}")
        open_trade = engine.get("open_trade")
        if isinstance(open_trade, dict) and open_trade:
            st.success("Trade: OPEN")
            st.write(f"Instrument: {open_trade.get('instrument','')}")
            st.write(f"Side: {open_trade.get('side','')}")
            st.write(f"Entry: {_to_float(open_trade.get('entry_option')):.2f}")
            st.write(f"Entry order id: {open_trade.get('entry_order_id','')}")
            st.write(f"Mode: {open_trade.get('trade_mode','PAPER')}")
            st.write(f"LTP: {_to_float(open_trade.get('ltp')):.2f}")
            st.write(f"uPnL: {_to_float(open_trade.get('unrealized')):.2f}")
            stop = _to_float(open_trade.get("stop_price"))
            dist = _to_float(open_trade.get("sl_distance"))
            st.write(f"SL: {stop:.2f}")
            st.write(f"SL Trigger: LTP <= {stop:.2f}")
            st.write(f"Distance to SL: {dist:.2f}")
            st.write(f"Trailing: {'ACTIVE' if open_trade.get('trailing_active') else 'WAITING'}")
            st.write(f"Highest LTP: {_to_float(open_trade.get('max_ltp', open_trade.get('entry_option'))):.2f}")
        else:
            st.info("Trade: NO OPEN POSITION")

        events: list[str] = engine.get("events", [])
        if events:
            st.caption("Recent events")
            st.code("\n".join(events[-8:]))

        order_logs = engine.get("order_logs", [])
        if isinstance(order_logs, list) and order_logs:
            st.caption("Order execution logs")
            st.dataframe(pd.DataFrame(order_logs[-8:]), width="stretch", height=220)

    with right:
        bars = engine.get("latest_bars")
        if isinstance(bars, pd.DataFrame) and not bars.empty:
            session_date = pd.Timestamp(bars["timestamp"].iloc[-1]).strftime("%d %b %Y")
            st.caption(f"{session_date} candlesticks | EMA 3 yellow | EMA 30 blue")
            st.altair_chart(_build_price_chart(bars, name), width="stretch")

            latest = bars.iloc[-1]
            ema_fast = _to_float(latest.get("ema_fast"), 0.0)
            ema_slow = _to_float(latest.get("ema_slow"), 0.0)
            ema_gap = ema_fast - ema_slow
            trend_label = "BULLISH" if ema_gap > 0 else "BEARISH" if ema_gap < 0 else "NEUTRAL"
            cross_label = "CROSS UP" if bool(latest.get("cross_up", False)) else "CROSS DOWN" if bool(latest.get("cross_down", False)) else "NO NEW CROSS"

            e1, e2, e3, e4 = st.columns(4)
            with e1:
                st.metric("EMA 3", f"{ema_fast:.2f}")
            with e2:
                st.metric("EMA 30", f"{ema_slow:.2f}")
            with e3:
                st.metric("EMA gap", f"{ema_gap:.2f}")
            with e4:
                st.metric("Trend", trend_label)
            st.caption(f"Latest EMA signal check: {cross_label}")
        else:
            st.caption("No chart data yet.")


@st.fragment(run_every=3, parallel=True)
def _render_live_engine(trade_mode: str, real_mode_armed: bool, expiry_week_offset: int) -> None:
    token = st.session_state.get("dashboard_session_token")
    if not _authentication().validate_session(token, touch=False):
        st.session_state.pop("dashboard_session_token", None)
        st.rerun(scope="app")
    with st.container(border=True):
        st.subheader(":material/monitoring: 3) Live market and trade engine")
        entry_start = _parse_hhmm("09:16")
        entry_end = _parse_hhmm("12:59")
        force_exit = _parse_hhmm("15:25")

        if st.session_state.connected:
            try:
                kite = _get_kite(st.session_state.api_key, st.session_state.access_token)
                _ = kite.profile()
                for name, cfg in UNDERLYINGS.items():
                    state = st.session_state.engine_state[name]
                    st.session_state.engine_state[name] = _run_engine_for(
                        kite=kite,
                        cfg=cfg,
                        engine=state,
                        entry_start=entry_start,
                        entry_end=entry_end,
                        force_exit=force_exit,
                        expiry_week_offset=expiry_week_offset,
                        trade_mode=trade_mode,
                        real_mode_armed=real_mode_armed,
                    )
                _persist_engine_state(st.session_state.engine_state)

                n_state = st.session_state.engine_state["NIFTY"]
                s_state = st.session_state.engine_state["SENSEX"]
                n_upnl = _to_float((n_state.get("open_trade") or {}).get("unrealized"), 0.0)
                s_upnl = _to_float((s_state.get("open_trade") or {}).get("unrealized"), 0.0)
                total_realized = _to_float(n_state.get("realized_pnl"), 0.0) + _to_float(s_state.get("realized_pnl"), 0.0)
                total_unrealized = n_upnl + s_upnl

                m1, m2, m3 = st.columns(3)
                with m1:
                    st.metric("Realized Total P&L", f"{total_realized:.2f}")
                with m2:
                    st.metric("Unrealized Total P&L", f"{total_unrealized:.2f}")
                with m3:
                    st.metric("Grand Total P&L", f"{(total_realized + total_unrealized):.2f}")

                left, right = st.columns(2)
                with left:
                    _render_underlying_card("NIFTY", n_state)
                with right:
                    _render_underlying_card("SENSEX", s_state)
            except Exception as error:
                st.error(f"Live engine error: {type(error).__name__}: {error}")
        else:
            st.warning("Connect first to start live market data + trade engine.")

        st.caption(
            f"Signal/data source: direct Zerodha market data. Durable recovery state: {TRADING_STATE_PATH}."
        )


def _init_session_state() -> None:
    if "connected" not in st.session_state:
        st.session_state.connected = False
    if "api_key" not in st.session_state:
        st.session_state.api_key = ""
    if "access_token" not in st.session_state:
        st.session_state.access_token = ""
    if "login_url" not in st.session_state:
        st.session_state.login_url = ""
    if "login_api_key" not in st.session_state:
        st.session_state.login_api_key = st.session_state.api_key
    if "login_api_secret" not in st.session_state:
        st.session_state.login_api_secret = ""
    if "credential_warning" not in st.session_state:
        st.session_state.credential_warning = ""
    if "engine_state" not in st.session_state:
        st.session_state.engine_state, recovery_metadata = _restore_persistent_engine_state()
        for name, settings in _load_risk_settings().items():
            st.session_state.engine_state[name].update(settings)
        st.session_state.recovery_metadata = recovery_metadata
    else:
        for name, cfg in UNDERLYINGS.items():
            state = st.session_state.engine_state.setdefault(name, {})
            for key, value in _default_state_for(cfg).items():
                state.setdefault(key, value)
    if "nifty_turn_off" not in st.session_state:
        st.session_state.nifty_turn_off = not bool(st.session_state.engine_state["NIFTY"].get("enabled", True))
    if "sensex_turn_off" not in st.session_state:
        st.session_state.sensex_turn_off = not bool(st.session_state.engine_state["SENSEX"].get("enabled", True))
    if "connection_restored" not in st.session_state:
        st.session_state.connection_restored = False
    if "risk_settings_saved_at" not in st.session_state:
        st.session_state.risk_settings_saved_at = {}
    if "recovery_metadata" not in st.session_state:
        st.session_state.recovery_metadata = {}
    if "reconciliation_report" not in st.session_state:
        st.session_state.reconciliation_report = None
    if not st.session_state.connected:
        saved = _load_connection_for_today()
        if saved:
            st.session_state.api_key = saved["api_key"]
            st.session_state.access_token = saved["access_token"]
            try:
                _ = _get_kite(saved["api_key"], saved["access_token"]).profile()
                st.session_state.connected = True
                st.session_state.connection_restored = True
            except Exception:
                _clear_saved_connection()


def _client_context() -> ClientContext:
    try:
        headers = dict(st.context.headers)
    except Exception:
        headers = {}
    return resolve_client_context(
        headers,
        trust_proxy_headers=_env_flag("ZERODHA_TRUST_PROXY_HEADERS"),
    )


def _require_dashboard_login() -> dict[str, object]:
    auth = _authentication()
    try:
        auth.bootstrap_owner_from_environment()
    except ValueError as error:
        st.error(f"OWNER bootstrap configuration error: {error}")
        st.stop()
    token = st.session_state.get("dashboard_session_token")
    session = auth.validate_session(token)
    if session:
        return session

    st.session_state.pop("dashboard_session_token", None)
    st.markdown("## :material/lock: Dashboard sign in")
    if not _database().owner_exists():
        st.error(
            "No OWNER account exists. Set ZERODHA_OWNER_USERNAME and ZERODHA_OWNER_PASSWORD "
            "in the runtime environment, then restart once to bootstrap the owner securely."
        )
        st.stop()

    with st.form("dashboard_login", clear_on_submit=True):
        username = st.text_input("Username or email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", icon=":material/login:", width="stretch")
    if submitted:
        new_token = auth.login(username, password, _client_context())
        if new_token:
            st.session_state.dashboard_session_token = new_token
            st.rerun()
        st.error("Sign-in failed or temporarily rate-limited.")
    st.caption("Sessions expire after 30 minutes idle or 12 hours absolute time.")
    st.stop()


def _format_inr(value: object) -> str:
    return f"₹{_to_float(value):,.2f}"


def _render_positions_page() -> None:
    st.markdown("## :material/account_balance_wallet: Positions")
    for name in UNDERLYINGS:
        state = st.session_state.engine_state[name]
        open_trade = state.get("open_trade")
        with st.container(border=True):
            st.subheader(name)
            if not isinstance(open_trade, dict) or not open_trade:
                st.info("No open position")
                continue
            with st.container(horizontal=True):
                st.metric("Symbol", str(open_trade.get("instrument", "")), border=True)
                st.metric("Quantity", int(open_trade.get("quantity", 0)), border=True)
                st.metric("Entry", _format_inr(open_trade.get("entry_option")), border=True)
                st.metric("LTP", _format_inr(open_trade.get("ltp")), border=True)
                st.metric("Unrealized P&L", _format_inr(open_trade.get("unrealized")), border=True)
            st.caption(f"Mode: {open_trade.get('trade_mode', 'PAPER')} · Entry: {open_trade.get('entry_time', '')}")


def _safe_trade_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "trade_date", "mode", "underlying", "option_symbol", "side", "quantity",
        "entry_time", "exit_time", "entry_price", "exit_price", "realized_pnl", "exit_reason",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns]


def _render_trade_history_page() -> None:
    st.markdown("## :material/history: Trade history")
    left, middle, right = st.columns(3)
    start = left.date_input("From", value=date.today().replace(day=1), key="history_start")
    end = middle.date_input("To", value=date.today(), key="history_end")
    mode = right.segmented_control("Mode", ["All", "PAPER", "REAL"], default="All", key="history_mode")
    underlying = st.segmented_control(
        "Underlying", ["All", "NIFTY", "SENSEX"], default="All", key="history_underlying"
    )
    rows = _database().list_trades(
        start_date=start.isoformat(), end_date=end.isoformat(), mode=str(mode), underlying=str(underlying)
    )
    frame = _safe_trade_frame(rows)
    st.dataframe(
        frame,
        hide_index=True,
        column_config={
            "entry_price": st.column_config.NumberColumn("Entry price", format="₹%.2f"),
            "exit_price": st.column_config.NumberColumn("Exit price", format="₹%.2f"),
            "realized_pnl": st.column_config.NumberColumn("Realized P&L", format="₹%.2f"),
        },
    )


def _load_thresholds() -> PnlThresholds:
    database = _database()
    return PnlThresholds(
        strong_profit=float(database.get_setting("pnl_strong_profit", "3000")),
        strong_loss=float(database.get_setting("pnl_strong_loss", "-3000")),
    )


def _render_pnl_calendar_page() -> None:
    st.markdown("## :material/calendar_month: P&L calendar")
    today = date.today()
    c1, c2, c3, c4 = st.columns(4)
    month = c1.selectbox("Month", list(range(1, 13)), index=today.month - 1, format_func=lambda value: calendar.month_name[value])
    year = int(c2.number_input("Year", min_value=2020, max_value=2100, value=today.year, step=1))
    mode = c3.segmented_control("Mode", ["All", "PAPER", "REAL"], default="All", key="calendar_mode")
    underlying = c4.segmented_control("Underlying", ["All", "NIFTY", "SENSEX"], default="All", key="calendar_underlying")
    start, end = month_bounds(year, int(month))
    rows = _database().daily_pnl(start.isoformat(), end.isoformat(), str(mode), str(underlying))
    thresholds = _load_thresholds()
    summary = monthly_summary(rows, year, int(month))

    with st.container(horizontal=True):
        st.metric("Net P&L", _format_inr(summary["net_pnl"]), border=True)
        st.metric("Trading days", summary["trading_days"], border=True)
        st.metric("Profit / loss days", f"{summary['profit_days']} / {summary['loss_days']}", border=True)
        st.metric("No-trade days", summary["no_trade_days"], border=True)
        st.metric("Win rate", f"{summary['win_rate']:.1f}%", border=True)
    with st.container(horizontal=True):
        best = summary["best_day"] or {}
        worst = summary["worst_day"] or {}
        st.metric("Best day", f"{best.get('trade_date', '—')} · {_format_inr(best.get('total_pnl', 0))}", border=True)
        st.metric("Worst day", f"{worst.get('trade_date', '—')} · {_format_inr(worst.get('total_pnl', 0))}", border=True)
        st.metric("Total trades", summary["total_trades"], border=True)
        st.metric("Winning / losing", f"{summary['winning_trades']} / {summary['losing_trades']}", border=True)

    st.html(calendar_html(year, int(month), rows, thresholds))
    selected = st.date_input("Select a date for trade details", value=start, min_value=start, max_value=end)
    selected_rows = _database().list_trades(
        start_date=selected.isoformat(), end_date=selected.isoformat(), mode=str(mode), underlying=str(underlying)
    )
    selected_daily = next((row for row in rows if row["trade_date"] == selected.isoformat()), {})
    count = int(selected_daily.get("trade_count", 0))
    wins = int(selected_daily.get("winning_trades", 0))
    with st.container(horizontal=True):
        st.metric("Total P&L", _format_inr(selected_daily.get("total_pnl")), border=True)
        st.metric("NIFTY P&L", _format_inr(selected_daily.get("nifty_pnl")), border=True)
        st.metric("SENSEX P&L", _format_inr(selected_daily.get("sensex_pnl")), border=True)
        st.metric("Trades", count, border=True)
        st.metric("Wins / losses", f"{wins} / {int(selected_daily.get('losing_trades', 0))}", border=True)
        st.metric("Win rate", f"{(wins / count * 100.0) if count else 0.0:.1f}%", border=True)
    st.dataframe(
        _safe_trade_frame(selected_rows), hide_index=True,
        column_config={"entry_price": st.column_config.NumberColumn(format="₹%.2f"), "exit_price": st.column_config.NumberColumn(format="₹%.2f"), "realized_pnl": st.column_config.NumberColumn(format="₹%.2f")},
    )


def _render_security_page(session: dict[str, object]) -> None:
    _authentication().require_owner(session)
    st.markdown("## :material/security: Security")
    sessions = _database().active_sessions()
    st.metric("Active sessions", len(sessions), border=True)
    for item in sessions:
        is_current = item["session_id"] == session["session_id"]
        with st.container(border=True):
            st.markdown(f"**{item['username']}** {'· Current session' if is_current else ''}")
            st.write(
                f"Login: {item['created_at']} · Last activity: {item['last_activity']} · "
                f"Approx. location: {item.get('city') or 'Unavailable'}, {item.get('region') or 'Unavailable'}, {item.get('country') or 'Unavailable'}"
            )
            st.caption(
                f"IP: {item.get('ip_address') or 'Unavailable'} · {item.get('browser') or 'Unavailable'} · "
                f"{item.get('operating_system') or 'Unavailable'} · {item.get('device_type') or 'Unavailable'} · Active"
            )
            if st.button("Terminate session", key=f"revoke_{item['session_id'][:12]}", disabled=is_current):
                _authentication().revoke_session(session, str(item["session_id"]))
                st.rerun()
    if st.button("Terminate all other sessions", type="primary"):
        count = _authentication().revoke_all_other_sessions(session)
        st.success(f"Terminated {count} other session(s).")
    st.subheader("Security audit log")
    st.dataframe(pd.DataFrame(_database().security_events()), hide_index=True)


def _render_settings_page(session: dict[str, object]) -> None:
    _authentication().require_owner(session)
    st.markdown("## :material/settings: Owner settings")
    thresholds = _load_thresholds()
    with st.form("pnl_thresholds"):
        strong_profit = st.number_input("Strong profit threshold (INR)", min_value=0.01, value=thresholds.strong_profit)
        strong_loss_magnitude = st.number_input("Strong loss threshold magnitude (INR)", min_value=0.01, value=abs(thresholds.strong_loss))
        save_thresholds = st.form_submit_button("Save P&L thresholds")
    if save_thresholds:
        _database().set_setting("pnl_strong_profit", str(float(strong_profit)), int(session["user_id"]))
        _database().set_setting("pnl_strong_loss", str(-float(strong_loss_magnitude)), int(session["user_id"]))
        _database().log_security_event("PNL_THRESHOLDS_CHANGED", user_id=int(session["user_id"]))
        st.success("P&L thresholds saved.")

    with st.form("owner_password_change", clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirmation = st.text_input("Confirm new password", type="password")
        change_password = st.form_submit_button("Change owner password", type="primary")
    if change_password:
        try:
            revoked = _authentication().change_owner_password(session, current, new, confirmation)
            st.success(f"Password changed. {revoked} other session(s) invalidated.")
        except ValueError as error:
            st.error(str(error))


def main() -> None:
    st.set_page_config(page_title="Zerodha live trade control", page_icon=":material/monitoring:", layout="wide")
    _apply_visual_style()
    session = _require_dashboard_login()
    _init_session_state()

    pages = ["Dashboard / Live trading", "Positions", "Trade history", "P&L calendar"]
    if str(session.get("role", "")).upper() == "OWNER":
        pages.extend(["Security", "Settings"])
    with st.sidebar:
        st.caption(f"Signed in as {session['username']} · {session['role']}")
        selected_page = st.radio("Navigation", pages, key="dashboard_navigation")
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            _authentication().logout(str(st.session_state.dashboard_session_token), _client_context())
            st.session_state.pop("dashboard_session_token", None)
            st.rerun()

    if selected_page == "Positions":
        _render_positions_page()
        return
    if selected_page == "Trade history":
        _render_trade_history_page()
        return
    if selected_page == "P&L calendar":
        _render_pnl_calendar_page()
        return
    if selected_page == "Security":
        _render_security_page(session)
        return
    if selected_page == "Settings":
        _render_settings_page(session)
        return

    st.markdown("## :material/candlestick_chart: Zerodha live trade control center")
    if _is_cloud_runtime():
        st.warning(
            "Cloud safety mode is active: PAPER execution only. REAL broker orders are hard-disabled in this deployment."
        )
    h1, h2 = st.columns([2.2, 1.2])
    with h1:
        st.caption("Direct Zerodha data feed, dual-underlying execution, and in-session risk tracking.")
    with h2:
        now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
        st.caption(f":material/schedule: {now_str}")

    with st.container(border=True):
        st.subheader(":material/vpn_key: 1) API login and connection")
        if st.session_state.connected:
            c1, c2 = st.columns([2.2, 1.0])
            with c1:
                st.success("Connection status: CONNECTED")
                if st.session_state.connection_restored:
                    st.caption("Connected session restored for today. No re-login needed on browser refresh.")
                if st.session_state.credential_warning:
                    st.warning(st.session_state.credential_warning)
            with c2:
                if st.button("Disconnect", width="stretch"):
                    st.session_state.connected = False
                    st.session_state.access_token = ""
                    st.session_state.connection_restored = False
                    _clear_saved_connection()
                    st.warning("Disconnected.")
                    st.rerun()
        else:
            try:
                saved_credentials = _load_login_credentials()
            except RuntimeError as error:
                saved_credentials = None
                st.warning(str(error))

            if saved_credentials:
                saved_left, saved_middle, saved_right = st.columns([2.0, 1.0, 1.0])
                saved_left.info(
                    f"Configured credentials available: API key {_credential_preview(saved_credentials['api_key'])}; API secret hidden."
                )
                if saved_middle.button("Use saved API credentials", width="stretch"):
                    st.session_state.login_api_key = saved_credentials["api_key"]
                    st.session_state.login_api_secret = saved_credentials["api_secret"]
                    st.session_state.api_key = saved_credentials["api_key"]
                    st.rerun()
                if saved_right.button(
                    "Forget saved credentials",
                    width="stretch",
                    disabled=_is_cloud_runtime(),
                    help="Cloud secrets are managed in Azure." if _is_cloud_runtime() else None,
                ):
                    try:
                        _clear_login_credentials()
                        st.session_state.login_api_key = ""
                        st.session_state.login_api_secret = ""
                        st.success("Saved API credentials removed.")
                        st.rerun()
                    except RuntimeError as error:
                        st.error(str(error))

            c1, c2, c3 = st.columns(3)
            with c1:
                api_key = st.text_input("API Key", type="password", key="login_api_key")
            with c2:
                api_secret = st.text_input("API Secret", type="password", key="login_api_secret")
            with c3:
                request_token = st.text_input("Request Token", value="", type="password")

            remember_credentials = st.checkbox(
                "Remember API key and API secret securely on this computer",
                value=not _is_cloud_runtime(),
                disabled=_is_cloud_runtime(),
                help=(
                    "Cloud credentials come from secret-backed environment variables."
                    if _is_cloud_runtime()
                    else "Stores them in Windows Credential Manager, not in the project files."
                ),
            )

            b1, b2 = st.columns(2)
            with b1:
                if st.button("Generate Login Link", width="stretch"):
                    if not api_key.strip():
                        st.error("Paste API key first.")
                    else:
                        st.session_state.login_url = build_kite_login_url(api_key.strip())
                        st.session_state.api_key = api_key.strip()
            with b2:
                if st.button("Connect", width="stretch"):
                    try:
                        if not api_key.strip() or not api_secret.strip() or not request_token.strip():
                            raise ValueError("API key, API secret, and request token are required.")
                        kite = KiteConnect(api_key=api_key.strip())
                        session_data = kite.generate_session(request_token.strip(), api_secret=api_secret.strip())
                        access_token = str(session_data.get("access_token", "")).strip()
                        if not access_token:
                            raise ValueError("No access token returned from Zerodha session response.")
                        check = _get_kite(api_key.strip(), access_token).profile()
                        st.session_state.connected = True
                        st.session_state.api_key = api_key.strip()
                        st.session_state.access_token = access_token
                        _save_connection_for_today(api_key.strip(), access_token)
                        if remember_credentials:
                            try:
                                _save_login_credentials(api_key.strip(), api_secret.strip())
                                st.session_state.credential_warning = ""
                            except RuntimeError as credential_error:
                                st.session_state.credential_warning = str(credential_error)
                        st.session_state.connection_restored = False
                        st.success(f"Connection success: {check.get('user_id', '')}")
                        st.rerun()
                    except Exception as error:
                        st.session_state.connected = False
                        st.error(f"Connection failure: {type(error).__name__}: {error}")

            if st.session_state.login_url:
                st.link_button("Open Zerodha Login Page", st.session_state.login_url, width="stretch")

            st.info("Connection status: NOT CONNECTED")

    with st.container(border=True):
        st.subheader(":material/tune: 2) Trade controls")
        c1, c2, c3 = st.columns(3)
        nifty_enabled = bool(st.session_state.engine_state["NIFTY"].get("enabled", True))
        sensex_enabled = bool(st.session_state.engine_state["SENSEX"].get("enabled", True))

        with c1:
            if st.button(
                "Start NIFTY trade logic",
                width="stretch",
                disabled=bool(st.session_state.engine_state["NIFTY"].get("reconciliation_required")),
            ):
                st.session_state.engine_state["NIFTY"]["enabled"] = True
                st.session_state.nifty_turn_off = False
                nifty_enabled = True
        with c2:
            if st.button(
                "Start SENSEX trade logic",
                width="stretch",
                disabled=bool(st.session_state.engine_state["SENSEX"].get("reconciliation_required")),
            ):
                st.session_state.engine_state["SENSEX"]["enabled"] = True
                st.session_state.sensex_turn_off = False
                sensex_enabled = True
        with c3:
            if st.button("Refresh now", width="stretch"):
                st.rerun()

        o1, o2 = st.columns(2)
        with o1:
            nifty_turn_off = st.toggle(
                "Turn OFF NIFTY",
                key="nifty_turn_off",
                disabled=bool(st.session_state.engine_state["NIFTY"].get("reconciliation_required")),
            )
        with o2:
            sensex_turn_off = st.toggle(
                "Turn OFF SENSEX",
                key="sensex_turn_off",
                disabled=bool(st.session_state.engine_state["SENSEX"].get("reconciliation_required")),
            )

        st.session_state.engine_state["NIFTY"]["enabled"] = (
            not nifty_turn_off
            and not bool(st.session_state.engine_state["NIFTY"].get("reconciliation_required"))
        )
        st.session_state.engine_state["SENSEX"]["enabled"] = (
            not sensex_turn_off
            and not bool(st.session_state.engine_state["SENSEX"].get("reconciliation_required"))
        )
        n_status = "RUNNING" if st.session_state.engine_state["NIFTY"]["enabled"] else "OFF"
        s_status = "RUNNING" if st.session_state.engine_state["SENSEX"]["enabled"] else "OFF"
        st.caption(f"Trade logic status -> NIFTY: {n_status} | SENSEX: {s_status}")

        hard_nifty, hard_sensex = st.columns(2)
        with hard_nifty:
            if st.button("Hard stop NIFTY", type="primary", width="stretch"):
                st.session_state.engine_state["NIFTY"]["enabled"] = False
                st.session_state.engine_state["NIFTY"]["hard_stop_requested"] = True
                st.session_state.nifty_turn_off = True
                _persist_engine_state(st.session_state.engine_state)
                st.rerun()
        with hard_sensex:
            if st.button("Hard stop SENSEX", type="primary", width="stretch"):
                st.session_state.engine_state["SENSEX"]["enabled"] = False
                st.session_state.engine_state["SENSEX"]["hard_stop_requested"] = True
                st.session_state.sensex_turn_off = True
                _persist_engine_state(st.session_state.engine_state)
                st.rerun()
        st.caption("Hard stop closes that index's open position and keeps only that index OFF. REAL exits require REAL confirmation to remain armed.")

        reconciliation_required = any(
            bool(state.get("reconciliation_required"))
            for state in st.session_state.engine_state.values()
        )
        recovery_saved_at = st.session_state.recovery_metadata.get("saved_at", "")
        if reconciliation_required:
            st.error(
                "Recovered broker activity is unverified. Trade logic is OFF until a manual broker reconciliation matches."
            )
        elif recovery_saved_at:
            st.caption(f"Durable state restored from {recovery_saved_at}.")

        if st.button(
            "Reconcile recovered state with Zerodha",
            icon=":material/sync:",
            width="stretch",
            disabled=not st.session_state.connected,
        ):
            try:
                reconciliation_kite = _get_kite(st.session_state.api_key, st.session_state.access_token)
                report = _reconcile_engine_state_with_broker(
                    reconciliation_kite,
                    st.session_state.engine_state,
                )
                st.session_state.reconciliation_report = report
                _persist_engine_state(st.session_state.engine_state)
                st.rerun()
            except Exception as error:
                st.error(f"Broker reconciliation failed: {type(error).__name__}: {error}")

        report = st.session_state.reconciliation_report
        if isinstance(report, dict):
            if report.get("status") == "MATCHED":
                st.success(
                    f"Broker reconciliation matched ({len(report.get('checked_order_ids', []))} order IDs checked)."
                )
            else:
                st.error("Broker reconciliation mismatch: " + "; ".join(report.get("issues", [])))

        mode_col, arm_col = st.columns([1.2, 2.0])
        with mode_col:
            trade_mode = st.segmented_control(
                "Execution mode",
                options=["PAPER", "REAL"],
                default="PAPER",
                key="trade_execution_mode",
            )
        with arm_col:
            real_mode_armed = st.checkbox(
                "I confirm REAL mode should place live orders",
                value=False,
                key="real_mode_armed",
            )

        if trade_mode == "REAL":
            if _is_cloud_runtime():
                st.error("REAL mode is unavailable in cloud deployments. Use a supervised local session after reconciliation.")
            elif reconciliation_required:
                st.error("REAL mode remains blocked until broker reconciliation succeeds.")
            elif real_mode_armed:
                st.warning("REAL mode armed: new ENTRY/EXIT signals will place live market orders.")
            else:
                st.info("REAL mode selected but not armed. Signals will be logged, but no live order is placed.")
        else:
            st.caption("PAPER mode: strategy runs simulation only. No broker order is placed.")
        st.success(f"Selected execution mode: {trade_mode}")

        expiry_mode = st.segmented_control(
            "Expiry selection mode",
            options=["Current week", "Next week"],
            default="Next week",
            key="expiry_mode",
        )
        expiry_week_offset = 1 if expiry_mode == "Next week" else 0
        st.success(f"Selected expiry: {expiry_mode}")
        st.caption(f"Option contract filter: {expiry_mode} expiry for both NIFTY and SENSEX.")

        c5, c6, c7, c8 = st.columns(4)
        with c5:
            nifty_lot_size = LOT_SIZES["NIFTY"]
            nifty_qty = int(st.session_state.engine_state["NIFTY"].get("quantity", 130))
            nifty_lots = _normalize_lots(nifty_qty / float(nifty_lot_size), min_lots=2, lot_step=2)
            selected_nifty_lots = int(
                st.number_input("NIFTY lots", min_value=2, value=nifty_lots, step=2)
            )
            st.session_state.engine_state["NIFTY"]["quantity"] = selected_nifty_lots * nifty_lot_size
            st.caption(f"NIFTY quantity: {st.session_state.engine_state['NIFTY']['quantity']}")
        with c6:
            sensex_lot_size = LOT_SIZES["SENSEX"]
            sensex_qty = int(st.session_state.engine_state["SENSEX"].get("quantity", 40))
            sensex_lots = _normalize_lots(sensex_qty / float(sensex_lot_size), min_lots=2, lot_step=2)
            selected_sensex_lots = int(
                st.number_input("SENSEX lots", min_value=2, value=sensex_lots, step=2)
            )
            st.session_state.engine_state["SENSEX"]["quantity"] = selected_sensex_lots * sensex_lot_size
            st.caption(f"SENSEX quantity: {st.session_state.engine_state['SENSEX']['quantity']}")
        with c7:
            st.session_state.engine_state["NIFTY"]["max_trades"] = int(
                st.number_input("NIFTY Max Trades", min_value=1, value=int(st.session_state.engine_state["NIFTY"].get("max_trades", 3)), step=1)
            )
        with c8:
            st.session_state.engine_state["SENSEX"]["max_trades"] = int(
                st.number_input("SENSEX Max Trades", min_value=1, value=int(st.session_state.engine_state["SENSEX"].get("max_trades", 3)), step=1)
            )

        _, reset_column, _ = st.columns(3)
        with reset_column:
            has_real_recovery = any(
                bool(state.get("reconciliation_required"))
                or (
                    isinstance(state.get("open_trade"), dict)
                    and str(state["open_trade"].get("trade_mode", "PAPER")).upper() == "REAL"
                )
                for state in st.session_state.engine_state.values()
            )
            if st.button(
                "Reset durable PAPER trade state",
                width="stretch",
                disabled=has_real_recovery,
                help="Reconcile broker activity before resetting recovered REAL state." if has_real_recovery else None,
            ):
                st.session_state.engine_state = {
                    name: _default_state_for(cfg) for name, cfg in UNDERLYINGS.items()
                }
                st.session_state.nifty_turn_off = False
                st.session_state.sensex_turn_off = False
                _persist_engine_state(st.session_state.engine_state)
                st.success("Durable PAPER engine state reset for NIFTY and SENSEX.")

        with st.expander("Trailing stop settings", expanded=True):
            for name in UNDERLYINGS:
                state = st.session_state.engine_state[name]
                with st.form(f"{name.lower()}_risk_settings"):
                    st.markdown(f"**{name} risk and trailing values**")
                    t1, t2, t3 = st.columns(3)
                    sl_points = t1.number_input(
                        f"{name} fixed SL points", min_value=1.0,
                        value=_to_float(state.get("sl_points"), UNDERLYINGS[name].default_sl_points), step=1.0,
                    )
                    sl_to_cost_pct = t2.number_input(
                        f"{name} SL to cost %", min_value=1.0, max_value=200.0,
                        value=_to_float(state.get("sl_to_cost_profit_pct"), 0.30) * 100.0, step=1.0,
                    )
                    trail_after_pct = t3.number_input(
                        f"{name} trail starts %", min_value=1.0, max_value=300.0,
                        value=_to_float(state.get("trail_after_profit_pct"), 0.50) * 100.0, step=1.0,
                    )
                    t4, t5, t6 = st.columns(3)
                    giveback_pct = t4.number_input(
                        f"{name} giveback %", min_value=0.0, max_value=100.0,
                        value=_to_float(state.get("mfe_giveback_pct"), 0.275) * 100.0, step=0.5,
                    )
                    trigger_points = t5.number_input(
                        f"{name} trigger points", min_value=1.0,
                        value=_to_float(state.get("trail_trigger_points"), 15.0), step=1.0,
                    )
                    step_points = t6.number_input(
                        f"{name} SL step points", min_value=0.0,
                        value=_to_float(state.get("trail_step_points"), 5.0), step=1.0,
                    )
                    submitted = st.form_submit_button(f"Save {name} settings", width="stretch")

                if submitted:
                    state.update(
                        {
                            "sl_points": float(sl_points),
                            "sl_to_cost_profit_pct": float(sl_to_cost_pct) / 100.0,
                            "trail_after_profit_pct": float(trail_after_pct) / 100.0,
                            "mfe_giveback_pct": float(giveback_pct) / 100.0,
                            "trail_trigger_points": float(trigger_points),
                            "trail_step_points": float(step_points),
                        }
                    )
                    _save_risk_settings(st.session_state.engine_state)
                    st.session_state.risk_settings_saved_at[name] = datetime.now(IST).strftime("%H:%M:%S")

                saved_at = st.session_state.risk_settings_saved_at.get(name)
                if saved_at:
                    st.success(f"{name} settings saved and active at {saved_at} IST.")
                else:
                    st.caption(f"Edit the {name} values, then press Save {name} settings to apply them.")

    _persist_engine_state(st.session_state.engine_state)
    effective_real_mode_armed = (
        bool(real_mode_armed)
        and not _is_cloud_runtime()
        and not any(
            bool(state.get("reconciliation_required"))
            for state in st.session_state.engine_state.values()
        )
    )
    _render_live_engine(str(trade_mode), effective_real_mode_armed, expiry_week_offset)


if __name__ == "__main__":
    main()
