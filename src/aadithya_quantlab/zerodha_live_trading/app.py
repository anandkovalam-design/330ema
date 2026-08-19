from __future__ import annotations

import json
import os
import calendar
import html
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
    acknowledge_hard_stop_requests,
    load_daily_connection,
    load_engine_state,
    load_hard_stop_requests,
    request_hard_stop,
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
HARD_STOP_COMMAND_PATH = runtime_path("hard_stop_commands.json")
CREDENTIAL_SERVICE_NAME = "aadithya-zerodha-live-trading"
API_KEY_ACCOUNT = "api-key"
API_SECRET_ACCOUNT = "api-secret"
ENTRY_START_IST = "09:16"
ENTRY_CUTOFF_IST = "15:00"
MANDATORY_EXIT_IST = "15:15"
STRATEGY_EMA = "EMA_3_30"
STRATEGY_HEIKIN_ASHI = "HEIKIN_ASHI_REVERSAL"
HEIKIN_ASHI_PROFIT_GATE_POINTS = 10.0
HEIKIN_ASHI_STOP_POINTS = 20.0
DEFAULT_INDEX_EXPIRY_MODE = "Current week"


@st.cache_resource
def _database() -> Database:
    return Database()


@st.cache_resource
def _authentication() -> AuthenticationService:
    return AuthenticationService(_database())


@dataclass(frozen=True)
class UnderlyingConfig:
    name: str
    spot_instrument_token: int | None
    option_symbol_prefix: str
    option_exchange: str
    expiry_weekday: int | None
    default_qty: int
    default_sl_points: float
    default_lots: int = 1
    lot_step: int = 1
    risk_point_multiplier: float = 1.0
    signal_source: str = "SPOT"
    entry_start_ist: str = ENTRY_START_IST
    entry_cutoff_ist: str = ENTRY_CUTOFF_IST
    mandatory_exit_ist: str = MANDATORY_EXIT_IST


UNDERLYINGS: dict[str, UnderlyingConfig] = {
    "NIFTY": UnderlyingConfig(
        name="NIFTY",
        spot_instrument_token=256265,
        option_symbol_prefix="NIFTY",
        option_exchange="NFO",
        expiry_weekday=1,
        default_qty=130,
        default_sl_points=20.0,
        default_lots=2,
        lot_step=2,
    ),
    "SENSEX": UnderlyingConfig(
        name="SENSEX",
        spot_instrument_token=265,
        option_symbol_prefix="SENSEX",
        option_exchange="BFO",
        expiry_weekday=3,
        default_qty=40,
        default_sl_points=50.0,
        default_lots=2,
        lot_step=2,
    ),
    "CRUDEOIL": UnderlyingConfig(
        name="CRUDEOIL", spot_instrument_token=None, option_symbol_prefix="CRUDEOIL",
        option_exchange="MCX", expiry_weekday=None, default_qty=1, default_sl_points=20.0,
        signal_source="FRONT_FUTURE", entry_start_ist="09:00", entry_cutoff_ist="22:30",
        mandatory_exit_ist="22:50",
    ),
    "NATURALGAS": UnderlyingConfig(
        name="NATURALGAS", spot_instrument_token=None, option_symbol_prefix="NATURALGAS",
        option_exchange="MCX", expiry_weekday=None, default_qty=1, default_sl_points=20.0,
        signal_source="FRONT_FUTURE", entry_start_ist="09:00", entry_cutoff_ist="22:30",
        mandatory_exit_ist="22:50",
    ),
    "GOLD": UnderlyingConfig(
        name="GOLD", spot_instrument_token=None, option_symbol_prefix="GOLD",
        option_exchange="MCX", expiry_weekday=None, default_qty=1, default_sl_points=120.0,
        risk_point_multiplier=6.0,
        signal_source="FRONT_FUTURE", entry_start_ist="09:00", entry_cutoff_ist="22:30",
        mandatory_exit_ist="22:50",
    ),
    "SILVER": UnderlyingConfig(
        name="SILVER", spot_instrument_token=None, option_symbol_prefix="SILVER",
        option_exchange="MCX", expiry_weekday=None, default_qty=1, default_sl_points=200.0,
        risk_point_multiplier=10.0,
        signal_source="FRONT_FUTURE", entry_start_ist="09:00", entry_cutoff_ist="22:30",
        mandatory_exit_ist="22:50",
    ),
}

INDEX_NAMES = ("NIFTY", "SENSEX")
COMMODITY_NAMES = ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER")
INDEX_QUOTE_INSTRUMENTS = {
    "NIFTY": "NSE:NIFTY 50",
    "SENSEX": "BSE:SENSEX",
}

# MCX exposes broker order quantity as contracts (normally lot_size=1), while
# each standard contract represents the physical quantity below.  Keep these
# display multipliers separate from the quantity sent to Kite.
MCX_CONTRACT_SPECS: dict[str, tuple[int, str]] = {
    "CRUDEOIL": (100, "barrels"),
    "NATURALGAS": (1250, "MMBtu"),
    "GOLD": (100, "x 10 g = 1 kg"),
    "SILVER": (30, "kg"),
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

UNIVERSAL_TRAIL_START_PCT = 0.30

NATURAL_GAS_RISK_DEFAULTS = {
    "sl_points": 2.0,
    "sl_to_cost_profit_pct": 0.25,
    "trail_after_profit_pct": UNIVERSAL_TRAIL_START_PCT,
    "mfe_giveback_pct": 0.275,
    "trail_trigger_points": 3.0,
    "trail_step_points": 1.0,
}

LEGACY_NATURAL_GAS_RISK_DEFAULTS = {
    "sl_points": 20.0,
    "sl_to_cost_profit_pct": 0.30,
    "trail_after_profit_pct": 0.50,
    "mfe_giveback_pct": 0.275,
    "trail_trigger_points": 15.0,
    "trail_step_points": 5.0,
}


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


def _cloud_live_orders_enabled() -> bool:
    """Require an explicit deployment-level opt-in for REAL cloud orders."""

    return _is_cloud_runtime() and _env_flag("ZERODHA_ALLOW_REAL_TRADING")


def _runtime_allows_live_orders() -> bool:
    return not _is_cloud_runtime() or _cloud_live_orders_enabled()


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


def _sync_scaled_metal_risk_settings(engine_state: dict[str, dict[str, Any]]) -> bool:
    """Keep GOLD at 6x and SILVER at 10x NIFTY point settings."""
    nifty = engine_state.get("NIFTY")
    if not isinstance(nifty, dict):
        return False
    point_keys = ("sl_points", "trail_trigger_points", "trail_step_points")
    percentage_keys = ("sl_to_cost_profit_pct", "trail_after_profit_pct", "mfe_giveback_pct")
    changed = False
    for name in ("GOLD", "SILVER"):
        state = engine_state.get(name)
        if not isinstance(state, dict):
            continue
        multiplier = UNDERLYINGS[name].risk_point_multiplier
        expected = {
            **{key: _to_float(nifty.get(key), 0.0) * multiplier for key in point_keys},
            **{key: _to_float(nifty.get(key), 0.0) for key in percentage_keys},
        }
        if any(abs(_to_float(state.get(key), value) - value) >= 1e-9 for key, value in expected.items()):
            state.update(expected)
            changed = True
    return changed


def _migrate_natural_gas_risk_defaults(engine_state: dict[str, dict[str, Any]]) -> bool:
    state = engine_state.get("NATURALGAS")
    if not isinstance(state, dict):
        return False
    if any(
        abs(_to_float(state.get(key), value) - value) >= 1e-9
        for key, value in LEGACY_NATURAL_GAS_RISK_DEFAULTS.items()
    ):
        return False
    state.update(NATURAL_GAS_RISK_DEFAULTS)
    return True


def _enforce_universal_trail_start(engine_state: dict[str, dict[str, Any]]) -> bool:
    changed = False
    for state in engine_state.values():
        if not isinstance(state, dict):
            continue
        if abs(_to_float(state.get("trail_after_profit_pct"), 0.0) - UNIVERSAL_TRAIL_START_PCT) >= 1e-9:
            state["trail_after_profit_pct"] = UNIVERSAL_TRAIL_START_PCT
            changed = True
    return changed


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


def _exchange_zerodha_request_token(
    api_key: str,
    api_secret: str,
    request_token: str,
) -> tuple[str, dict[str, Any]]:
    kite = KiteConnect(api_key=api_key)
    session_data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = str(session_data.get("access_token", "")).strip()
    if not access_token:
        raise ValueError("No access token returned from Zerodha session response.")
    profile = dict(_get_kite(api_key, access_token).profile())
    return access_token, profile


def _complete_zerodha_login_callback() -> bool:
    request_token = str(st.query_params.get("request_token", "")).strip()
    if not request_token:
        return False

    try:
        credentials = _load_login_credentials()
        if not credentials:
            raise RuntimeError("Configure the Zerodha API key and API secret before authenticating.")
        access_token, profile = _exchange_zerodha_request_token(
            credentials["api_key"],
            credentials["api_secret"],
            request_token,
        )
        st.session_state["connected"] = True
        st.session_state["api_key"] = credentials["api_key"]
        st.session_state["access_token"] = access_token
        st.session_state["connection_restored"] = False
        st.session_state["zerodha_login_notice"] = (
            f"Zerodha authentication successful: {profile.get('user_id', '')}"
        )
        _save_connection_for_today(credentials["api_key"], access_token)
        return True
    except Exception as error:
        st.session_state["connected"] = False
        st.session_state["zerodha_login_notice"] = (
            f"Zerodha authentication failed: {type(error).__name__}: {error}"
        )
        return False
    finally:
        st.query_params.clear()


def _render_same_tab_zerodha_login(url: str) -> None:
    safe_url = html.escape(url, quote=True)
    st.html(
        f"""
        <a href="{safe_url}" target="_self" style="
            align-items:center;background:#16883f;border:1px solid #29b35a;
            border-radius:6px;color:#ffffff;display:flex;font-weight:700;
            justify-content:center;min-height:42px;text-decoration:none;width:100%;
        ">Authenticate with Zerodha</a>
        """
    )


def _cached_exchange_instruments(kite: KiteConnect, exchange: str) -> list[dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = st.session_state.setdefault("instrument_master_cache", {})
    now = datetime.now(IST)
    cached = cache.get(exchange)
    if isinstance(cached, dict):
        fetched_at = pd.to_datetime(cached.get("fetched_at"), errors="coerce")
        if not pd.isna(fetched_at):
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.tz_localize(IST)
            if now - fetched_at.to_pydatetime() < timedelta(hours=6):
                rows = cached.get("rows")
                if isinstance(rows, list):
                    return rows
    rows = list(kite.instruments(exchange))
    cache[exchange] = {"fetched_at": now.isoformat(), "rows": rows}
    return rows


def _row_matches_underlying(row: dict[str, Any], cfg: UnderlyingConfig) -> bool:
    row_name = str(row.get("name", "")).strip().upper()
    if row_name:
        return row_name == cfg.name
    symbol = str(row.get("tradingsymbol", "")).strip().upper()
    if not symbol.startswith(cfg.option_symbol_prefix):
        return False
    suffix = symbol[len(cfg.option_symbol_prefix) :]
    return bool(suffix) and suffix[0].isdigit()


def _front_future_row(
    cfg: UnderlyingConfig,
    instrument_rows: list[dict[str, Any]],
    expiry_offset: int = 0,
) -> dict[str, Any]:
    session_date = datetime.now(IST).date()
    candidates: list[tuple[date, str, dict[str, Any]]] = []
    for row in instrument_rows:
        symbol = str(row.get("tradingsymbol", ""))
        if not _row_matches_underlying(row, cfg):
            continue
        if str(row.get("instrument_type", "")).upper() != "FUT":
            continue
        expiry = pd.to_datetime(row.get("expiry"), errors="coerce")
        if pd.isna(expiry) or expiry.date() < session_date:
            continue
        candidates.append((expiry.date(), symbol, row))
    if not candidates:
        raise ValueError(f"No live {cfg.name} MCX futures contract found.")
    expiry_dates = sorted({item[0] for item in candidates})
    selected_offset = max(int(expiry_offset), 0)
    if selected_offset >= len(expiry_dates):
        raise ValueError(f"No {'next-month' if selected_offset else 'current-month'} {cfg.name} MCX futures contract found.")
    selected_expiry = expiry_dates[selected_offset]
    selected = [item for item in candidates if item[0] == selected_expiry]
    return sorted(selected, key=lambda item: item[1])[0][2]


def _signal_instrument(
    cfg: UnderlyingConfig,
    instrument_rows: list[dict[str, Any]] | None = None,
    expiry_offset: int = 0,
) -> tuple[int, str]:
    if cfg.signal_source == "SPOT":
        if cfg.spot_instrument_token is None:
            raise ValueError(f"No spot instrument token configured for {cfg.name}.")
        return int(cfg.spot_instrument_token), cfg.name
    future = _front_future_row(cfg, instrument_rows or [], expiry_offset=expiry_offset)
    return int(future["instrument_token"]), str(future["tradingsymbol"])


def _fetch_spot_5m(
    kite: KiteConnect,
    cfg: UnderlyingConfig,
    instrument_rows: list[dict[str, Any]] | None = None,
    expiry_offset: int = 0,
) -> pd.DataFrame:
    now_ist = datetime.now(IST)
    start_dt = datetime.combine(now_ist.date() - timedelta(days=7), wall_time(9, 0), tzinfo=IST)
    instrument_token, signal_symbol = _signal_instrument(cfg, instrument_rows, expiry_offset=expiry_offset)
    rows = kite.historical_data(
        instrument_token,
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
    frame["signal_symbol"] = signal_symbol
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
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


def _compute_heikin_ashi(frame: pd.DataFrame) -> pd.DataFrame:
    """Add standard Heikin-Ashi OHLC and colour columns to chronological bars."""

    bars = frame.copy().sort_values("timestamp").reset_index(drop=True)
    if bars.empty:
        for column in ("ha_open", "ha_high", "ha_low", "ha_close", "ha_color"):
            bars[column] = pd.Series(dtype="object" if column == "ha_color" else "float64")
        return bars

    bars["ha_close"] = bars[["open", "high", "low", "close"]].mean(axis=1)
    ha_open: list[float] = [(_to_float(bars.iloc[0]["open"]) + _to_float(bars.iloc[0]["close"])) / 2.0]
    for index in range(1, len(bars)):
        ha_open.append((ha_open[-1] + _to_float(bars.iloc[index - 1]["ha_close"])) / 2.0)
    bars["ha_open"] = ha_open
    bars["ha_high"] = bars[["high", "ha_open", "ha_close"]].max(axis=1)
    bars["ha_low"] = bars[["low", "ha_open", "ha_close"]].min(axis=1)
    bars["ha_color"] = "NEUTRAL"
    bars.loc[bars["ha_close"] > bars["ha_open"], "ha_color"] = "GREEN"
    bars.loc[bars["ha_close"] < bars["ha_open"], "ha_color"] = "RED"
    return bars


def _completed_5m_bars(frame: pd.DataFrame, now_ist: datetime) -> pd.DataFrame:
    """Exclude the currently forming five-minute candle."""

    if frame.empty:
        return frame.copy()
    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    cutoff = pd.Timestamp(now_ist)
    if timestamps.dt.tz is None:
        cutoff = cutoff.tz_localize(None)
    else:
        cutoff = cutoff.tz_convert(timestamps.dt.tz)
    candle_close_times = timestamps + pd.Timedelta(5, unit="min")
    return frame.loc[timestamps.notna() & (candle_close_times <= cutoff)].copy()


def _latest_confirmed_heikin_ashi_signal(bars: pd.DataFrame) -> pd.Series | None:
    """Return the second new-colour candle only for an immediate 2-candle reversal."""

    if len(bars) < 3 or "ha_color" not in bars.columns:
        return None
    for end in range(len(bars), 2, -1):
        candidate = bars.iloc[end - 3 : end]
        colours = candidate["ha_color"].astype(str).tolist()
        if colours == ["RED", "GREEN", "GREEN"] or colours == ["GREEN", "RED", "RED"]:
            return candidate.iloc[-1]
    return None


def _has_opposite_heikin_ashi_candle(
    bars: pd.DataFrame,
    entry_color: str,
    signal_time: pd.Timestamp,
) -> bool:
    later = bars.loc[pd.to_datetime(bars["timestamp"], errors="coerce") > signal_time]
    if later.empty:
        return False
    opposite = "RED" if entry_color == "GREEN" else "GREEN"
    return str(later.iloc[-1].get("ha_color", "NEUTRAL")) == opposite


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
        y=alt.Y("low:Q", title=f"{name} signal price", scale=alt.Scale(zero=False)),
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


def _build_heikin_ashi_chart(bars: pd.DataFrame, name: str) -> alt.LayerChart:
    chart_data = bars[["timestamp", "ha_open", "ha_high", "ha_low", "ha_close", "ha_color"]].copy()
    base = alt.Chart(chart_data).encode(
        x=alt.X("timestamp:T", title="Time (IST)", axis=alt.Axis(format="%H:%M", labelAngle=-45)),
        tooltip=[
            alt.Tooltip("timestamp:T", title="Time", format="%d %b %H:%M"),
            alt.Tooltip("ha_open:Q", title="HA Open", format=",.2f"),
            alt.Tooltip("ha_high:Q", title="HA High", format=",.2f"),
            alt.Tooltip("ha_low:Q", title="HA Low", format=",.2f"),
            alt.Tooltip("ha_close:Q", title="HA Close", format=",.2f"),
            alt.Tooltip("ha_color:N", title="Colour"),
        ],
    )
    candle_color = alt.condition(
        "datum.ha_color === 'GREEN'",
        alt.value("#3fff86"),
        alt.value("#ff5f6d"),
    )
    wicks = base.mark_rule(strokeWidth=1).encode(
        y=alt.Y("ha_low:Q", title=f"{name} Heikin-Ashi price", scale=alt.Scale(zero=False)),
        y2="ha_high:Q",
        color=candle_color,
    )
    bodies = base.mark_bar(size=5).encode(
        y=alt.Y("ha_open:Q", scale=alt.Scale(zero=False)),
        y2="ha_close:Q",
        color=candle_color,
    )
    return alt.layer(wicks, bodies).properties(height=360).interactive()


def _next_expiry_weekday(day: datetime.date, expiry_weekday: int) -> datetime.date:
    days_ahead = (expiry_weekday - day.weekday()) % 7
    return day + timedelta(days=days_ahead)


def _select_option_contract(
    kite: KiteConnect,
    cfg: UnderlyingConfig,
    side: str,
    spot_price: float,
    expiry_week_offset: int = 0,
    instrument_rows: list[dict[str, Any]] | None = None,
) -> str:
    option_rows = instrument_rows if instrument_rows is not None else kite.instruments(cfg.option_exchange)
    option_type = "CE" if side == "CALL" else "PE"
    session_date = datetime.now(IST).date()
    target_expiry: date | None = None
    if cfg.expiry_weekday is not None:
        base_expiry = _next_expiry_weekday(session_date, cfg.expiry_weekday)
        target_expiry = base_expiry + timedelta(days=7 * max(int(expiry_week_offset), 0))

    pool: list[tuple[date, float, str, int]] = []
    for row in option_rows:
        symbol = str(row.get("tradingsymbol", ""))
        if not _row_matches_underlying(row, cfg):
            continue
        if str(row.get("instrument_type", "")) != option_type:
            continue
        expiry = pd.to_datetime(row.get("expiry"), errors="coerce")
        if pd.isna(expiry):
            continue
        expiry_date = expiry.date()
        if expiry_date < session_date:
            continue
        strike = _to_float(row.get("strike"), 0.0)
        if strike <= 0:
            continue
        lot_size = int(_to_float(row.get("lot_size"), 0.0))
        if lot_size <= 0:
            continue
        pool.append((expiry_date, abs(strike - spot_price), symbol, lot_size))

    if not pool:
        raise ValueError(f"No eligible {cfg.name} {option_type} contract found.")

    if target_expiry is not None:
        exact_expiry = [r for r in pool if r[0] == target_expiry]
        if exact_expiry:
            candidates = exact_expiry
        else:
            post_target = [r for r in pool if r[0] >= target_expiry]
            candidates = post_target if post_target else pool
    else:
        expiry_dates = sorted({row[0] for row in pool})
        selected_offset = max(int(expiry_week_offset), 0)
        if selected_offset >= len(expiry_dates):
            raise ValueError(
                f"No {'next-month' if selected_offset else 'current-month'} eligible "
                f"{cfg.name} {option_type} contract found."
            )
        selected_expiry = expiry_dates[selected_offset]
        candidates = [row for row in pool if row[0] == selected_expiry]
    _, _, symbol, _ = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
    return f"{cfg.option_exchange}:{symbol}"


def _contract_lot_size(instrument: str, instrument_rows: list[dict[str, Any]]) -> int:
    _, symbol = _split_instrument(instrument)
    for row in instrument_rows:
        if str(row.get("tradingsymbol", "")) == symbol:
            lot_size = int(_to_float(row.get("lot_size"), 0.0))
            if lot_size > 0:
                return lot_size
    raise ValueError(f"No valid lot size found for {instrument}.")


def _mcx_order_quantity(
    instrument: str,
    instrument_rows: list[dict[str, Any]],
    requested_lots: int,
) -> tuple[int, int, int]:
    """Return (lots, Zerodha lot_size, broker order quantity) for an MCX contract."""

    lots = max(1, int(requested_lots))
    broker_lot_size = _contract_lot_size(instrument, instrument_rows)
    return lots, broker_lot_size, lots * broker_lot_size


def _trade_pnl_quantity(cfg: UnderlyingConfig, open_trade: dict[str, Any]) -> int:
    """Return the price-unit multiplier used for option premium P&L."""

    broker_quantity = max(1, int(open_trade.get("quantity", 1)))
    if cfg.option_exchange != "MCX":
        return broker_quantity
    contract_multiplier, _ = MCX_CONTRACT_SPECS[cfg.name]
    return broker_quantity * contract_multiplier


def _mcx_contract_size_label(name: str, lots: int) -> str:
    normalized_lots = max(1, int(lots))
    contract_multiplier, contract_unit = MCX_CONTRACT_SPECS[name]
    if name == "GOLD":
        return f"{normalized_lots * contract_multiplier} x 10 g = {normalized_lots} kg"
    return f"{normalized_lots * contract_multiplier} {contract_unit}"


def _mcx_position_size_details(
    name: str,
    state: dict[str, Any],
    open_trade: dict[str, Any],
) -> tuple[int, int, str]:
    broker_quantity = max(1, int(open_trade.get("quantity", 1)))
    broker_lot_size = max(
        1,
        int(open_trade.get("exchange_lot_size", state.get("lot_size", 1)) or 1),
    )
    inferred_lots = max(1, broker_quantity // broker_lot_size)
    lots = max(1, int(open_trade.get("lots", inferred_lots)))
    return lots, broker_quantity, _mcx_contract_size_label(name, lots)


def _extract_ltp(kite: KiteConnect, instrument: str) -> float:
    payload = kite.ltp(instrument)
    info = payload.get(instrument)
    if not isinstance(info, dict):
        raise ValueError(f"No LTP for {instrument}")
    ltp = _to_float(info.get("last_price"), 0.0)
    if ltp <= 0:
        raise ValueError(f"Invalid LTP for {instrument}")
    return ltp


def _quote_ltp(instrument: str, quote: dict[str, Any]) -> float:
    ltp = _to_float(quote.get("last_price"), 0.0)
    if ltp <= 0:
        raise ValueError(f"Invalid LTP for {instrument}")
    return ltp


def _best_depth_price(quote: dict[str, Any], side: str) -> float:
    depth = quote.get("depth")
    rows = depth.get(side, []) if isinstance(depth, dict) else []
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return 0.0
    return _to_float(rows[0].get("price"), 0.0)


def _apply_open_trade_quote(
    open_trade: dict[str, Any],
    instrument: str,
    quote: dict[str, Any],
) -> float:
    ltp = _quote_ltp(instrument, quote)
    open_trade["quote_timestamp"] = quote.get("timestamp")
    open_trade["last_trade_time"] = quote.get("last_trade_time")
    open_trade["best_bid"] = _best_depth_price(quote, "buy")
    open_trade["best_ask"] = _best_depth_price(quote, "sell")
    return ltp


def _collect_live_quote_instruments(
    engine_state: dict[str, dict[str, Any]],
    mcx_rows: list[dict[str, Any]],
    commodity_expiry_offset: int,
) -> list[str]:
    instruments = set(INDEX_QUOTE_INSTRUMENTS.values())
    for name in COMMODITY_NAMES:
        future = _front_future_row(
            UNDERLYINGS[name],
            mcx_rows,
            expiry_offset=commodity_expiry_offset,
        )
        instruments.add(f"MCX:{future['tradingsymbol']}")
    for state in engine_state.values():
        for key in ("open_trade", "ha_open_trade"):
            trade = state.get(key)
            if isinstance(trade, dict) and str(trade.get("instrument", "")).strip():
                instruments.add(str(trade["instrument"]).strip())
    return sorted(instruments)


def _refresh_live_signal_quote(
    kite: KiteConnect,
    cfg: UnderlyingConfig,
    engine: dict[str, Any],
    quote_snapshot: dict[str, dict[str, Any]] | None = None,
) -> None:
    if cfg.name in INDEX_QUOTE_INSTRUMENTS:
        instrument = INDEX_QUOTE_INSTRUMENTS[cfg.name]
    else:
        signal_symbol = str(engine.get("signal_symbol", "")).strip()
        instrument = f"MCX:{signal_symbol}" if signal_symbol else ""
    if not instrument:
        return
    try:
        quote = (quote_snapshot or {}).get(instrument)
        engine["live_signal_ltp"] = (
            _quote_ltp(instrument, quote)
            if isinstance(quote, dict)
            else _extract_ltp(kite, instrument)
        )
        engine["live_signal_time"] = datetime.now(IST)
        engine["live_signal_error"] = ""
    except Exception as error:
        engine["live_signal_error"] = f"{type(error).__name__}: {error}"


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
    risk_defaults = {
        "sl_points": cfg.default_sl_points,
        "sl_to_cost_profit_pct": 0.30,
        "trail_after_profit_pct": UNIVERSAL_TRAIL_START_PCT,
        "mfe_giveback_pct": 0.275,
        "trail_trigger_points": 15.0 * cfg.risk_point_multiplier,
        "trail_step_points": 5.0 * cfg.risk_point_multiplier,
    }
    if cfg.name == "NATURALGAS":
        risk_defaults.update(NATURAL_GAS_RISK_DEFAULTS)
    return {
        "enabled": True,
        "strategy_mode": STRATEGY_EMA,
        "hard_stop_requested": False,
        "last_signal_ts": None,
        "open_trade": None,
        "realized_pnl": 0.0,
        "events": [],
        "order_logs": [],
        "quantity": cfg.default_qty,
        "lots": cfg.default_lots,
        **risk_defaults,
        "max_trades": 3,
        "trades_taken": 0,
        "entry_status": "Waiting for market data",
        "reconciliation_required": False,
        "reconciliation_status": "NOT_REQUIRED",
        "ha_last_signal_ts": None,
        "ha_open_trade": None,
        "ha_realized_pnl": 0.0,
        "ha_events": [],
        "ha_order_logs": [],
        "ha_trades_taken": 0,
        "ha_entry_status": "Waiting for Heikin-Ashi data",
    }


def _parallel_heikin_ashi_state(
    engine: dict[str, Any],
    cfg: UnderlyingConfig,
    *,
    hard_stop_requested: bool = False,
) -> dict[str, Any]:
    """Build the independent PAPER state used by the parallel HA engine."""

    state = _default_state_for(cfg)
    state.update(
        {
            "enabled": bool(engine.get("enabled", True)),
            "strategy_mode": STRATEGY_HEIKIN_ASHI,
            "hard_stop_requested": hard_stop_requested,
            "last_signal_ts": engine.get("ha_last_signal_ts"),
            "open_trade": engine.get("ha_open_trade"),
            "realized_pnl": _to_float(engine.get("ha_realized_pnl"), 0.0),
            "events": list(engine.get("ha_events", [])),
            "order_logs": list(engine.get("ha_order_logs", [])),
            "quantity": int(engine.get("quantity", cfg.default_qty)),
            "lots": int(engine.get("lots", cfg.default_lots)),
            "lot_size": engine.get("lot_size"),
            "trades_taken": int(engine.get("ha_trades_taken", 0)),
            "entry_status": str(engine.get("ha_entry_status", "Waiting for Heikin-Ashi data")),
            "reconciliation_required": False,
            "reconciliation_status": "NOT_REQUIRED",
        }
    )
    return state


def _merge_parallel_heikin_ashi_state(engine: dict[str, Any], state: dict[str, Any]) -> None:
    """Copy durable and display fields from the independent HA engine."""

    engine.update(
        {
            "ha_last_signal_ts": state.get("last_signal_ts"),
            "ha_open_trade": state.get("open_trade"),
            "ha_realized_pnl": _to_float(state.get("realized_pnl"), 0.0),
            "ha_events": list(state.get("events", [])),
            "ha_order_logs": list(state.get("order_logs", [])),
            "ha_trades_taken": int(state.get("trades_taken", 0)),
            "ha_entry_status": str(state.get("entry_status", "Waiting for Heikin-Ashi data")),
        }
    )
    if isinstance(state.get("latest_ha_bars"), pd.DataFrame):
        engine["latest_ha_bars"] = state["latest_ha_bars"]


def _persist_engine_state(engine_state: dict[str, dict[str, Any]]) -> None:
    save_engine_state(
        TRADING_STATE_PATH,
        session_date=_today_ist_iso(),
        engine_state=engine_state,
        saved_at=datetime.now(IST),
    )


def _apply_pending_hard_stops(engine_state: dict[str, dict[str, Any]]) -> set[str]:
    pending = set(load_hard_stop_requests(HARD_STOP_COMMAND_PATH))
    for name in pending:
        state = engine_state.get(name)
        if not isinstance(state, dict):
            continue
        state["enabled"] = False
        state["hard_stop_requested"] = True
    return pending


def _request_hard_stop_from_controls(name: str) -> None:
    engine_state = st.session_state["engine_state"]
    state = engine_state[name]
    state["enabled"] = False
    state["hard_stop_requested"] = True
    st.session_state[f"{name.lower()}_turn_off"] = True
    request_hard_stop(
        HARD_STOP_COMMAND_PATH,
        underlying=name,
        requested_at=datetime.now(IST),
    )
    _persist_engine_state(engine_state)


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
    strategy = str(open_trade.get("strategy_mode") or STRATEGY_EMA)
    trade_key = (
        f"REAL:{entry_order_id}"
        if mode == "REAL" and entry_order_id
        else f"PAPER:{strategy}:{cfg.name}:{open_trade['instrument']}:{entry_time}"
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
            "strategy": strategy,
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
        strategy=str(open_trade.get("strategy_mode") or STRATEGY_EMA),
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
        and _runtime_allows_live_orders()
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
    trail_after_pct = max(sl_to_cost_pct, UNIVERSAL_TRAIL_START_PCT)
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
    instrument_rows: list[dict[str, Any]] | None = None,
    market_bars: pd.DataFrame | None = None,
    quote_snapshot: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now_ist = datetime.now(IST)

    bars = (
        market_bars.copy()
        if isinstance(market_bars, pd.DataFrame)
        else _fetch_spot_5m(kite, cfg, instrument_rows, expiry_offset=expiry_week_offset)
    )
    if bars.empty:
        engine["entry_status"] = "No market data returned"
        return engine

    bars = _compute_signals(bars)
    session_bars = bars.loc[bars["timestamp"].dt.date == now_ist.date()].copy()
    heikin_ashi_bars = _compute_heikin_ashi(bars)
    session_heikin_ashi = heikin_ashi_bars.loc[
        heikin_ashi_bars["timestamp"].dt.date == now_ist.date()
    ].copy()
    completed_heikin_ashi = _completed_5m_bars(session_heikin_ashi, now_ist)
    display_bars = session_bars if not session_bars.empty else bars.loc[bars["timestamp"].dt.date == bars["timestamp"].dt.date.max()]
    display_heikin_ashi = (
        session_heikin_ashi
        if not session_heikin_ashi.empty
        else heikin_ashi_bars.loc[
            heikin_ashi_bars["timestamp"].dt.date == heikin_ashi_bars["timestamp"].dt.date.max()
        ]
    )
    latest = display_bars.iloc[-1]
    latest_time = pd.Timestamp(latest["timestamp"])
    latest_close = _to_float(latest["close"], 0.0)
    engine["latest_bars"] = display_bars.tail(80).copy()
    engine["latest_ha_bars"] = display_heikin_ashi.tail(80).copy()
    engine["latest_spot"] = latest_close
    engine["latest_time"] = latest_time
    if "signal_symbol" in display_bars.columns:
        engine["signal_symbol"] = str(display_bars.iloc[-1]["signal_symbol"])
    _refresh_live_signal_quote(kite, cfg, engine, quote_snapshot)

    open_trade = engine.get("open_trade")
    if isinstance(open_trade, dict) and open_trade:
        instrument = str(open_trade["instrument"])
        quote = (quote_snapshot or {}).get(instrument)
        ltp = (
            _apply_open_trade_quote(open_trade, instrument, quote)
            if isinstance(quote, dict)
            else _extract_ltp(kite, instrument)
        )
        qty = int(open_trade["quantity"])
        pnl_qty = _trade_pnl_quantity(cfg, open_trade)
        entry = _to_float(open_trade["entry_option"], 0.0)
        is_heikin_ashi_trade = str(open_trade.get("strategy_mode", STRATEGY_EMA)) == STRATEGY_HEIKIN_ASHI
        trail_stage = None
        if is_heikin_ashi_trade:
            open_trade["max_ltp"] = max(_to_float(open_trade.get("max_ltp"), entry), ltp)
            if (
                not bool(open_trade.get("profit_gate_reached", False))
                and ltp - entry >= HEIKIN_ASHI_PROFIT_GATE_POINTS
            ):
                open_trade["profit_gate_reached"] = True
                _push_event(
                    engine,
                    f"{cfg.name} Heikin-Ashi +{HEIKIN_ASHI_PROFIT_GATE_POINTS:g} point gate reached; holding until colour reversal.",
                )
        else:
            trail_stage = _update_trailing_stop(open_trade, engine, ltp)
        stop = _to_float(open_trade["stop_price"], 0.0)
        if trail_stage:
            _push_event(engine, f"{cfg.name} {trail_stage} raised SL to {stop:.2f} at LTP {ltp:.2f}")

        unrealized = (ltp - entry) * pnl_qty
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
            pnl = (ltp - entry) * pnl_qty
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
            pnl = (stop - entry) * pnl_qty
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
            pnl = (ltp - entry) * pnl_qty
            _complete_persistent_trade(
                cfg, open_trade, exit_price=ltp, realized_pnl=pnl,
                exit_reason="EOD_CLOSE", exit_time=now_ist,
            )
            engine["realized_pnl"] = _to_float(engine.get("realized_pnl"), 0.0) + pnl
            engine["trades_taken"] = int(engine.get("trades_taken", 0)) + 1
            _push_event(engine, f"{cfg.name} EXIT EOD_CLOSE {open_trade['instrument']} pnl={pnl:.2f}")
            engine["open_trade"] = None
        elif is_heikin_ashi_trade and _has_opposite_heikin_ashi_candle(
            completed_heikin_ashi,
            str(open_trade.get("entry_ha_color", "")),
            pd.Timestamp(open_trade["signal_time"]),
        ):
            if not _try_exit_live_if_needed("HA_COLOR_REVERSAL"):
                return engine
            pnl = (ltp - entry) * pnl_qty
            _complete_persistent_trade(
                cfg, open_trade, exit_price=ltp, realized_pnl=pnl,
                exit_reason="HA_COLOR_REVERSAL", exit_time=now_ist,
            )
            engine["realized_pnl"] = _to_float(engine.get("realized_pnl"), 0.0) + pnl
            engine["trades_taken"] = int(engine.get("trades_taken", 0)) + 1
            _push_event(
                engine,
                f"{cfg.name} EXIT HA_COLOR_REVERSAL {open_trade['instrument']} pnl={pnl:.2f}",
            )
            engine["open_trade"] = None

    if engine.get("open_trade") is not None:
        open_strategy = str(engine["open_trade"].get("strategy_mode", STRATEGY_EMA))
        engine["entry_status"] = (
            "Heikin-Ashi position open; monitoring fixed stop and colour reversal"
            if open_strategy == STRATEGY_HEIKIN_ASHI
            else "Position open; monitoring stop"
        )
        return engine

    if bool(engine.get("hard_stop_requested", False)):
        engine["hard_stop_requested"] = False
        engine["entry_status"] = "Hard stopped; no open position"
        return engine
    if not bool(engine.get("enabled", True)):
        engine["entry_status"] = "Trade logic is OFF"
        return engine
    if (
        cfg.expiry_weekday is not None
        and _entries_blocked_for_expiry_day(now_ist.weekday(), cfg.expiry_weekday, expiry_week_offset)
    ):
        engine["entry_status"] = "Current-week entries blocked on expiry weekday; select Next week to trade"
        return engine
    if now_ist.time() < entry_start or now_ist.time() > entry_end:
        engine["entry_status"] = (
            f"Outside entry window {entry_start.strftime('%H:%M')}-"
            f"{entry_end.strftime('%H:%M')} IST"
        )
        return engine
    strategy_mode = str(engine.get("strategy_mode", STRATEGY_EMA))
    use_heikin_ashi = cfg.name in INDEX_NAMES and strategy_mode == STRATEGY_HEIKIN_ASHI
    if use_heikin_ashi and str(trade_mode).upper() != "PAPER":
        engine["entry_status"] = "Heikin-Ashi test strategy is PAPER-only"
        return engine
    if not use_heikin_ashi and int(engine.get("trades_taken", 0)) >= int(engine.get("max_trades", 1)):
        engine["entry_status"] = "Maximum trades reached"
        return engine
    if session_bars.empty:
        engine["entry_status"] = "No bars received for today's session"
        return engine

    if use_heikin_ashi:
        sig = _latest_confirmed_heikin_ashi_signal(completed_heikin_ashi)
        if sig is None:
            engine["entry_status"] = "Waiting for a confirmed two-candle Heikin-Ashi reversal"
            return engine
        side = "CALL" if str(sig["ha_color"]) == "GREEN" else "PUT"
    else:
        signals = session_bars[(session_bars["cross_up"]) | (session_bars["cross_down"])]
        if signals.empty:
            engine["entry_status"] = "No EMA 3/30 crossover today"
            return engine
        sig = signals.iloc[-1]
        side = "CALL" if bool(sig["cross_up"]) else "PUT"
    signal_time = pd.Timestamp(sig["timestamp"])
    signal_iso = signal_time.isoformat()
    last_signal = engine.get("last_signal_ts")
    if isinstance(last_signal, str) and last_signal and signal_iso <= last_signal:
        engine["entry_status"] = "Latest strategy signal already processed"
        return engine

    sig_local_time = signal_time.tz_convert(IST).time() if signal_time.tzinfo is not None else signal_time.time()
    if sig_local_time < entry_start or sig_local_time > entry_end:
        engine["last_signal_ts"] = signal_iso
        engine["entry_status"] = "Latest strategy signal was outside entry window"
        return engine

    spot_price = _to_float(sig["close"], 0.0)
    instrument = _select_option_contract(
        kite,
        cfg,
        side,
        spot_price,
        expiry_week_offset=expiry_week_offset,
        instrument_rows=instrument_rows,
    )
    entry_option = _extract_ltp(kite, instrument)
    sl_points = (
        HEIKIN_ASHI_STOP_POINTS
        if use_heikin_ashi
        else _to_float(engine.get("sl_points"), cfg.default_sl_points)
    )
    stop_price = max(entry_option - sl_points, 0.05)
    selected_lots = max(1, int(engine.get("lots", cfg.default_lots)))
    if cfg.signal_source == "FRONT_FUTURE":
        if not instrument_rows:
            raise ValueError(f"MCX instrument master is unavailable for {cfg.name}.")
        selected_lots, lot_size, qty = _mcx_order_quantity(
            instrument,
            instrument_rows,
            selected_lots,
        )
        engine["quantity"] = qty
        engine["lot_size"] = lot_size
    else:
        qty = int(engine.get("quantity", cfg.default_qty))
        lot_size = LOT_SIZES.get(cfg.name, max(1, qty // selected_lots))

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
        "lots": selected_lots,
        "exchange_lot_size": lot_size,
        "stop_price": stop_price,
        "max_ltp": entry_option,
        "trailing_active": False,
        "next_trail_trigger": entry_option * (1.0 + _to_float(engine.get("trail_after_profit_pct"), 0.50))
        + _to_float(engine.get("trail_trigger_points"), 15.0),
        "ltp": entry_option,
        "unrealized": 0.0,
        "sl_distance": entry_option - stop_price,
        "entry_order_id": entry_order_id,
        "trade_mode": "PAPER" if use_heikin_ashi else trade_mode,
        "strategy_mode": STRATEGY_HEIKIN_ASHI if use_heikin_ashi else STRATEGY_EMA,
    }
    if use_heikin_ashi:
        engine["open_trade"].update(
            {
                "entry_ha_color": str(sig["ha_color"]),
                "profit_gate_points": HEIKIN_ASHI_PROFIT_GATE_POINTS,
                "profit_gate_reached": False,
            }
        )
    _persist_open_trade(cfg, engine["open_trade"])
    engine["last_signal_ts"] = signal_iso
    engine["entry_status"] = (
        f"PAPER position opened after confirmed {sig['ha_color']} Heikin-Ashi reversal"
        if use_heikin_ashi
        else f"Position opened from {side} crossover"
    )
    _push_event(
        engine,
        f"{cfg.name} {'HA ' if use_heikin_ashi else ''}ENTRY {side} {instrument} "
        f"qty={qty} entry={entry_option:.2f} stop={stop_price:.2f}",
    )
    return engine


def _run_parallel_index_engines(
    kite: KiteConnect,
    cfg: UnderlyingConfig,
    engine: dict[str, Any],
    entry_start: wall_time,
    entry_end: wall_time,
    force_exit: wall_time,
    expiry_week_offset: int,
    trade_mode: str,
    real_mode_armed: bool,
    quote_snapshot: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run EMA in the selected mode and HA independently in PAPER mode."""

    shared_market_bars = _fetch_spot_5m(
        kite,
        cfg,
        expiry_offset=expiry_week_offset,
    )

    if str(engine.get("strategy_mode")) == STRATEGY_HEIKIN_ASHI:
        if not engine.get("ha_last_signal_ts"):
            engine["ha_last_signal_ts"] = engine.get("last_signal_ts")
        if engine.get("open_trade"):
            if not engine.get("ha_open_trade"):
                engine["ha_open_trade"] = engine.get("open_trade")
            engine["open_trade"] = None
        engine["last_signal_ts"] = datetime.now(IST).isoformat()

    engine["strategy_mode"] = STRATEGY_EMA
    hard_stop_requested = bool(engine.get("hard_stop_requested", False))
    engine = _run_engine_for(
        kite=kite,
        cfg=cfg,
        engine=engine,
        entry_start=entry_start,
        entry_end=entry_end,
        force_exit=force_exit,
        expiry_week_offset=expiry_week_offset,
        trade_mode=trade_mode,
        real_mode_armed=real_mode_armed,
        market_bars=shared_market_bars,
        quote_snapshot=quote_snapshot,
    )
    ha_state = _parallel_heikin_ashi_state(
        engine,
        cfg,
        hard_stop_requested=hard_stop_requested,
    )
    ha_state = _run_engine_for(
        kite=kite,
        cfg=cfg,
        engine=ha_state,
        entry_start=entry_start,
        entry_end=entry_end,
        force_exit=force_exit,
        expiry_week_offset=expiry_week_offset,
        trade_mode="PAPER",
        real_mode_armed=False,
        market_bars=shared_market_bars,
        quote_snapshot=quote_snapshot,
    )
    _merge_parallel_heikin_ashi_state(engine, ha_state)
    return engine


def _render_underlying_card(name: str, engine: dict[str, Any]) -> None:
    st.subheader(name)
    cfg = UNDERLYINGS[name]
    signal_symbol = str(engine.get("signal_symbol", name))
    parallel_heikin_ashi = name in INDEX_NAMES
    strategy_label = "EMA 3/30 + Heikin-Ashi reversal (parallel PAPER)" if parallel_heikin_ashi else "EMA 3/30"
    st.caption(
        f"Signal: {signal_symbol} · {strategy_label} · entries to {cfg.entry_cutoff_ist} IST · "
        f"exit {cfg.mandatory_exit_ist} IST"
    )
    left, right = st.columns([1.1, 1.9])
    with left:
        combined_realized = _to_float(engine.get("realized_pnl")) + _to_float(engine.get("ha_realized_pnl"))
        st.metric("Combined realized P&L", f"{combined_realized:.2f}")
        trade_count = f"EMA {int(engine.get('trades_taken', 0))}/{int(engine.get('max_trades', 1))}"
        if parallel_heikin_ashi:
            trade_count += f" · HA {int(engine.get('ha_trades_taken', 0))}/unlimited"
        st.metric("Trades taken", trade_count)
        st.caption(f"EMA status: {engine.get('entry_status', 'Waiting')}")
        open_trade = engine.get("open_trade")
        if isinstance(open_trade, dict) and open_trade:
            st.success("EMA trade: OPEN")
            st.write(f"Instrument: {open_trade.get('instrument','')}")
            st.write(f"Side: {open_trade.get('side','')}")
            st.write(f"Entry: {_to_float(open_trade.get('entry_option')):.2f}")
            st.write(f"Entry order id: {open_trade.get('entry_order_id','')}")
            st.write(f"Mode: {open_trade.get('trade_mode','PAPER')}")
            trade_quantity = int(open_trade.get("quantity", 0))
            if cfg.option_exchange == "MCX":
                broker_lot_size = int(
                    open_trade.get("exchange_lot_size", engine.get("lot_size", 1))
                )
                inferred_lots = max(1, trade_quantity // max(1, broker_lot_size))
                trade_lots = int(open_trade.get("lots", inferred_lots))
                st.write(f"MCX lots: {trade_lots}")
                st.write(f"Contract size: {_mcx_contract_size_label(cfg.name, trade_lots)}")
                st.write(f"Broker order quantity: {trade_quantity}")
                st.caption(
                    f"Zerodha instrument-master lot_size: {broker_lot_size}. "
                    "Contract size is informational; Kite receives the broker order quantity."
                )
            else:
                st.write(f"Quantity: {trade_quantity}")
            st.write(f"LTP: {_to_float(open_trade.get('ltp')):.2f}")
            st.write(
                f"Best bid / ask: {_to_float(open_trade.get('best_bid')):.2f} / "
                f"{_to_float(open_trade.get('best_ask')):.2f}"
            )
            if open_trade.get("last_trade_time"):
                st.caption(
                    f"Last trade {_format_ist_timestamp(open_trade.get('last_trade_time'))} · "
                    f"quote {_format_ist_timestamp(open_trade.get('quote_timestamp'))}"
                )
            st.write(f"uPnL: {_to_float(open_trade.get('unrealized')):.2f}")
            stop = _to_float(open_trade.get("stop_price"))
            dist = _to_float(open_trade.get("sl_distance"))
            st.write(f"SL: {stop:.2f}")
            st.write(f"SL Trigger: LTP <= {stop:.2f}")
            st.write(f"Distance to SL: {dist:.2f}")
            st.write(f"Trailing: {'ACTIVE' if open_trade.get('trailing_active') else 'WAITING'}")
            st.write(f"Highest LTP: {_to_float(open_trade.get('max_ltp', open_trade.get('entry_option'))):.2f}")
        else:
            st.info("EMA trade: NO OPEN POSITION")

        if parallel_heikin_ashi:
            st.caption(f"Heikin-Ashi status: {engine.get('ha_entry_status', 'Waiting')}")
            ha_trade = engine.get("ha_open_trade")
            if isinstance(ha_trade, dict) and ha_trade:
                st.success("Heikin-Ashi PAPER trade: OPEN")
                st.write(f"Instrument: {ha_trade.get('instrument', '')}")
                st.write(f"Side: {ha_trade.get('side', '')}")
                st.write(f"Entry: {_to_float(ha_trade.get('entry_option')):.2f}")
                st.write(f"LTP: {_to_float(ha_trade.get('ltp')):.2f}")
                st.write(f"uPnL: {_to_float(ha_trade.get('unrealized')):.2f}")
                st.write(f"SL: {_to_float(ha_trade.get('stop_price')):.2f}")
                st.write(f"Entry HA colour: {ha_trade.get('entry_ha_color', '')}")
                st.write(
                    f"+10 point gate: {'REACHED' if ha_trade.get('profit_gate_reached') else 'WAITING'}"
                )
                st.write("Exit: first opposite closed HA candle or 15:15 IST")
            else:
                st.info("Heikin-Ashi PAPER trade: NO OPEN POSITION")

            ha_events = engine.get("ha_events", [])
            if isinstance(ha_events, list) and ha_events:
                st.caption("Recent Heikin-Ashi events (IST)")
                st.code("\n".join(ha_events[-8:]))

        events: list[str] = engine.get("events", [])
        if events:
            st.caption("Recent events (IST)")
            st.code("\n".join(events[-8:]))

        order_logs = engine.get("order_logs", [])
        if isinstance(order_logs, list) and order_logs:
            st.caption("Order execution logs (IST)")
            st.dataframe(
                pd.DataFrame(order_logs[-8:]),
                width="stretch",
                height=220,
                column_config={"time": st.column_config.TextColumn("Time (IST)")},
            )

    with right:
        bars = engine.get("latest_bars")
        if isinstance(bars, pd.DataFrame) and not bars.empty:
            session_date = pd.Timestamp(bars["timestamp"].iloc[-1]).strftime("%d %b %Y")
            latest = bars.iloc[-1]
            if name in COMMODITY_NAMES:
                quote_left, quote_right = st.columns(2)
                with quote_left:
                    st.metric(
                        "Live futures LTP",
                        f"{_to_float(engine.get('live_signal_ltp')):.2f}",
                    )
                    live_signal_time = engine.get("live_signal_time")
                    if live_signal_time:
                        st.caption(f"Quote updated {_format_ist_timestamp(live_signal_time)}")
                with quote_right:
                    st.metric("Last 5-minute candle", f"{_to_float(latest.get('close')):.2f}")
                    st.caption(f"Candle time {_format_ist_timestamp(latest.get('timestamp'))}")
                live_signal_error = str(engine.get("live_signal_error", "")).strip()
                if live_signal_error:
                    st.warning(f"Live futures quote unavailable: {live_signal_error}")
            st.caption(f"{session_date} candlesticks | EMA 3 yellow | EMA 30 blue")
            st.altair_chart(_build_price_chart(bars, name), width="stretch")
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

        if parallel_heikin_ashi:
            ha_bars = engine.get("latest_ha_bars")
            if isinstance(ha_bars, pd.DataFrame) and not ha_bars.empty:
                session_date = pd.Timestamp(ha_bars["timestamp"].iloc[-1]).strftime("%d %b %Y")
                latest_ha = ha_bars.iloc[-1]
                st.caption(
                    f"{session_date} · parallel 5-minute Heikin-Ashi PAPER strategy · "
                    "second candle confirms"
                )
                st.altair_chart(_build_heikin_ashi_chart(ha_bars, name), width="stretch")
                h1, h2, h3 = st.columns(3)
                with h1:
                    st.metric("HA colour", str(latest_ha.get("ha_color", "NEUTRAL")))
                with h2:
                    st.metric("HA open", f"{_to_float(latest_ha.get('ha_open')):.2f}")
                with h3:
                    st.metric("HA close", f"{_to_float(latest_ha.get('ha_close')):.2f}")


@st.fragment(run_every=5)
def _render_live_engine(
    trade_mode: str,
    real_mode_armed: bool,
    expiry_week_offset: int,
    commodity_expiry_offset: int,
) -> None:
    token = st.session_state.get("dashboard_session_token")
    if not _authentication().validate_session(token, touch=False):
        st.session_state.pop("dashboard_session_token", None)
        st.rerun(scope="app")
    with st.container(border=True):
        st.subheader(":material/monitoring: Live market and trade engine")
        st.caption(
            f"Entry window: {ENTRY_START_IST}-{ENTRY_CUTOFF_IST} IST (15:00 inclusive) · "
            f"mandatory exit: {MANDATORY_EXIT_IST} IST. MCX entries: 09:00-22:30 IST · mandatory exit: 22:50 IST."
        )

        if st.session_state.connected:
            try:
                kite = _get_kite(st.session_state.api_key, st.session_state.access_token)
                _ = kite.profile()
                mcx_rows = _cached_exchange_instruments(kite, "MCX")
                quote_instruments = _collect_live_quote_instruments(
                    st.session_state.engine_state,
                    mcx_rows,
                    commodity_expiry_offset,
                )
                quote_snapshot = kite.quote(*quote_instruments)
                pending_hard_stops = _apply_pending_hard_stops(st.session_state.engine_state)
                for name, cfg in UNDERLYINGS.items():
                    state = st.session_state.engine_state[name]
                    contract_expiry_offset = (
                        expiry_week_offset if cfg.expiry_weekday is not None else commodity_expiry_offset
                    )
                    if name in INDEX_NAMES:
                        state = _run_parallel_index_engines(
                            kite=kite,
                            cfg=cfg,
                            engine=state,
                            entry_start=_parse_hhmm(cfg.entry_start_ist),
                            entry_end=_parse_hhmm(cfg.entry_cutoff_ist),
                            force_exit=_parse_hhmm(cfg.mandatory_exit_ist),
                            expiry_week_offset=contract_expiry_offset,
                            trade_mode=trade_mode,
                            real_mode_armed=real_mode_armed,
                            quote_snapshot=quote_snapshot,
                        )
                    else:
                        state["strategy_mode"] = STRATEGY_EMA
                        state = _run_engine_for(
                            kite=kite,
                            cfg=cfg,
                            engine=state,
                            entry_start=_parse_hhmm(cfg.entry_start_ist),
                            entry_end=_parse_hhmm(cfg.entry_cutoff_ist),
                            force_exit=_parse_hhmm(cfg.mandatory_exit_ist),
                            expiry_week_offset=contract_expiry_offset,
                            trade_mode=trade_mode,
                            real_mode_armed=real_mode_armed,
                            instrument_rows=mcx_rows,
                            quote_snapshot=quote_snapshot,
                        )
                    st.session_state.engine_state[name] = state
                completed_hard_stops = {
                    name
                    for name in pending_hard_stops
                    if name in st.session_state.engine_state
                    and not bool(st.session_state.engine_state[name].get("hard_stop_requested", False))
                }
                if completed_hard_stops:
                    acknowledge_hard_stop_requests(HARD_STOP_COMMAND_PATH, completed_hard_stops)
                _persist_engine_state(st.session_state.engine_state)

                total_realized = sum(
                    _to_float(state.get("realized_pnl"), 0.0)
                    + _to_float(state.get("ha_realized_pnl"), 0.0)
                    for state in st.session_state.engine_state.values()
                )
                total_unrealized = sum(
                    _to_float((state.get("open_trade") or {}).get("unrealized"), 0.0)
                    + _to_float((state.get("ha_open_trade") or {}).get("unrealized"), 0.0)
                    for state in st.session_state.engine_state.values()
                )

                m1, m2, m3 = st.columns(3)
                with m1:
                    st.metric("Realized Total P&L", f"{total_realized:.2f}")
                with m2:
                    st.metric("Unrealized Total P&L", f"{total_unrealized:.2f}")
                with m3:
                    st.metric("Grand Total P&L", f"{(total_realized + total_unrealized):.2f}")

                names = list(UNDERLYINGS)
                for start in range(0, len(names), 2):
                    columns = st.columns(2)
                    for column, name in zip(columns, names[start : start + 2]):
                        with column:
                            _render_underlying_card(name, st.session_state.engine_state[name])
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
    if "zerodha_login_notice" not in st.session_state:
        st.session_state.zerodha_login_notice = ""
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
    if _migrate_natural_gas_risk_defaults(st.session_state.engine_state):
        _save_risk_settings(st.session_state.engine_state)
    if _sync_scaled_metal_risk_settings(st.session_state.engine_state):
        _save_risk_settings(st.session_state.engine_state)
    if _enforce_universal_trail_start(st.session_state.engine_state):
        _save_risk_settings(st.session_state.engine_state)
    for name in UNDERLYINGS:
        toggle_key = f"{name.lower()}_turn_off"
        if toggle_key not in st.session_state:
            st.session_state[toggle_key] = not bool(
                st.session_state.engine_state[name].get("enabled", True)
            )
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


def _has_real_trade_recovery(engine_state: dict[str, dict[str, Any]]) -> bool:
    return any(
        bool(state.get("reconciliation_required"))
        or (
            isinstance(state.get("open_trade"), dict)
            and str(state["open_trade"].get("trade_mode", "PAPER")).upper() == "REAL"
        )
        for state in engine_state.values()
    )


def _reset_durable_paper_trade_state() -> None:
    """Reset PAPER state in a pre-rerun callback so widget keys remain writable."""

    if _has_real_trade_recovery(st.session_state.engine_state):
        st.session_state.paper_reset_error = (
            "Recovered REAL activity must be reconciled before PAPER state can be reset."
        )
        return
    st.session_state.engine_state = {
        name: _default_state_for(cfg) for name, cfg in UNDERLYINGS.items()
    }
    for name in UNDERLYINGS:
        st.session_state[f"{name.lower()}_turn_off"] = False
    st.session_state.recovery_metadata = {}
    st.session_state.reconciliation_report = None
    _persist_engine_state(st.session_state.engine_state)
    st.session_state.paper_reset_completed = True


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
        if _is_cloud_runtime():
            st.error(
                "No OWNER account exists. Set ZERODHA_OWNER_USERNAME and ZERODHA_OWNER_PASSWORD "
                "as deployment secrets, then restart once to bootstrap the owner securely."
            )
            st.stop()
        st.info("First local run: create the OWNER account. The password is hashed before storage.")
        with st.form("local_owner_bootstrap", clear_on_submit=True):
            owner_username = st.text_input("OWNER username or email")
            owner_password = st.text_input("OWNER password", type="password")
            owner_confirmation = st.text_input("Confirm OWNER password", type="password")
            create_owner = st.form_submit_button(
                "Create OWNER account", type="primary", icon=":material/admin_panel_settings:", width="stretch"
            )
        if create_owner:
            try:
                auth.bootstrap_local_owner(owner_username, owner_password, owner_confirmation)
                st.success("OWNER account created. Sign in with the new credentials.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))
        st.stop()

    with st.form("dashboard_login", clear_on_submit=True):
        username = st.text_input("Username or email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", icon=":material/login:", width="stretch")
    if submitted:
        new_token = auth.login(username, password, _client_context())
        if new_token:
            st.session_state.dashboard_session_token = new_token
            st.session_state.pop("dashboard_navigation", None)
            st.rerun()
        st.error("Sign-in failed or temporarily rate-limited.")
    st.caption("Sessions expire after 30 minutes idle or 12 hours absolute time.")
    st.stop()


def _format_inr(value: object) -> str:
    return f"₹{_to_float(value):,.2f}"


def _format_ist_timestamp(value: object) -> str:
    """Format stored timestamps consistently for dashboard display in IST."""

    if value is None or str(value).strip() == "":
        return "—"
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(timestamp):
        return "—"
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(IST)
    else:
        timestamp = timestamp.tz_convert(IST)
    return timestamp.strftime("%d %b %Y, %I:%M:%S %p IST")


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
                if name in COMMODITY_NAMES:
                    lots, broker_quantity, contract_size = _mcx_position_size_details(
                        name, state, open_trade
                    )
                    st.metric("MCX lots", f"{lots} lot{'s' if lots != 1 else ''}", border=True)
                    st.metric("Broker quantity", broker_quantity, border=True)
                    st.metric("Contract size", contract_size, border=True)
                else:
                    st.metric("Lots", int(open_trade.get("lots", 0)), border=True)
                    st.metric("Broker quantity", int(open_trade.get("quantity", 0)), border=True)
            with st.container(horizontal=True):
                st.metric("Entry", _format_inr(open_trade.get("entry_option")), border=True)
                st.metric("LTP", _format_inr(open_trade.get("ltp")), border=True)
                st.metric("Unrealized P&L", _format_inr(open_trade.get("unrealized")), border=True)
            st.caption(
                f"Mode: {open_trade.get('trade_mode', 'PAPER')} · "
                f"Entry: {_format_ist_timestamp(open_trade.get('entry_time'))}"
            )


def _safe_trade_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "trade_date", "mode", "strategy", "underlying", "option_symbol", "side", "quantity",
        "entry_time", "exit_time", "entry_price", "exit_price", "realized_pnl", "exit_reason",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows)
    if "strategy" not in frame.columns:
        frame["strategy"] = STRATEGY_EMA
    strategy_labels = {
        STRATEGY_EMA: "EMA 3/30",
        STRATEGY_HEIKIN_ASHI: "Heikin-Ashi reversal",
        "LEGACY_UNKNOWN": "Legacy trade (strategy not recorded)",
    }
    frame["strategy"] = frame["strategy"].fillna(STRATEGY_EMA).map(
        lambda value: strategy_labels.get(str(value), str(value))
    )
    frame = frame[columns]
    for column in ("entry_time", "exit_time"):
        frame[column] = frame[column].map(_format_ist_timestamp)
    return frame


def _render_trade_history_page() -> None:
    st.markdown("## :material/history: Trade history")
    left, middle, right = st.columns(3)
    today_ist = datetime.now(IST).date()
    start = left.date_input("From", value=today_ist.replace(day=1), key="history_start")
    end = middle.date_input("To", value=today_ist, key="history_end")
    mode = right.segmented_control("Mode", ["All", "PAPER", "REAL"], default="All", key="history_mode")
    underlying = st.segmented_control(
        "Underlying", ["All", *UNDERLYINGS.keys()], default="All", key="history_underlying"
    )
    rows = _database().list_trades(
        start_date=start.isoformat(), end_date=end.isoformat(), mode=str(mode), underlying=str(underlying)
    )
    frame = _safe_trade_frame(rows)
    st.dataframe(
        frame,
        hide_index=True,
        column_config={
            "strategy": st.column_config.TextColumn("Strategy", pinned=True),
            "entry_time": st.column_config.TextColumn("Entry time (IST)"),
            "exit_time": st.column_config.TextColumn("Exit time (IST)"),
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


def _render_pnl_calendar_page(asset_class: str) -> None:
    is_index = asset_class.upper() == "INDEX"
    asset_names = INDEX_NAMES if is_index else COMMODITY_NAMES
    page_label = "Index" if is_index else "Commodity"
    st.markdown(f"## :material/calendar_month: {page_label} P&L")
    today = datetime.now(IST).date()
    c1, c2, c3, c4 = st.columns(4)
    month = c1.selectbox("Month", list(range(1, 13)), index=today.month - 1, format_func=lambda value: calendar.month_name[value])
    year = int(c2.number_input("Year", min_value=2020, max_value=2100, value=today.year, step=1))
    mode = c3.segmented_control(
        "Mode", ["All", "PAPER", "REAL"], default="All", key=f"calendar_mode_{asset_class.lower()}"
    )
    underlying = c4.selectbox(
        "Underlying",
        [f"All {page_label.lower()}", *asset_names],
        key=f"calendar_underlying_{asset_class.lower()}",
    )
    query_underlying = asset_class.upper() if str(underlying).startswith("All ") else str(underlying)
    start, end = month_bounds(year, int(month))
    rows = _database().daily_pnl(start.isoformat(), end.isoformat(), str(mode), query_underlying)
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
    selected = st.date_input(
        "Select a date for trade details", value=start, min_value=start, max_value=end,
        key=f"calendar_selected_date_{asset_class.lower()}",
    )
    selected_rows = _database().list_trades(
        start_date=selected.isoformat(), end_date=selected.isoformat(), mode=str(mode),
        underlying="ALL" if str(underlying).startswith("All ") else str(underlying),
    )
    selected_rows = [row for row in selected_rows if str(row.get("underlying")) in asset_names]
    selected_daily = next((row for row in rows if row["trade_date"] == selected.isoformat()), {})
    count = int(selected_daily.get("trade_count", 0))
    wins = int(selected_daily.get("winning_trades", 0))
    with st.container(horizontal=True):
        st.metric("Total P&L", _format_inr(selected_daily.get("total_pnl")), border=True)
        for name in asset_names:
            st.metric(f"{name} P&L", _format_inr(selected_daily.get(f"{name.lower()}_pnl")), border=True)
        st.metric("Trades", count, border=True)
        st.metric("Wins / losses", f"{wins} / {int(selected_daily.get('losing_trades', 0))}", border=True)
        st.metric("Win rate", f"{(wins / count * 100.0) if count else 0.0:.1f}%", border=True)
    st.dataframe(
        _safe_trade_frame(selected_rows), hide_index=True,
        column_config={
            "strategy": st.column_config.TextColumn("Strategy", pinned=True),
            "entry_time": st.column_config.TextColumn("Entry time (IST)"),
            "exit_time": st.column_config.TextColumn("Exit time (IST)"),
            "entry_price": st.column_config.NumberColumn(format="₹%.2f"),
            "exit_price": st.column_config.NumberColumn(format="₹%.2f"),
            "realized_pnl": st.column_config.NumberColumn(format="₹%.2f"),
        },
    )


def _render_security_page(session: dict[str, object]) -> None:
    _authentication().require_owner(session)
    st.markdown("## :material/security: Security")
    st.subheader("Viewer accounts")
    st.caption("Viewers can monitor positions, trade history, and P&L, but cannot access trading controls or owner settings.")
    with st.form("create_viewer", clear_on_submit=True):
        viewer_username = st.text_input("Viewer username or email")
        viewer_password = st.text_input("Viewer password", type="password")
        viewer_confirmation = st.text_input("Confirm viewer password", type="password")
        create_viewer = st.form_submit_button(
            "Create viewer",
            type="primary",
            icon=":material/person_add:",
        )
    if create_viewer:
        try:
            _authentication().create_viewer(
                session,
                viewer_username,
                viewer_password,
                viewer_confirmation,
            )
            st.success(f"Viewer account {viewer_username.strip().lower()} created.")
        except ValueError as error:
            st.error(str(error))

    viewers = _database().list_users("USER")
    if not viewers:
        st.info("No viewer accounts yet.")
    for viewer in viewers:
        active = bool(viewer["active"])
        with st.container(border=True):
            st.markdown(f"**{viewer['username']}** · {'Active' if active else 'Disabled'}")
            st.caption(f"Created: {_format_ist_timestamp(viewer['created_at'])}")
            action = "Disable access" if active else "Enable access"
            if st.button(
                action,
                key=f"viewer_active_{viewer['id']}",
                icon=":material/person_off:" if active else ":material/person_check:",
            ):
                _authentication().set_viewer_active(session, int(viewer["id"]), not active)
                st.rerun()

    st.subheader("Active sessions")
    sessions = _database().active_sessions()
    st.metric("Active sessions", len(sessions), border=True)
    for item in sessions:
        is_current = item["session_id"] == session["session_id"]
        with st.container(border=True):
            st.markdown(f"**{item['username']}** {'· Current session' if is_current else ''}")
            st.write(
                f"Login: {_format_ist_timestamp(item['created_at'])} · "
                f"Last activity: {_format_ist_timestamp(item['last_activity'])} · "
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
    security_frame = pd.DataFrame(_database().security_events())
    if "timestamp" in security_frame.columns:
        security_frame["timestamp"] = security_frame["timestamp"].map(_format_ist_timestamp)
    st.dataframe(
        security_frame,
        hide_index=True,
        column_config={"timestamp": st.column_config.TextColumn("Time (IST)")},
    )


def _navigation_pages_for_role(role: str) -> list[str]:
    monitoring_pages = ["Positions", "Trade history", "Index P&L", "Commodity P&L"]
    if role.upper() == "OWNER":
        return ["Live dashboard", "Trade controls", *monitoring_pages, "Security", "Settings"]
    return monitoring_pages


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
    _complete_zerodha_login_callback()

    role = str(session.get("role", "")).upper()
    pages = _navigation_pages_for_role(role)
    with st.sidebar:
        display_role = "VIEWER" if role == "USER" else role
        st.caption(f"Signed in as {session['username']} · {display_role}")
        selected_page = st.radio("Navigation", pages, key="dashboard_navigation")
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            _authentication().logout(str(st.session_state.dashboard_session_token), _client_context())
            st.session_state.pop("dashboard_session_token", None)
            st.session_state.pop("dashboard_navigation", None)
            st.rerun()

    if selected_page == "Positions":
        _render_positions_page()
        return
    if selected_page == "Trade history":
        _render_trade_history_page()
        return
    if selected_page == "Index P&L":
        _render_pnl_calendar_page("INDEX")
        return
    if selected_page == "Commodity P&L":
        _render_pnl_calendar_page("COMMODITY")
        return
    if selected_page == "Security":
        _render_security_page(session)
        return
    if selected_page == "Settings":
        _render_settings_page(session)
        return

    if selected_page == "Live dashboard":
        st.markdown("## :material/candlestick_chart: Live dashboard")
        live_left, live_right = st.columns([2.2, 1.2])
        with live_left:
            st.caption("Live charts, signals, positions, and execution events.")
        with live_right:
            now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
            st.caption(f":material/schedule: {now_str}")

        trade_mode = str(st.session_state.get("trade_execution_mode", "PAPER"))
        real_mode_armed = bool(st.session_state.get("real_mode_armed", False))
        expiry_mode = str(st.session_state.get("index_expiry_mode", DEFAULT_INDEX_EXPIRY_MODE))
        commodity_expiry_mode = str(st.session_state.get("commodity_expiry_mode", "Current month"))
        expiry_week_offset = 1 if expiry_mode == "Next week" else 0
        commodity_expiry_offset = 1 if commodity_expiry_mode == "Next month" else 0
        reconciliation_required = any(
            bool(state.get("reconciliation_required"))
            for state in st.session_state.engine_state.values()
        )
        effective_real_mode_armed = (
            real_mode_armed
            and _runtime_allows_live_orders()
            and not reconciliation_required
        )
        st.caption(
            f"Execution: {trade_mode} · index expiry: {expiry_mode} · "
            f"commodity expiry: {commodity_expiry_mode}. Change these on Trade controls."
        )
        _persist_engine_state(st.session_state.engine_state)
        _render_live_engine(
            trade_mode,
            effective_real_mode_armed,
            expiry_week_offset,
            commodity_expiry_offset,
        )
        return

    st.markdown("## :material/tune: Trade controls")
    st.info("Configure execution here, then keep Live dashboard open while the trading engine is running.")
    if _is_cloud_runtime():
        if _cloud_live_orders_enabled():
            st.error(
                "Cloud REAL-order capability is enabled for this deployment. Actual Zerodha orders can be sent only "
                "after OWNER login, REAL mode selection, manual arming, and successful reconciliation."
            )
        else:
            st.warning(
                "Cloud safety mode is active: PAPER execution only. Set ZERODHA_ALLOW_REAL_TRADING=true at the "
                "deployment level to permit supervised REAL orders."
            )
    h1, h2 = st.columns([2.2, 1.2])
    with h1:
        st.caption("Connection, execution, expiry, position sizing, and risk settings.")
    with h2:
        now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
        st.caption(f":material/schedule: {now_str}")

    with st.container(border=True):
        st.subheader(":material/vpn_key: API login and connection")
        login_notice = str(st.session_state.pop("zerodha_login_notice", ""))
        if login_notice:
            if st.session_state.connected:
                st.success(login_notice)
            else:
                st.error(login_notice)
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
                st.info(
                    f"API credentials configured: {_credential_preview(saved_credentials['api_key'])}."
                )
                _render_same_tab_zerodha_login(
                    build_kite_login_url(saved_credentials["api_key"])
                )
                st.caption(
                    "Complete Zerodha login and 2FA. You will return here already connected."
                )

            with st.expander("Manual authentication fallback", expanded=not bool(saved_credentials)):
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
                )
                if st.button("Connect manually", width="stretch"):
                    try:
                        if not api_key.strip() or not api_secret.strip() or not request_token.strip():
                            raise ValueError("API key, API secret, and request token are required.")
                        access_token, check = _exchange_zerodha_request_token(
                            api_key.strip(), api_secret.strip(), request_token.strip()
                        )
                        st.session_state.connected = True
                        st.session_state.api_key = api_key.strip()
                        st.session_state.access_token = access_token
                        _save_connection_for_today(api_key.strip(), access_token)
                        if remember_credentials:
                            _save_login_credentials(api_key.strip(), api_secret.strip())
                        st.session_state.connection_restored = False
                        st.success(f"Connection success: {check.get('user_id', '')}")
                        st.rerun()
                    except Exception as error:
                        st.session_state.connected = False
                        st.error(f"Connection failure: {type(error).__name__}: {error}")

            st.info("Connection status: NOT CONNECTED")

    with st.container(border=True):
        st.subheader(":material/tune: Instrument controls")
        if st.button("Refresh now", width="stretch"):
            st.rerun()

        for start in range(0, len(UNDERLYINGS), 3):
            names = list(UNDERLYINGS)[start : start + 3]
            columns = st.columns(3)
            for column, name in zip(columns, names):
                state = st.session_state.engine_state[name]
                toggle_key = f"{name.lower()}_turn_off"
                with column:
                    with st.container(border=True):
                        st.markdown(f"**{name}**")
                        if st.button(
                            f"Start {name}", key=f"start_{name}", width="stretch",
                            disabled=bool(state.get("reconciliation_required")),
                        ):
                            state["enabled"] = True
                            st.session_state[toggle_key] = False
                        turned_off = st.toggle(
                            f"Turn OFF {name}", key=toggle_key,
                            disabled=bool(state.get("reconciliation_required")),
                        )
                        state["enabled"] = not turned_off and not bool(state.get("reconciliation_required"))
                        st.button(
                            f"Hard stop {name}",
                            key=f"hard_stop_{name}",
                            type="primary",
                            width="stretch",
                            on_click=_request_hard_stop_from_controls,
                            args=(name,),
                        )
                        status = "RUNNING" if state["enabled"] else "OFF"
                        cfg = UNDERLYINGS[name]
                        st.caption(
                            f"{status} · entries to {cfg.entry_cutoff_ist} IST · exit {cfg.mandatory_exit_ist} IST"
                        )
        st.caption(
            "Hard stop closes both strategy positions for that instrument and keeps both strategies OFF. "
            "Any REAL EMA exit requires REAL confirmation to remain armed."
        )

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
            st.caption(f"Durable state restored from {_format_ist_timestamp(recovery_saved_at)}.")

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
            if not _runtime_allows_live_orders():
                st.error(
                    "REAL mode is locked by the deployment. Set ZERODHA_ALLOW_REAL_TRADING=true and redeploy, "
                    "then complete broker reconciliation."
                )
            elif reconciliation_required:
                st.error("REAL mode remains blocked until broker reconciliation succeeds.")
            elif real_mode_armed:
                location = "cloud" if _is_cloud_runtime() else "local"
                st.warning(
                    f"REAL mode armed in this {location} session: new ENTRY/EXIT signals will place live market orders."
                )
            else:
                st.info("REAL mode selected but not armed. Signals will be logged, but no live order is placed.")
        else:
            st.caption("PAPER mode: strategy runs simulation only. No broker order is placed.")
        st.success(f"Selected execution mode: {trade_mode}")

        index_expiry_column, commodity_expiry_column = st.columns(2)
        with index_expiry_column:
            expiry_mode = st.segmented_control(
                "NIFTY and SENSEX expiry",
                options=["Current week", "Next week"],
                default=DEFAULT_INDEX_EXPIRY_MODE,
                key="index_expiry_mode",
            )
        with commodity_expiry_column:
            commodity_expiry_mode = st.segmented_control(
                "MCX commodity expiry",
                options=["Current month", "Next month"],
                default="Current month",
                key="commodity_expiry_mode",
            )
        expiry_week_offset = 1 if expiry_mode == "Next week" else 0
        commodity_expiry_offset = 1 if commodity_expiry_mode == "Next month" else 0
        st.success(f"Selected expiry: indices {expiry_mode}; commodities {commodity_expiry_mode}")
        st.caption(
            f"Index option filter: {expiry_mode}. MCX futures chart and ATM option: {commodity_expiry_mode}."
        )

        st.info(
            "NIFTY and SENSEX run both strategies in parallel: EMA 3/30 follows the selected execution mode, "
            "while the 5-minute Heikin-Ashi reversal strategy always runs in PAPER mode with independent positions."
        )

        st.markdown("**Position sizing and daily limits**")
        for start in range(0, len(UNDERLYINGS), 3):
            names = list(UNDERLYINGS)[start : start + 3]
            columns = st.columns(3)
            for column, name in zip(columns, names):
                cfg = UNDERLYINGS[name]
                state = st.session_state.engine_state[name]
                with column:
                    lots = int(
                        st.number_input(
                            f"{name} lots", min_value=cfg.default_lots,
                            value=max(cfg.default_lots, int(state.get("lots", cfg.default_lots))),
                            step=cfg.lot_step, key=f"lots_{name}",
                        )
                    )
                    state["lots"] = lots
                    if name in LOT_SIZES:
                        state["quantity"] = lots * LOT_SIZES[name]
                        st.caption(f"Lots: {lots} · broker order quantity: {state['quantity']}")
                    elif state.get("lot_size"):
                        broker_lot_size = int(state["lot_size"])
                        st.caption(
                            f"MCX lots: {lots} · broker order quantity: {lots * broker_lot_size} · "
                            f"contract size: {_mcx_contract_size_label(name, lots)} · "
                            f"Zerodha lot_size: {broker_lot_size}"
                        )
                    else:
                        st.caption(
                            f"Contract size: {_mcx_contract_size_label(name, lots)}. "
                            "Broker order quantity resolves from the selected MCX option's live Zerodha lot_size."
                        )
                    max_trades = st.number_input(
                        f"{name} max trades", min_value=1,
                        value=int(state.get("max_trades", 3)), step=1,
                        key=f"max_trades_{name}",
                    )
                    state["max_trades"] = int(max_trades)
                    if name in INDEX_NAMES:
                        st.caption("This limit applies to EMA only; parallel Heikin-Ashi PAPER trades are unlimited.")

        _, reset_column, _ = st.columns(3)
        with reset_column:
            has_real_recovery = _has_real_trade_recovery(st.session_state.engine_state)
            st.button(
                "Reset durable PAPER trade state",
                width="stretch",
                disabled=has_real_recovery,
                help="Reconcile broker activity before resetting recovered REAL state." if has_real_recovery else None,
                on_click=_reset_durable_paper_trade_state,
            )
            if st.session_state.pop("paper_reset_completed", False):
                st.success("Durable PAPER engine state reset for all instruments.")
            reset_error = st.session_state.pop("paper_reset_error", "")
            if reset_error:
                st.error(reset_error)

        with st.expander("Trailing stop settings", expanded=True):
            for name in UNDERLYINGS:
                state = st.session_state.engine_state[name]
                linked_to_nifty = name in ("GOLD", "SILVER")
                with st.form(f"{name.lower()}_risk_settings"):
                    st.markdown(f"**{name} risk and trailing values**")
                    if linked_to_nifty:
                        multiplier = UNDERLYINGS[name].risk_point_multiplier
                        st.caption(
                            f"Linked to NIFTY: point values are {multiplier:g}x; percentages match NIFTY."
                        )
                    t1, t2, t3 = st.columns(3)
                    sl_points = t1.number_input(
                        f"{name} fixed SL points", min_value=1.0,
                        value=_to_float(state.get("sl_points"), UNDERLYINGS[name].default_sl_points), step=1.0,
                        disabled=linked_to_nifty,
                    )
                    sl_to_cost_pct = t2.number_input(
                        f"{name} SL to cost %", min_value=1.0, max_value=200.0,
                        value=_to_float(state.get("sl_to_cost_profit_pct"), 0.30) * 100.0, step=1.0,
                        disabled=linked_to_nifty,
                    )
                    trail_after_pct = t3.number_input(
                        f"{name} trail starts %", min_value=1.0, max_value=300.0,
                        value=UNIVERSAL_TRAIL_START_PCT * 100.0, step=1.0,
                        disabled=True,
                    )
                    t4, t5, t6 = st.columns(3)
                    giveback_pct = t4.number_input(
                        f"{name} giveback %", min_value=0.0, max_value=100.0,
                        value=_to_float(state.get("mfe_giveback_pct"), 0.275) * 100.0, step=0.5,
                        disabled=linked_to_nifty,
                    )
                    trigger_points = t5.number_input(
                        f"{name} trigger points", min_value=1.0,
                        value=_to_float(state.get("trail_trigger_points"), 15.0), step=1.0,
                        disabled=linked_to_nifty,
                    )
                    step_points = t6.number_input(
                        f"{name} SL step points", min_value=0.0,
                        value=_to_float(state.get("trail_step_points"), 5.0), step=1.0,
                        disabled=linked_to_nifty,
                    )
                    submitted = st.form_submit_button(
                        f"{name} follows NIFTY" if linked_to_nifty else f"Save {name} settings",
                        width="stretch",
                        disabled=linked_to_nifty,
                    )

                if submitted:
                    state.update(
                        {
                            "sl_points": float(sl_points),
                            "sl_to_cost_profit_pct": float(sl_to_cost_pct) / 100.0,
                            "trail_after_profit_pct": UNIVERSAL_TRAIL_START_PCT,
                            "mfe_giveback_pct": float(giveback_pct) / 100.0,
                            "trail_trigger_points": float(trigger_points),
                            "trail_step_points": float(step_points),
                        }
                    )
                    if name == "NIFTY":
                        _sync_scaled_metal_risk_settings(st.session_state.engine_state)
                    _save_risk_settings(st.session_state.engine_state)
                    st.session_state.risk_settings_saved_at[name] = datetime.now(IST).strftime("%H:%M:%S")

                saved_at = st.session_state.risk_settings_saved_at.get(name)
                if saved_at:
                    st.success(f"{name} settings saved and active at {saved_at} IST.")
                else:
                    st.caption(f"Edit the {name} values, then press Save {name} settings to apply them.")

    _persist_engine_state(st.session_state.engine_state)
    st.success("Controls saved. Open Live dashboard to run and monitor the trading engine.")


if __name__ == "__main__":
    main()
