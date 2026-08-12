"""Streamlit app for safe Zerodha live-data testing (no live orders)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# Ensure src-layout imports resolve regardless of launch cwd.
SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from aadithya_quantlab.experiments.run_exp001 import main as run_exp001_main
from aadithya_quantlab.trading import auto_paper as auto_paper_module
from aadithya_quantlab.trading.zerodha import build_kite_login_url
from aadithya_quantlab.trading.zerodha_live import (
    DEFAULT_API_KEY_FILE,
    DEFAULT_ACCESS_TOKEN_FILE,
    DailyLoginInputs,
    NoHistoricalCandlesError,
    access_token_preview,
    build_safe_kite_client_from_env,
    fetch_historical_to_canonical_csv,
    load_api_key,
    load_access_token,
    run_daily_login,
)
from quantlab.apex.agents.base import OptionContract
from quantlab.apex.agents.options import OptionsAgent


IST = ZoneInfo("Asia/Kolkata")

APP_THEME_OPTIONS = ("Light", "Dark")


def _theme_tokens(theme_mode: str) -> dict[str, str]:
    normalized = theme_mode.strip().lower()
    if normalized == "dark":
        return {
            "mode": "dark",
            "bg": "#0b1020",
            "bg_alt": "#11182c",
            "surface": "#121a2f",
            "surface_alt": "#18233b",
            "border": "#26304a",
            "text": "#f1f5f9",
            "muted": "#9aa4bf",
            "accent": "#a855f7",
            "accent_alt": "#7c3aed",
            "accent_soft": "rgba(168, 85, 247, 0.16)",
            "success": "#22c55e",
            "danger": "#fb7185",
            "warning": "#f59e0b",
        }
    return {
        "mode": "light",
        "bg": "#f7f8fc",
        "bg_alt": "#eef2ff",
        "surface": "#ffffff",
        "surface_alt": "#f6f7fb",
        "border": "#dfe3f2",
        "text": "#111827",
        "muted": "#6b7280",
        "accent": "#8b5cf6",
        "accent_alt": "#6d28d9",
        "accent_soft": "rgba(139, 92, 246, 0.14)",
        "success": "#059669",
        "danger": "#ef4444",
        "warning": "#d97706",
    }


def _apply_app_theme(theme_mode: str) -> None:
    tokens = _theme_tokens(theme_mode)
    css = """
    <style>
    :root {
        --aq-bg: __BG__;
        --aq-bg-alt: __BG_ALT__;
        --aq-surface: __SURFACE__;
        --aq-surface-alt: __SURFACE_ALT__;
        --aq-border: __BORDER__;
        --aq-text: __TEXT__;
        --aq-muted: __MUTED__;
        --aq-accent: __ACCENT__;
        --aq-accent-alt: __ACCENT_ALT__;
        --aq-accent-soft: __ACCENT_SOFT__;
        --aq-success: __SUCCESS__;
        --aq-danger: __DANGER__;
        --aq-warning: __WARNING__;
    }

    .stApp {
        background:
            radial-gradient(circle at top left, rgba(139, 92, 246, 0.10), transparent 28%),
            radial-gradient(circle at top right, rgba(34, 197, 94, 0.06), transparent 24%),
            linear-gradient(180deg, var(--aq-bg-alt) 0%, var(--aq-bg) 42%, var(--aq-bg) 100%);
        color: var(--aq-text);
    }

    [data-testid="stAppViewContainer"] {
        background: transparent;
        color: var(--aq-text);
    }

    [data-testid="stHeader"] {
        background: transparent;
    }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, var(--aq-surface) 0%, var(--aq-surface-alt) 100%);
        border-right: 1px solid var(--aq-border);
    }

    [data-testid="stSidebar"] * {
        color: var(--aq-text);
    }

    .stApp,
    .stApp p,
    .stApp div,
    .stApp label,
    .stApp span,
    .stApp h1,
    .stApp h2,
    .stApp h3,
    .stApp h4,
    .stApp h5,
    .stApp h6 {
        color: var(--aq-text);
    }

    .stApp [data-testid="stMetric"],
    .stApp [data-testid="metric-container"],
    .stApp [data-testid="stForm"],
    .stApp [data-testid="stExpander"],
    .stApp .stAlert,
    .stApp .stDataFrame,
    .stApp .stTable,
    .stApp .stCodeBlock {
        background: var(--aq-surface);
        border: 1px solid var(--aq-border);
        border-radius: 18px;
        box-shadow: 0 18px 40px rgba(15, 23, 42, 0.06);
    }

    .stApp [data-testid="stMetric"] {
        padding: 0.85rem 1rem;
    }

    .stApp button[kind="primary"],
    .stApp .stButton > button,
    .stApp .stDownloadButton > button {
        background: linear-gradient(135deg, var(--aq-accent) 0%, var(--aq-accent-alt) 100%);
        color: white;
        border: 1px solid transparent;
        border-radius: 999px;
        box-shadow: 0 10px 24px rgba(124, 58, 237, 0.18);
    }

    .stApp button[kind="secondary"] {
        background: var(--aq-surface);
        color: var(--aq-text);
        border: 1px solid var(--aq-border);
        border-radius: 999px;
    }

    .stApp .stTextInput input,
    .stApp .stNumberInput input,
    .stApp .stTextArea textarea,
    .stApp .stSelectbox [data-baseweb="select"] > div,
    .stApp .stMultiSelect [data-baseweb="select"] > div {
        background: var(--aq-surface);
        color: var(--aq-text);
        border-color: var(--aq-border);
    }

    .stApp .stCheckbox label,
    .stApp .stRadio label,
    .stApp .stSlider label {
        color: var(--aq-text);
    }

    .stApp hr {
        border-color: var(--aq-border);
    }

    .stApp [data-testid="stNotificationContentInfo"],
    .stApp [data-testid="stNotificationContentSuccess"] {
        border-color: color-mix(in srgb, var(--aq-success) 35%, var(--aq-border));
    }

    .stApp [data-testid="stNotificationContentWarning"],
    .stApp [data-testid="stNotificationContentError"] {
        border-color: color-mix(in srgb, var(--aq-danger) 35%, var(--aq-border));
    }

    .aq-theme-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        padding: 0.35rem 0.75rem;
        border-radius: 999px;
        border: 1px solid var(--aq-border);
        background: var(--aq-surface);
        color: var(--aq-muted);
        font-size: 0.85rem;
        font-weight: 600;
    }

    .aq-theme-chip strong {
        color: var(--aq-text);
    }
    </style>
    """
    for key, value in tokens.items():
        placeholder = f"__{key.upper()}__"
        css = css.replace(placeholder, value)
    st.markdown(css, unsafe_allow_html=True)

# Guard against stale module state where helper symbols may be missing.
AutoPaperConfig = auto_paper_module.AutoPaperConfig
AutoPaperPosition = auto_paper_module.AutoPaperPosition
open_auto_paper_position = auto_paper_module.open_auto_paper_position
update_auto_paper_position = auto_paper_module.update_auto_paper_position


def _fallback_append_auto_paper_trade(path: str | Path, trade: dict[str, object]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([trade])
    frame.to_csv(output, mode="a", header=not output.exists(), index=False)
    return output


def _fallback_append_auto_paper_entry(path: str | Path, position: AutoPaperPosition) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                "mode": "PAPER_AUTO",
                "side": position.side,
                "contract_symbol": position.contract_symbol,
                "entry_time": position.entry_time,
                "entry_price": position.entry_price,
                "stop_loss_price": position.stop_loss_price,
                "target_price": position.target_price,
                "trailing_active": position.trailing_active,
            }
        ]
    )
    frame.to_csv(output, mode="a", header=not output.exists(), index=False)
    return output


append_auto_paper_entry = getattr(auto_paper_module, "append_auto_paper_entry", _fallback_append_auto_paper_entry)
append_auto_paper_trade = getattr(auto_paper_module, "append_auto_paper_trade", _fallback_append_auto_paper_trade)


def _parse_iso_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            "Use ISO datetime format, for example 2026-07-10T09:15:00+05:30"
        ) from error


def _safe_token_status() -> tuple[str, str]:
    try:
        token = load_access_token()
        return "Available", access_token_preview(token)
    except ValueError:
        return "Missing", "not found"


def _safe_api_key_status() -> tuple[str, str]:
    try:
        api_key = load_api_key()
        return "Available", access_token_preview(api_key)
    except ValueError:
        return "Missing", "not found"


def _env_status(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "set" if value else "missing"


def _today_session_window() -> tuple[str, str]:
    now = datetime.now(IST)
    session_start = datetime.combine(now.date(), time(9, 15), tzinfo=IST)
    session_end = datetime.combine(now.date(), time(15, 30), tzinfo=IST)
    effective_end = min(now, session_end)
    return session_start.isoformat(), effective_end.isoformat()


def _apply_runtime_credentials(
    *,
    api_key: str,
    access_token: str = "",
) -> None:
    if api_key.strip():
        os.environ["ZERODHA_API_KEY"] = api_key.strip()
        DEFAULT_API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_API_KEY_FILE.write_text(api_key.strip(), encoding="utf-8")
    if access_token.strip():
        os.environ["ZERODHA_ACCESS_TOKEN"] = access_token.strip()


def _clear_runtime_and_local_token(token_file: Path) -> tuple[bool, str]:
    file_removed = False
    if token_file.exists():
        token_file.unlink()
        file_removed = True

    os.environ.pop("ZERODHA_ACCESS_TOKEN", None)
    return file_removed, f"Cleared runtime access token. Removed local token file: {file_removed}."


def _check_connection() -> dict[str, str]:
    profile = build_safe_kite_client_from_env().profile()
    return {
        "user_id": str(profile.get("user_id", "")),
        "user_name": str(profile.get("user_name", "")),
    }


def _fetch_ltp(instrument: str) -> dict[str, object]:
    return build_safe_kite_client_from_env().ltp(instrument)


def _as_aware_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return datetime.combine(value, time(15, 30), tzinfo=timezone.utc)
    return None


def _load_nifty_option_instruments(asof: datetime) -> list[dict[str, Any]]:
    cache = st.session_state.get("nifty_option_universe_cache")
    cache_key = asof.date().isoformat()
    if isinstance(cache, dict) and cache.get("session_date") == cache_key:
        return cache.get("rows", [])

    kite = build_safe_kite_client_from_env()
    rows = kite.instruments("NFO")
    filtered: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("tradingsymbol", ""))
        option_type = str(row.get("instrument_type", ""))
        expiry_dt = _as_aware_datetime(row.get("expiry"))
        if not symbol.startswith("NIFTY"):
            continue
        if option_type not in {"CE", "PE"}:
            continue
        if expiry_dt is None or expiry_dt < asof:
            continue

        filtered.append(
            {
                "symbol": symbol,
                "option_type": option_type,
                "strike": int(float(row.get("strike", 0) or 0)),
                "expiry": expiry_dt,
            }
        )

    st.session_state["nifty_option_universe_cache"] = {
        "session_date": cache_key,
        "rows": filtered,
    }
    return filtered


def _select_live_option_contract(
    *,
    spot_price: float,
    side: str,
    asof: datetime,
) -> tuple[str, float] | None:
    option_type = "CE" if side.upper() == "CALL" else "PE"
    instruments = _load_nifty_option_instruments(asof)
    if not instruments:
        return None

    future_expiries = sorted({row["expiry"] for row in instruments if row["expiry"] >= asof})
    if not future_expiries:
        return None
    nearest_expiry = future_expiries[0]
    atm_strike = int(round(spot_price / 50.0) * 50)

    shortlisted = [
        row
        for row in instruments
        if row["expiry"] == nearest_expiry
        and row["option_type"] == option_type
        and abs(row["strike"] - atm_strike) <= 50
    ]
    if not shortlisted:
        return None

    quote_symbols = [f"NFO:{row['symbol']}" for row in shortlisted]
    kite = build_safe_kite_client_from_env()
    quotes = kite.quote_many(quote_symbols)

    contracts: list[OptionContract] = []
    price_by_symbol: dict[str, float] = {}
    for row in shortlisted:
        quote_key = f"NFO:{row['symbol']}"
        quote = quotes.get(quote_key, {})
        depth = quote.get("depth", {}) if isinstance(quote, dict) else {}
        buy_depth = depth.get("buy", []) if isinstance(depth, dict) else []
        sell_depth = depth.get("sell", []) if isinstance(depth, dict) else []
        ltp = _to_float(quote.get("last_price", 0.0), 0.0)
        bid = _to_float((buy_depth[0] or {}).get("price", ltp) if buy_depth else ltp, ltp)
        ask = _to_float((sell_depth[0] or {}).get("price", ltp) if sell_depth else ltp, ltp)
        oi = int(_to_float(quote.get("oi", 0), 0))
        if ltp <= 0:
            continue
        ask = max(ask, bid)

        contracts.append(
            OptionContract(
                symbol=row["symbol"],
                expiry=row["expiry"],
                strike=row["strike"],
                option_type=row["option_type"],
                bid=bid,
                ask=ask,
                ltp=ltp,
                oi=oi,
            )
        )
        price_by_symbol[row["symbol"]] = ltp

    if not contracts:
        return None

    report = OptionsAgent().run(
        spot_price=spot_price,
        contracts=contracts,
        asof=asof,
        option_type=option_type,
    )
    if not report.approved or report.selection is None:
        return None

    symbol = report.selection.contract_symbol
    selected_price = price_by_symbol.get(symbol, 0.0)
    if selected_price <= 0:
        return None
    return symbol, selected_price


def _friendly_error_message(error: Exception) -> str:
    raw = str(error)
    lowered = raw.lower()
    if "api_key" in lowered and "access_token" in lowered:
        return (
            "Incorrect API key or access token. Ensure both belong to the same Zerodha app, "
            "generate a fresh request_token via Step 1, run Step 2 again, then retry Step 3."
        )
    if "token" in lowered and "expired" in lowered:
        return "Access token expired. Generate a fresh token in Step 2 and retry."
    return raw


def _run_workflow(
    *,
    instrument: str,
    symbol: str,
    instrument_token: int,
    interval: str,
    from_iso: str,
    to_iso: str,
    historical_output: str,
    report_output: str,
    labeled_output: str,
) -> tuple[int, str]:
    from_dt = _parse_iso_datetime(from_iso)
    to_dt = _parse_iso_datetime(to_iso)

    _check_connection()
    _fetch_ltp(instrument)

    output_path = fetch_historical_to_canonical_csv(
        instrument_token=instrument_token,
        symbol=symbol,
        interval=interval,
        from_date=from_dt,
        to_date=to_dt,
        output_csv=historical_output,
    )

    if not output_path.exists():
        return 3, "Historical CSV was not created, skipping EXP-001."

    exp_exit = run_exp001_main(
        [
            "--input",
            str(output_path),
            "--output",
            report_output,
            "--labeled-output",
            labeled_output,
        ]
    )
    if exp_exit != 0:
        return exp_exit, "EXP-001 returned a non-zero exit code."
    return 0, "Workflow completed successfully."


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compute_short_horizon_bias(
    labeled: pd.DataFrame,
    *,
    bias_score_threshold: float = 1.25,
) -> dict[str, object]:
    if labeled.empty:
        raise ValueError("Labeled dataset is empty.")

    bars = labeled.sort_values(["timestamp"]).reset_index(drop=True)
    latest = bars.iloc[-1]
    recent = bars.tail(min(len(bars), 5))

    state_id = str(latest.get("state_id", ""))
    state_name = str(latest.get("state_name", "Unknown"))
    confidence = _to_float(latest.get("state_confidence", 0.0))
    signed_eff = _to_float(latest.get("signed_directional_efficiency", 0.0))
    compression = _to_float(latest.get("compression_score", 1.0))
    rv = _to_float(latest.get("realized_volatility", 0.0))

    up_count = int((recent.get("state_id") == "S02").sum())
    down_count = int((recent.get("state_id") == "S03").sum())
    failed_breakout = bool(latest.get("failed_breakout_up", False)) or bool(
        latest.get("failed_breakout_down", False)
    )
    noisy = state_id == "S08"

    score = 0.0
    if state_id == "S02":
        score += 2.0
    elif state_id == "S03":
        score -= 2.0
    elif state_id in {"S04", "S05", "S06", "S08"}:
        score += 0.0

    score += 1.2 * signed_eff
    score += 0.3 * (up_count - down_count)

    if failed_breakout:
        score *= 0.5
    if noisy:
        score *= 0.6
    if compression < 0.70:
        score *= 0.8

    if score >= bias_score_threshold and confidence >= 0.60:
        bias = "Bullish bias (consider CALL side setup)"
    elif score <= -bias_score_threshold and confidence >= 0.60:
        bias = "Bearish bias (consider PUT side setup)"
    else:
        bias = "No-trade / wait for confirmation"

    event_flags: list[str] = []
    if failed_breakout:
        event_flags.append("Failed breakout detected")
    if noisy:
        event_flags.append("Disorderly/noisy state")
    if compression < 0.70:
        event_flags.append("Volatility compression")
    if rv > 0.005:
        event_flags.append("Elevated short-horizon volatility")
    if not event_flags:
        event_flags.append("No major risk event flag")

    hold_window = "10-20 min" if abs(score) >= 2.0 else "20-45 min"

    return {
        "bias": bias,
        "score": round(score, 3),
        "state_name": state_name,
        "state_confidence": round(confidence, 3),
        "signed_efficiency": round(signed_eff, 3),
        "hold_window": hold_window,
        "event_flags": event_flags,
        "latest_timestamp": str(latest.get("timestamp", "")),
        "bias_score_threshold": round(bias_score_threshold, 3),
    }


def _evaluate_confirmation_checklist(
    analysis: dict[str, object],
    *,
    min_confidence: float,
    min_abs_score: float,
    allow_failed_breakout: bool,
    allow_noisy_state: bool,
    allow_compression: bool,
    allow_elevated_volatility: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    score = abs(_to_float(analysis.get("score", 0.0)))
    confidence = _to_float(analysis.get("state_confidence", 0.0))
    bias_text = str(analysis.get("bias", ""))
    flags = [str(flag).lower() for flag in analysis.get("event_flags", [])]

    if "no-trade" in bias_text.lower():
        reasons.append("Directional bias is not strong enough.")
    if confidence < min_confidence:
        reasons.append(
            f"State confidence {confidence:.3f} is below threshold {min_confidence:.3f}."
        )
    if score < min_abs_score:
        reasons.append(f"Signal score {score:.3f} is below threshold {min_abs_score:.3f}.")

    if (not allow_failed_breakout) and any("failed breakout" in flag for flag in flags):
        reasons.append("Failed-breakout risk flag is active.")
    if (not allow_noisy_state) and any("noisy" in flag for flag in flags):
        reasons.append("Disorderly/noisy state flag is active.")
    if (not allow_compression) and any("compression" in flag for flag in flags):
        reasons.append("Volatility-compression flag is active.")
    if (not allow_elevated_volatility) and any("elevated" in flag for flag in flags):
        reasons.append("Elevated-volatility flag is active.")

    return (len(reasons) == 0, reasons)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _build_market_adaptive_config(
    *,
    labeled_bars: pd.DataFrame,
    spot_price: float,
    base_config: AutoPaperConfig,
    analysis: dict[str, object],
) -> tuple[AutoPaperConfig, dict[str, float]]:
    latest = labeled_bars.sort_values(["timestamp"]).iloc[-1]
    atr = _to_float(latest.get("atr", 0.0), 0.0)
    confidence = _to_float(analysis.get("state_confidence", 0.0), 0.0)
    flags = [str(flag).lower() for flag in analysis.get("event_flags", [])]

    baseline_move = max(spot_price * 0.0015, 1.0)
    vol_factor = _clamp(atr / baseline_move, 0.70, 1.80)
    confidence_factor = _clamp(1.25 - confidence, 0.80, 1.30)
    noisy_factor = 1.15 if any("noisy" in flag or "elevated" in flag for flag in flags) else 1.0

    stop_loss_pct = _clamp(
        base_config.stop_loss_pct * (0.85 + 0.35 * vol_factor) * noisy_factor,
        0.04,
        0.20,
    )
    profit_trigger_points = _clamp(
        base_config.profit_trigger_points * vol_factor * confidence_factor,
        5.0,
        50.0,
    )
    trailing_points = _clamp(
        base_config.trailing_points * (0.85 + 0.30 * vol_factor),
        2.0,
        20.0,
    )
    target_points = _clamp(
        base_config.target_points * (0.90 + 0.45 * vol_factor),
        10.0,
        80.0,
    )

    adaptive = AutoPaperConfig(
        stop_loss_pct=stop_loss_pct,
        profit_trigger_points=profit_trigger_points,
        trailing_points=trailing_points,
        target_points=target_points,
    )
    diagnostics = {
        "atr": round(atr, 3),
        "vol_factor": round(vol_factor, 3),
        "confidence_factor": round(confidence_factor, 3),
        "noisy_factor": round(noisy_factor, 3),
    }
    return adaptive, diagnostics


def main() -> None:
    st.set_page_config(page_title="Aadithya QuantLab - Zerodha Live Data App", layout="wide")

    if st.session_state.get("ui_theme") not in APP_THEME_OPTIONS:
        st.session_state["ui_theme"] = APP_THEME_OPTIONS[0]

    header_left, header_right = st.columns([5, 1])
    with header_right:
        st.selectbox(
            "Theme",
            options=list(APP_THEME_OPTIONS),
            key="ui_theme",
            label_visibility="collapsed",
        )

    _apply_app_theme(st.session_state["ui_theme"])

    with header_left:
        st.title("Aadithya QuantLab: Zerodha Live-Data Test App")
        st.caption("Live-data only. Live order placement is hard-disabled.")

    st.markdown(
        f'<div class="aq-theme-chip">Theme <strong>{st.session_state["ui_theme"]}</strong></div>',
        unsafe_allow_html=True,
    )

    if "wf_from" not in st.session_state or "wf_to" not in st.session_state:
        default_from, default_to = _today_session_window()
        st.session_state["wf_from"] = default_from
        st.session_state["wf_to"] = default_to

    api_key_state, api_key_preview = _safe_api_key_status()
    token_state, token_preview = _safe_token_status()

    st.subheader("Quick Start")
    st.info(
        "Use this page instead of the terminal flow: save API key, generate login URL, paste request token + API secret, "
        "verify connection, then run today's workflow. File-backed credentials count as available even when terminal env vars are empty."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("API Key (effective)", api_key_state)
    c2.metric("API Key Preview", api_key_preview)
    c3.metric("Access Token", token_state)
    c4.metric("Token Preview", token_preview)

    c5, c6 = st.columns(2)
    c5.metric("ZERODHA_API_KEY env", _env_status("ZERODHA_API_KEY"))
    c6.metric("ZERODHA_API_SECRET env", _env_status("ZERODHA_API_SECRET"))

    st.divider()
    st.subheader("0) I already have access token (optional path)")
    st.caption("Use this only if you already generated today access token. If you need to generate it now, skip to Step 2.")
    with st.form("runtime-credentials"):
        runtime_api_key = st.text_input("ZERODHA_API_KEY", value=os.getenv("ZERODHA_API_KEY", ""))
        runtime_access_token = st.text_input(
            "ZERODHA_ACCESS_TOKEN (optional if token file exists)",
            value="",
            type="password",
        )
        submit_runtime = st.form_submit_button("Use Existing Token")

    if submit_runtime:
        try:
            if not runtime_api_key.strip():
                raise ValueError("ZERODHA_API_KEY is required.")
            _apply_runtime_credentials(
                api_key=runtime_api_key,
                access_token=runtime_access_token,
            )
            st.success("API key and existing token settings applied for this app session.")
            if runtime_access_token.strip():
                st.info(f"Access token preview: {access_token_preview(runtime_access_token.strip())}")
        except Exception as error:  # pragma: no cover - UI path
            st.error(str(error))

    clear_token_file = st.text_input(
        "Token file to clear",
        value=str(DEFAULT_ACCESS_TOKEN_FILE),
        help="Use this to clear stale token state before generating a new token.",
    )
    clear_api_key_file = st.checkbox("Also clear saved API key file", value=False)
    if st.button("Clear Local Token And Restart Login Flow", type="secondary", use_container_width=True):
        try:
            removed_file, message = _clear_runtime_and_local_token(Path(clear_token_file))
            if clear_api_key_file and DEFAULT_API_KEY_FILE.exists():
                DEFAULT_API_KEY_FILE.unlink()
                os.environ.pop("ZERODHA_API_KEY", None)
            st.success(message)
            if not removed_file:
                st.info("No token file was found at that path. Runtime token was still cleared.")
            st.info("Now run Step 1 and Step 2 to generate a fresh token.")
        except Exception as error:  # pragma: no cover - UI path
            st.error(str(error))

    st.divider()
    st.subheader("1) Login URL")
    st.caption("Paste API key once here. The app saves it locally for later steps and later terminal commands.")
    api_key = st.text_input(
        "API Key for URL",
        value=os.getenv("ZERODHA_API_KEY", DEFAULT_API_KEY_FILE.read_text(encoding="utf-8").strip() if DEFAULT_API_KEY_FILE.exists() else ""),
        help="Only used to generate the login URL.",
    )
    save_key_col, url_col = st.columns(2)
    if save_key_col.button("Save API Key Locally", use_container_width=True):
        try:
            if not api_key.strip():
                raise ValueError("Provide ZERODHA_API_KEY first.")
            _apply_runtime_credentials(api_key=api_key)
            st.success(f"Saved API key to {DEFAULT_API_KEY_FILE}")
        except Exception as error:  # pragma: no cover - UI path
            st.error(str(error))

    if url_col.button("Generate Zerodha Login URL", use_container_width=True):
        if not api_key.strip():
            st.error("Provide ZERODHA_API_KEY first.")
        else:
            _apply_runtime_credentials(api_key=api_key)
            st.code(build_kite_login_url(api_key.strip()))

    st.divider()
    st.subheader("2) Generate today's access token (recommended)")
    st.caption("Provide API key, request token from Zerodha redirect URL, and API secret. This saves access token locally and prepares Step 3.")
    with st.form("daily-login"):
        daily_api_key = st.text_input("ZERODHA_API_KEY", value=os.getenv("ZERODHA_API_KEY", ""))
        request_token = st.text_input("ZERODHA_REQUEST_TOKEN", value="")
        api_secret = st.text_input("ZERODHA_API_SECRET", value="", type="password")
        token_output = st.text_input("Token Output File", value=str(DEFAULT_ACCESS_TOKEN_FILE))
        submit_login = st.form_submit_button("Generate Access Token")

    if submit_login:
        try:
            result = run_daily_login(
                DailyLoginInputs(
                    api_key=daily_api_key,
                    api_secret=api_secret,
                    request_token=request_token,
                    token_output_path=Path(token_output),
                )
            )
            saved_token = load_access_token(fallback_path=Path(token_output))
            _apply_runtime_credentials(
                api_key=daily_api_key,
                access_token=saved_token,
            )
            st.success(f"Saved token: {result.token_output_path}")
            st.info(f"Token preview: {result.masked_access_token}")
            st.info("Step 3 is ready. Click Check Connection (kite.profile).")
        except Exception as error:  # pragma: no cover - UI path
            st.error(str(error))

    st.divider()
    st.subheader("3) Connection Check + Live LTP")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Check Connection (kite.profile)", use_container_width=True):
            try:
                profile = _check_connection()
                st.success(f"Connected: user_id={profile['user_id']} user_name={profile['user_name']}")
            except Exception as error:  # pragma: no cover - UI path
                st.error(_friendly_error_message(error))

    with col_b:
        instrument_ltp = st.text_input("Instrument for LTP", value="NSE:NIFTY 50")
        if st.button("Fetch Live LTP", use_container_width=True):
            try:
                ltp = _fetch_ltp(instrument_ltp)
                st.code(json.dumps(ltp, indent=2, default=str))
            except Exception as error:  # pragma: no cover - UI path
                st.error(_friendly_error_message(error))

    st.divider()
    st.subheader("4) Historical Fetch + EXP-001")
    quick_col1, quick_col2 = st.columns(2)
    if quick_col1.button("Use Today's NIFTY Session Window", use_container_width=True):
        default_from, default_to = _today_session_window()
        st.session_state["wf_from"] = default_from
        st.session_state["wf_to"] = default_to
        st.success("Loaded today's 09:15 IST to now for NIFTY.")
    quick_col2.caption("This sets Step 4 to today's live session window without typing ISO timestamps.")

    with st.form("workflow-form"):
        wf_instrument = st.text_input("Workflow Instrument", value="NSE:NIFTY 50")
        wf_symbol = st.text_input("Workflow Symbol", value="NIFTY")
        wf_token = st.number_input("Instrument Token", min_value=1, value=256265, step=1)
        wf_interval = st.selectbox("Interval", options=["minute", "3minute", "5minute", "15minute"], index=2)
        wf_from = st.text_input("From (ISO)", key="wf_from")
        wf_to = st.text_input("To (ISO)", key="wf_to")
        wf_hist = st.text_input("Historical CSV", value="outputs/exp001/nifty_5minute_ohlcv.csv")
        wf_report = st.text_input("EXP-001 Report", value="validation/reports/EXP-001_nifty_live_history.md")
        wf_labeled = st.text_input(
            "EXP-001 Labeled CSV",
            value="validation/reports/EXP-001_nifty_live_history_labeled.csv",
        )
        submit_workflow = st.form_submit_button("Run Safe End-to-End Workflow")

    if submit_workflow:
        try:
            exit_code, message = _run_workflow(
                instrument=wf_instrument,
                symbol=wf_symbol,
                instrument_token=int(wf_token),
                interval=wf_interval,
                from_iso=wf_from,
                to_iso=wf_to,
                historical_output=wf_hist,
                report_output=wf_report,
                labeled_output=wf_labeled,
            )
            if exit_code == 0:
                st.success(message)
                st.write(f"Report: {wf_report}")
                st.write(f"Labeled: {wf_labeled}")
            else:
                st.error(f"Workflow failed with exit code {exit_code}: {message}")
        except NoHistoricalCandlesError as error:
            st.warning(str(error))
        except Exception as error:  # pragma: no cover - UI path
            st.error(_friendly_error_message(error))

    st.divider()
    st.subheader("5) Market Analysis (10-45 min option-buying)")
    st.caption(
        "Decision support only. This panel provides a directional bias from recent labeled bars and event filters; execution remains manual."
    )
    with st.expander("Confirmation Checklist Settings", expanded=True):
        bias_score_threshold = st.slider(
            "Directional bias score threshold",
            min_value=0.50,
            max_value=2.50,
            value=1.00,
            step=0.05,
        )
        min_confidence = st.slider(
            "Minimum state confidence",
            min_value=0.50,
            max_value=0.95,
            value=0.65,
            step=0.01,
        )
        min_abs_score = st.slider(
            "Minimum absolute signal score",
            min_value=0.50,
            max_value=3.00,
            value=1.50,
            step=0.05,
        )
        allow_failed_breakout = st.checkbox("Allow failed-breakout flag", value=False)
        allow_noisy_state = st.checkbox("Allow disorderly/noisy state flag", value=False)
        allow_compression = st.checkbox("Allow volatility-compression flag", value=False)
        allow_elevated_volatility = st.checkbox("Allow elevated-volatility flag", value=False)

    analysis_file = st.text_input(
        "Labeled CSV for analysis",
        value="validation/reports/EXP-001_nifty_live_history_labeled.csv",
    )
    if st.button("Analyze Current Market Bias", use_container_width=True):
        try:
            labeled_path = Path(analysis_file)
            if not labeled_path.exists():
                raise ValueError("Labeled CSV not found. Run Step 4 first.")
            labeled_bars = pd.read_csv(labeled_path)
            analysis = _compute_short_horizon_bias(
                labeled_bars,
                bias_score_threshold=bias_score_threshold,
            )

            st.success(f"Bias: {analysis['bias']}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Signal Score", str(analysis["score"]))
            m2.metric("State", str(analysis["state_name"]))
            m3.metric("Confidence", str(analysis["state_confidence"]))
            m4.metric("Suggested Hold", str(analysis["hold_window"]))
            st.write(f"Directional threshold: {analysis['bias_score_threshold']}")

            st.write(f"Latest bar: {analysis['latest_timestamp']}")
            st.write("Event flags:")
            for flag in analysis["event_flags"]:
                st.write(f"- {flag}")

            confirmed, reasons = _evaluate_confirmation_checklist(
                analysis,
                min_confidence=min_confidence,
                min_abs_score=min_abs_score,
                allow_failed_breakout=allow_failed_breakout,
                allow_noisy_state=allow_noisy_state,
                allow_compression=allow_compression,
                allow_elevated_volatility=allow_elevated_volatility,
            )
            if confirmed:
                st.success("Checklist verdict: CONFIRMED setup for manual execution.")
            else:
                st.warning("Checklist verdict: BLOCKED (wait for cleaner confirmation).")
                for reason in reasons:
                    st.write(f"- {reason}")
        except Exception as error:  # pragma: no cover - UI path
            st.error(_friendly_error_message(error))

    st.divider()
    st.subheader("6) Automatic Paper Trading (No Live Orders)")
    st.caption(
        "Automatic execution is simulation-only. This does not place broker orders."
    )

    if "auto_paper_position" not in st.session_state:
        st.session_state["auto_paper_position"] = None

    auto_instrument = st.text_input("Auto Trader Instrument", value="NSE:NIFTY 50")
    auto_labeled_csv = st.text_input(
        "Analysis source for auto trader",
        value="validation/reports/EXP-001_nifty_live_history_labeled.csv",
    )
    auto_ledger = st.text_input(
        "Auto paper trade log CSV",
        value="paper_trades/auto_paper_trades.csv",
    )
    auto_entry_ledger = st.text_input(
        "Auto paper entry log CSV",
        value="paper_trades/auto_paper_entries.csv",
    )

    ac1, ac2, ac3, ac4 = st.columns(4)
    with ac1:
        auto_stop_loss_pct = st.number_input(
            "Stop-loss percent",
            min_value=1.0,
            max_value=25.0,
            value=10.0,
            step=0.5,
        )
    with ac2:
        auto_profit_trigger = st.number_input(
            "Profit trigger points",
            min_value=1.0,
            max_value=50.0,
            value=10.0,
            step=1.0,
        )
    with ac3:
        auto_trailing = st.number_input(
            "Trailing points",
            min_value=1.0,
            max_value=20.0,
            value=5.0,
            step=1.0,
        )
    with ac4:
        auto_target = st.number_input(
            "Target points",
            min_value=5.0,
            max_value=80.0,
            value=25.0,
            step=1.0,
        )

    use_market_adaptive_risk = st.checkbox(
        "Use market-adaptive SL/target/trailing",
        value=True,
        help="Adjust risk controls every tick based on ATR, confidence, and event flags.",
    )

    config = AutoPaperConfig(
        stop_loss_pct=auto_stop_loss_pct / 100.0,
        profit_trigger_points=auto_profit_trigger,
        trailing_points=auto_trailing,
        target_points=auto_target,
    )

    position_payload = st.session_state.get("auto_paper_position")
    active_position = AutoPaperPosition(**position_payload) if isinstance(position_payload, dict) else None

    if active_position is None:
        st.info("No active paper position.")
    else:
        st.info(
            "Active paper position: "
            f"{active_position.side} "
            f"symbol={active_position.contract_symbol or 'NIFTY-SPOT-PROXY'} "
            f"entry={active_position.entry_price:.2f} "
            f"SL={active_position.stop_loss_price:.2f} "
            f"trail={'on' if active_position.trailing_active else 'off'}"
        )

    auto_check_enabled = st.checkbox(
        "Enable Auto-check",
        value=False,
        help="When enabled, the app checks market bias and updates paper position automatically on the selected interval.",
    )
    auto_check_interval_min = st.selectbox(
        "Auto-check interval (minutes)",
        options=[1, 2, 3],
        index=1,
    )

    def _execute_auto_paper_tick(trigger: str) -> None:
        position_payload_local = st.session_state.get("auto_paper_position")
        current_position = (
            AutoPaperPosition(**position_payload_local)
            if isinstance(position_payload_local, dict)
            else None
        )

        try:
            labeled_path = Path(auto_labeled_csv)
            if not labeled_path.exists():
                raise ValueError("Labeled CSV not found. Run Step 4 first.")

            labeled_bars = pd.read_csv(labeled_path)
            analysis = _compute_short_horizon_bias(
                labeled_bars,
                bias_score_threshold=bias_score_threshold,
            )
            confirmed, reasons = _evaluate_confirmation_checklist(
                analysis,
                min_confidence=min_confidence,
                min_abs_score=min_abs_score,
                allow_failed_breakout=allow_failed_breakout,
                allow_noisy_state=allow_noisy_state,
                allow_compression=allow_compression,
                allow_elevated_volatility=allow_elevated_volatility,
            )

            ltp_response = _fetch_ltp(auto_instrument)
            if auto_instrument not in ltp_response:
                raise ValueError("Could not find instrument in LTP response.")
            spot_price = _to_float(ltp_response[auto_instrument].get("last_price"), 0.0)
            if spot_price <= 0:
                raise ValueError("Invalid current price from LTP response.")

            effective_config = config
            if use_market_adaptive_risk:
                effective_config, adaptive_diag = _build_market_adaptive_config(
                    labeled_bars=labeled_bars,
                    spot_price=spot_price,
                    base_config=config,
                    analysis=analysis,
                )
                st.caption(
                    "Adaptive risk active: "
                    f"SL={effective_config.stop_loss_pct * 100:.2f}% "
                    f"target={effective_config.target_points:.1f} "
                    f"trigger={effective_config.profit_trigger_points:.1f} "
                    f"trail={effective_config.trailing_points:.1f} "
                    f"(atr={adaptive_diag['atr']}, vf={adaptive_diag['vol_factor']})"
                )

            if current_position is None:
                if not confirmed:
                    st.warning(f"{trigger}: no entry, checklist blocked this tick.")
                    for reason in reasons:
                        st.write(f"- {reason}")
                else:
                    bias_text = str(analysis.get("bias", "")).lower()
                    if "bullish" in bias_text:
                        side = "CALL"
                    elif "bearish" in bias_text:
                        side = "PUT"
                    else:
                        st.warning("No entry: directional bias is not tradeable.")
                        side = ""

                    new_position = None
                    if side:
                        selected = _select_live_option_contract(
                            spot_price=spot_price,
                            side=side,
                            asof=datetime.now(timezone.utc),
                        )
                        if selected is None:
                            st.warning(f"{trigger}: no entry, no eligible {side} option contract found.")
                        else:
                            selected_symbol, selected_price = selected
                            new_position = open_auto_paper_position(
                                side,
                                selected_price,
                                effective_config,
                                contract_symbol=selected_symbol,
                            )

                    if new_position is not None:
                        st.session_state["auto_paper_position"] = new_position.to_dict()
                        entry_log_path = append_auto_paper_entry(auto_entry_ledger, new_position)
                        st.success(
                            f"{trigger}: opened {new_position.side} {new_position.contract_symbol} at {new_position.entry_price:.2f}. Entry recorded in {entry_log_path}."
                        )
            else:
                if current_position.contract_symbol:
                    option_key = f"NFO:{current_position.contract_symbol}"
                    option_ltp = _fetch_ltp(option_key)
                    current_price = _to_float(option_ltp.get(option_key, {}).get("last_price"), 0.0)
                    if current_price <= 0:
                        st.warning(f"{trigger}: missing LTP for {option_key}, using spot proxy price.")
                        current_price = spot_price
                else:
                    current_price = spot_price

                update = update_auto_paper_position(current_position, current_price, effective_config)
                if update.status == "closed" and update.closed_trade is not None:
                    st.session_state["auto_paper_position"] = None
                    ledger_path = append_auto_paper_trade(auto_ledger, update.closed_trade)
                    st.success(
                        f"{trigger}: position closed at {current_price:.2f}. Trade recorded in {ledger_path}."
                    )
                else:
                    st.session_state["auto_paper_position"] = update.position.to_dict() if update.position else None
                    st.info(f"{trigger}: {update.message}")
        except Exception as error:  # pragma: no cover - UI path
            st.error(_friendly_error_message(error))

    if st.button("Run Auto Paper Trader Tick", use_container_width=True):
        _execute_auto_paper_tick("Manual tick")

    if auto_check_enabled:
        st.caption(f"Auto-check is active every {auto_check_interval_min} minute(s).")

        @st.fragment(run_every=timedelta(minutes=auto_check_interval_min))
        def _auto_tick_fragment() -> None:
            _execute_auto_paper_tick("Auto tick")

        _auto_tick_fragment()

    st.divider()
    st.markdown("Safety: order placement APIs are disabled in the runtime wrapper.")


if __name__ == "__main__":
    main()
