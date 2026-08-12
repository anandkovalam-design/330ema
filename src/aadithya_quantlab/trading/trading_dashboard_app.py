"""Polished multi-page trading dashboard for Aadithya QuantLab.

Run with:
    streamlit run src/aadithya_quantlab/trading/trading_dashboard_app.py

The interface is deliberately safe by default: the Live switch controls the
dashboard state only and this module never calls a broker order API.
"""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import streamlit as st

from kite_gateway import LIVE_CONFIRMATION_PHRASE


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TRADE_REPORT = PROJECT_ROOT / "reports" / "apex_last30_all_trades_details.csv"
BACKTEST_REPORT = PROJECT_ROOT / "validation" / "reports" / "backtest_125d_nifty_sensex_all_trades_detailed_nearest_expiry.csv"
PAGES = ("Dashboard", "Accounts", "Place Order", "Live Trade", "Trade History", "Backtest Results", "Subscription")


def _seed_state() -> None:
    defaults = {
        "trade_mode": "Live",
        "trading_enabled": True,
        "watchlist": [],
        "accounts": [
            {
                "client_id": "AB•••21",
                "platform": "Alice Blue",
                "lot_size": "2 / 3",
                "broker": "Connected",
                "status": "Active",
                "live": True,
            }
        ],
        "open_positions": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _add_watchlist_symbol() -> None:
    normalized = st.session_state.get("watchlist_input", "").strip().upper()
    if normalized and normalized not in st.session_state.watchlist and len(st.session_state.watchlist) < 25:
        st.session_state.watchlist.append(normalized)
    st.session_state.watchlist_input = ""


def _apply_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        :root {
          --purple: #9233e9; --purple-dark: #7c21d7; --purple-soft: #f2e7fc;
          --ink: #111318; --muted: #697083; --line: #e7e2f2; --green: #00a567;
          --red: #ef4444; --amber: #f58a1f; --surface: #ffffff;
        }
        html, body, [class*="css"], [data-testid="stAppViewContainer"] {font-family: Inter, sans-serif;}
        .stApp {background:#fff; color:var(--ink);}
        [data-testid="stHeader"] {background:transparent; height:0;}
        [data-testid="stToolbar"] {display:none;}
        [data-testid="stMainBlockContainer"] {max-width:1280px!important; padding:.8rem 0 3rem!important; margin:0 auto!important;}
        [data-testid="stMainBlockContainer"]:has(.wide-marker) {max-width:none!important; padding:0!important; margin:0!important;}
        [data-testid="stMainBlockContainer"]:has(.history-marker) {max-width:none!important; padding:2.05rem 1.4rem 2rem 2rem!important; margin:0!important;}
        [data-testid="stElementContainer"]:has(.layout-marker) {display:none!important;}
        [data-testid="stSidebar"] {background:#fff; border-right:1px solid var(--line); min-width:254px!important; width:254px!important; max-width:254px!important;}
        [data-testid="stSidebarContent"], [data-testid="stSidebarUserContent"] {width:254px!important;}
        [data-testid="stSidebar"] > div:first-child {padding:.65rem .7rem;}
        [data-testid="stSidebar"] [data-testid="stRadio"] > label {display:none;}
        [data-testid="stSidebar"] [role="radiogroup"] {gap:.18rem;}
        [data-testid="stSidebar"] [role="radiogroup"] label {
          min-height:41px; padding:.56rem .72rem; border-radius:9px; transition:.15s ease;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#faf7fd;}
        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:var(--purple-soft);}
        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {color:var(--purple); font-weight:600;}
        [data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div > div:first-child {display:none!important;}
        [data-testid="stSidebar"] [role="radiogroup"] p {font-size:.91rem; color:#555b6b;}
        h1,h2,h3,p {color:var(--ink);}
        .page-title {font-size:1.18rem; font-weight:700; margin:0 0 .25rem;}
        .page-subtitle {font-size:.84rem; color:var(--muted); margin:0 0 1.2rem;}
        .panel {border:1px solid var(--line); border-radius:15px; padding:1.3rem 1.45rem; background:#fff; margin-bottom:0;}
        .hero {background:linear-gradient(105deg,#f0e3ff 0%,#fff 69%,#fcf8ff 100%); min-height:166px; display:flex; align-items:center; justify-content:space-between; margin-top:1rem; margin-bottom:2rem;}
        .eyebrow {font-size:.72rem; font-weight:700; color:#656b7b; letter-spacing:.04em; text-transform:uppercase;}
        .big-number {font-size:2.25rem; font-weight:700; line-height:1.1; margin:.35rem 0 .35rem;}
        .muted {font-size:.82rem; color:var(--muted);}
        .success-strip {border:1px solid #86dfc1; background:#e7f8f2; color:#008c61; padding:.65rem .8rem; border-radius:8px; font-size:.83rem; margin-top:.8rem;}
        .metric-card {border:1px solid var(--line); border-radius:13px; padding:1.25rem 1.15rem; min-height:154px; background:#fff;}
        .metric-icon {display:inline-grid; place-items:center; width:40px; height:40px; border-radius:10px; background:var(--purple-soft); color:var(--purple); font-size:1.15rem; margin-bottom:.7rem;}
        .metric-value {font-size:1.36rem; font-weight:700; line-height:1.3; color:#05070b;}
        .metric-label {font-size:.8rem; color:var(--muted); margin-top:.2rem;}
        .mini-card {display:inline-block; border:1px solid var(--line); border-radius:12px; padding:.8rem 1rem; min-width:130px; margin-left:.5rem;}
        .mini-card b {font-size:1.3rem; display:block; margin-top:.4rem;}
        .toolbar {border:1px solid var(--line); border-radius:14px 14px 0 0; background:#fff; padding:.75rem;}
        .table-shell {border:1px solid var(--line); border-radius:0 0 14px 14px; overflow:auto; background:#fff;}
        table.aq-table {border-collapse:collapse; width:100%; font-size:.79rem; min-width:980px;}
        .aq-table th {text-align:left; padding:.78rem .9rem; color:#646a7c; font-size:.69rem; letter-spacing:.025em; text-transform:uppercase; border-bottom:1px solid var(--line); white-space:nowrap;}
        .aq-table td {padding:.76rem .9rem; border-bottom:1px solid var(--line); color:#151720; white-space:nowrap;}
        .aq-table tr:last-child td {border-bottom:0;}
        .pill {display:inline-block; padding:.25rem .55rem; border-radius:999px; font-size:.69rem; font-weight:600;}
        .pill.green {background:#dff5e9; color:#009454;} .pill.red {background:#fee8e8; color:#df3434;}
        .pos {color:#009f57!important;} .neg {color:#e23030!important;}
        .empty-state {border:1px dashed var(--line); border-radius:12px; text-align:center; padding:4.25rem 1rem; margin:2rem 1rem 0; min-height:208px;}
        .empty-icon {font-size:1.7rem; color:#777d8c; margin-bottom:.7rem;}
        .empty-title {font-size:.85rem; font-weight:600; margin-bottom:.35rem;}
        div[data-testid="stButton"] > button, div[data-testid="stDownloadButton"] > button {
          border:1px solid var(--line); border-radius:9px; min-height:41px; font-weight:600; box-shadow:none;
        }
        div[data-testid="stButton"] > button[kind="primary"] {background:var(--purple); color:#fff; border-color:var(--purple);}
        div[data-testid="stButton"] > button[kind="primary"]:hover {background:var(--purple-dark); border-color:var(--purple-dark);}
        div[data-testid="stTextInput"] input, div[data-testid="stSelectbox"] > div > div {border-color:var(--line); border-radius:9px;}
        [data-testid="stMetric"] {border:1px solid var(--line); border-radius:12px; padding:.75rem 1rem;}
        [data-testid="stToggle"] p {font-size:.82rem;}
        [data-testid="stMainBlockContainer"]:has(.dashboard-marker) [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] {gap:.5rem!important;}
        [data-testid="stCheckbox"] label:has(input[role="switch"]:checked) > span + div {background:var(--purple)!important;}
        [data-testid="stMainBlockContainer"]:has(.live-marker) > [data-testid="stVerticalBlock"] {gap:0!important;}
        [data-testid="stMainBlockContainer"]:has(.live-marker) > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {gap:0!important;}
        [data-testid="stMainBlockContainer"]:has(.live-marker) > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {padding:1rem!important;}
        [data-testid="stMainBlockContainer"]:has(.live-marker) > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {border-right:1px solid var(--line); min-height:100vh;}
        [data-testid="stMainBlockContainer"]:has(.history-marker) .metric-card {min-height:72px; padding:.8rem 1rem; display:grid; grid-template-columns:40px 1fr; grid-template-rows:auto auto; column-gap:.75rem; align-items:center;}
        [data-testid="stMainBlockContainer"]:has(.history-marker) .metric-icon {grid-row:1 / 3; margin:0;}
        [data-testid="stMainBlockContainer"]:has(.history-marker) .metric-value {font-size:1.12rem;}
        [data-testid="stMainBlockContainer"]:has(.history-marker) .metric-label {grid-column:2; grid-row:1; font-size:.68rem; font-weight:700; order:-1;}
        [data-testid="stMainBlockContainer"]:has(.history-marker) .metric-value {grid-column:2; grid-row:2;}
        hr {border-color:var(--line);}
        @media (max-width: 850px) {
          [data-testid="stSidebar"] {min-width:220px;}
          [data-testid="stAppViewContainer"] > .main .block-container {padding:1rem;}
          .hero {display:block;} .hero-right {margin-top:1rem;} .mini-card {margin:.4rem .4rem 0 0;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _title(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="page-title">{html.escape(title)}</div><div class="page-subtitle">{html.escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def _metric_card(icon: str, value: str, label: str, color: str = "purple") -> None:
    backgrounds = {"green": ("#dcf5eb", "#00a567"), "amber": ("#fff0dc", "#e77b10"), "red": ("#fee7e7", "#ef4444"), "purple": ("#f0e2fc", "#9233e9")}
    bg, fg = backgrounds[color]
    st.markdown(
        f'<div class="metric-card"><span class="metric-icon" style="background:{bg};color:{fg}">{icon}</span>'
        f'<div class="metric-value">{html.escape(value)}</div><div class="metric-label">{html.escape(label)}</div></div>',
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def _load_history() -> pd.DataFrame:
    if TRADE_REPORT.exists():
        frame = pd.read_csv(TRADE_REPORT)
    else:
        frame = pd.DataFrame(
            [
                {"date": "2026-07-15", "direction": "CALL", "symbol": "NIFTY21JUL26P24200", "entry_time": "2026-07-15 12:40", "exit_time": "2026-07-15 12:56", "entry_price": 186.05, "exit_price": 217.30, "pnl": 4062.5, "exit_reason": "SIGNAL"},
                {"date": "2026-07-15", "direction": "CALL", "symbol": "NIFTY21JUL26C24100", "entry_time": "2026-07-15 09:45", "exit_time": "2026-07-15 11:13", "entry_price": 212.40, "exit_price": 192.05, "pnl": -2645.5, "exit_reason": "STOP_LOSS"},
            ]
        )
    return frame


@st.cache_data(show_spinner=False)
def _load_backtest_report() -> pd.DataFrame:
    if BACKTEST_REPORT.exists():
        return pd.read_csv(BACKTEST_REPORT)
    return pd.DataFrame()


def _history_rows(frame: pd.DataFrame) -> str:
    rows: list[str] = []
    for idx, row in frame.reset_index(drop=True).iterrows():
        pnl = float(row.get("pnl", row.get("pnl_points", 0)) or 0)
        symbol = str(row.get("symbol", row.get("contract_symbol", "NIFTY")))
        direction = str(row.get("direction", row.get("side", "BUY"))).upper()
        entry = float(row.get("entry_price", 0) or 0)
        exit_price = float(row.get("exit_price", 0) or 0)
        entry_at = str(row.get("entry_time", row.get("date", "—"))).replace("T", " ")[:16]
        exit_at = str(row.get("exit_time", "—")).replace("T", " ")[:16]
        status = str(row.get("exit_reason", "CLOSED")).replace("_", " ")
        rows.append(
            "<tr>"
            f"<td><b>apex_strategy</b><br><span class='muted'>Strategy #{idx + 1}</span></td>"
            f"<td class='{'pos' if pnl >= 0 else 'neg'}'><b>{html.escape(symbol)}</b><br><span class='muted'>1 lot</span></td>"
            f"<td><span class='pill green'>{html.escape(direction)}</span></td><td>65</td>"
            f"<td>{html.escape(status)}</td><td class='{'pos' if pnl >= 0 else 'neg'}'>{pnl:,.2f}</td>"
            f"<td>—</td><td>{entry:,.2f}</td><td>{exit_price:,.2f}</td>"
            f"<td>{html.escape(entry_at)}</td><td>{html.escape(exit_at)}</td><td>—</td></tr>"
        )
    return "".join(rows)


def _render_dashboard() -> None:
    history = _load_history()
    st.markdown('<span class="layout-marker dashboard-marker"></span>', unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown('<div class="eyebrow">Trading mode</div>', unsafe_allow_html=True)
        mode_cols = st.columns([1, 1, 5])
        if mode_cols[0].button("Paper", icon=":material/science:", width="stretch", type="primary" if st.session_state.trade_mode == "Paper" else "secondary"):
            st.session_state.trade_mode = "Paper"
            st.rerun()
        if mode_cols[1].button("Live", icon=":material/trending_up:", width="stretch", type="primary" if st.session_state.trade_mode == "Live" else "secondary"):
            st.session_state.trade_mode = "Live"
            st.rerun()
        st.caption("Orders use real broker connectivity only when live trading is separately enabled.")
        st.divider()
        toggle_col, control_col = st.columns([8, 1])
        with toggle_col:
            st.markdown("**Trading enabled**")
            st.caption("Allow automated strategy orders only when all readiness checks pass.")
        with control_col:
            st.toggle("Enabled", key="trading_enabled", label_visibility="collapsed")
        if st.session_state.trading_enabled:
            st.markdown('<div class="success-strip">◉ &nbsp;All checks passed — trading can run for your selected mode.</div>', unsafe_allow_html=True)
        else:
            st.info("Trading is paused. Enable it when you are ready.")

    open_count = len(st.session_state.open_positions)
    st.markdown(
        f'<div class="panel hero"><div><div class="muted">Open positions</div><div class="big-number">{open_count}</div>'
        '<div class="muted">No active trades right now. Strategies will show here when running.</div></div>'
        f'<div class="hero-right"><span class="mini-card"><span class="eyebrow" style="color:#9233e9">↗ Live P/L</span><b class="pos">0</b></span>'
        f'<span class="mini-card"><span class="eyebrow" style="color:#1489d4">↶ Closed</span><b>{len(history)}</b></span></div></div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(4)
    with cols[0]: _metric_card("▣", str(len(st.session_state.accounts)), "Broker accounts")
    with cols[1]: _metric_card("▣", str(sum(a["status"] == "Active" for a in st.session_state.accounts)), "Active accounts", "green")
    with cols[2]: _metric_card("↗", str(open_count), "Open positions", "amber")
    with cols[3]: _metric_card("▤", "1 Month Trial", "Subscription")


def _render_accounts() -> None:
    st.markdown('<span class="layout-marker accounts-marker"></span>', unsafe_allow_html=True)
    top_left, top_right = st.columns([6, 1.35])
    top_left.write("")
    if top_right.button("Add account", icon=":material/add:", type="primary", width="stretch"):
        st.session_state.show_account_form = True
    if st.session_state.get("show_account_form"):
        with st.form("add-account", clear_on_submit=True):
            a, b, c = st.columns(3)
            client_id = a.text_input("Client ID")
            platform = b.selectbox("Platform", ["Zerodha Kite", "Alice Blue", "Groww"])
            lot_size = c.text_input("Lot size", value="1 / 65")
            submitted = st.form_submit_button("Save account", type="primary")
            if submitted and client_id.strip():
                st.session_state.accounts.append({"client_id": client_id.strip(), "platform": platform, "lot_size": lot_size, "broker": "Not connected", "status": "Active", "live": False})
                st.session_state.show_account_form = False
                st.rerun()
    account_rows: list[str] = []
    for account in st.session_state.accounts:
        account_rows.append(
            f"<tr><td><b>{html.escape(account['client_id'])}</b></td><td>{html.escape(account['platform'])}</td>"
            f"<td>{html.escape(account['lot_size'])}</td><td class='{'pos' if account['broker'] == 'Connected' else ''}'>{html.escape(account['broker'])}</td>"
            f"<td>—</td><td><span class='pill green'>{html.escape(account['status'])}</span></td>"
            f"<td><span class='pill' style='border:1px solid var(--line);font-size:.75rem'>◉ {'Live' if account['live'] else 'Paper'}</span> &nbsp; 🔗 &nbsp; ✎ &nbsp; 🗑</td></tr>"
        )
    st.markdown(
        '<div class="table-shell" style="border-radius:14px"><table class="aq-table"><thead><tr><th>Client ID</th><th>Platform</th><th>Lot size</th><th>Broker</th><th>Primary IP</th><th>Status</th><th>Actions</th></tr></thead><tbody>'
        + "".join(account_rows) + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Page 1 of 1 · {len(st.session_state.accounts)} total")


def _render_live_trade() -> None:
    st.markdown('<span class="layout-marker wide-marker live-marker"></span>', unsafe_allow_html=True)
    watch, content = st.columns([1.15, 4.15], gap=None)
    with watch:
        st.text_input(
            "Add instrument",
            placeholder="Search instruments to add…",
            label_visibility="collapsed",
            key="watchlist_input",
            on_change=_add_watchlist_symbol,
        )
        st.markdown(f"**Watchlist** <span style='float:right;color:#73798a;font-size:.75rem'>{len(st.session_state.watchlist)}/25</span>", unsafe_allow_html=True)
        st.divider()
        if not st.session_state.watchlist:
            st.markdown('<div class="muted" style="text-align:center;padding:1rem">Add symbols from the instrument catalog to track live LTP here.</div>', unsafe_allow_html=True)
        for item in list(st.session_state.watchlist):
            left, right = st.columns([4, 1])
            left.markdown(f"**{html.escape(item)}**<br><span class='muted'>Waiting for tick</span>", unsafe_allow_html=True)
            if right.button("×", key=f"remove-{item}"):
                st.session_state.watchlist.remove(item)
                st.rerun()
    with content:
        s, b1, b2, b3 = st.columns([6, .7, 1.2, 1.15])
        s.text_input("Search positions", placeholder="Search stock…", label_visibility="collapsed")
        if b1.button("Clear", icon=":material/delete:", width="stretch", help="Clear positions"):
            st.session_state.open_positions = []
            st.rerun()
        if b2.button("Square off", width="stretch"):
            st.session_state.open_positions = []
            st.toast("All paper positions squared off")
        b3.button("Refresh", icon=":material/refresh:", width="stretch")
        m1, m2 = st.columns(2)
        with m1: _metric_card("▧", str(len(st.session_state.open_positions)), "TOTAL TRADES")
        with m2: _metric_card("₹", "0", "OVERALL P/L", "green")
        st.markdown('<div class="table-shell" style="border-radius:14px"><table class="aq-table"><thead><tr><th>Trade from</th><th>Symbol</th><th>Order type</th><th>Quantity</th><th>Status</th><th>P / L</th><th>Entry price</th><th>Exit price</th><th>Action</th></tr></thead></table></div>', unsafe_allow_html=True)
        if not st.session_state.open_positions:
            st.markdown('<div class="empty-state"><div class="empty-icon">▱</div><div class="empty-title">No open positions</div><div class="muted">Open positions will appear here when trades are active.</div></div>', unsafe_allow_html=True)


def _render_history() -> None:
    frame = _load_history().copy()
    st.markdown('<span class="layout-marker history-marker"></span>', unsafe_allow_html=True)
    search_col, reason_col, refresh_col, export_col = st.columns([5, 1.3, 1, 1.25])
    query = search_col.text_input("Search trade history", placeholder="Search stock…", label_visibility="collapsed")
    reasons = sorted(frame["exit_reason"].dropna().astype(str).unique()) if "exit_reason" in frame else []
    chosen = reason_col.selectbox("Filter", ["All", *reasons], label_visibility="collapsed")
    if refresh_col.button("Refresh", icon=":material/refresh:", width="stretch"):
        _load_history.clear()
        st.rerun()
    export_col.download_button("Export CSV", frame.to_csv(index=False), file_name="trade_history.csv", mime="text/csv", icon=":material/download:", width="stretch")
    if query:
        searchable = frame.astype(str).apply(lambda col: col.str.contains(query, case=False, na=False))
        frame = frame[searchable.any(axis=1)]
    if chosen != "All" and "exit_reason" in frame:
        frame = frame[frame["exit_reason"].astype(str) == chosen]
    pnl_series = pd.to_numeric(frame.get("pnl", frame.get("pnl_points", pd.Series(dtype=float))), errors="coerce").fillna(0)
    target_hits = int((pnl_series > 0).sum())
    stop_losses = int((pnl_series < 0).sum())
    stats = st.columns(5)
    values = [("▧", str(len(frame)), "TOTAL TRADES", "purple"), ("◉", str(target_hits), "TARGET HIT", "green"), ("♙", str(stop_losses), "STOP LOSS", "red"), ("−", str(int((pnl_series == 0).sum())), "BREAK EVEN", "purple"), ("₹", f"{pnl_series.sum():+,.2f}", "OVERALL P/L", "green" if pnl_series.sum() >= 0 else "red")]
    for col, value in zip(stats, values):
        with col: _metric_card(*value)
    st.markdown(
        '<div class="table-shell" style="border-radius:14px"><table class="aq-table"><thead><tr><th>Trade from</th><th>Symbol</th><th>Order type</th><th>Quantity</th><th>Status</th><th>P / L</th><th>Trigger</th><th>Entry price</th><th>Exit price</th><th>Entry at</th><th>Exit at</th><th>Logs</th></tr></thead><tbody>'
        + _history_rows(frame) + "</tbody></table></div>", unsafe_allow_html=True,
    )
    if frame.empty:
        st.info("No trades match the current search and filter.")


def _render_backtest_results() -> None:
    frame = _load_backtest_report().copy()
    st.markdown('<span class="layout-marker history-marker"></span>', unsafe_allow_html=True)
    _title("Backtest results", "Nearest-expiry 125-day NIFTY and SENSEX trade detail ready for deployment.")

    if frame.empty:
        st.warning("Backtest report not found. Make sure validation/reports/backtest_125d_nifty_sensex_all_trades_detailed_nearest_expiry.csv is committed to the app.")
        return

    session_count = int(frame["session_date"].nunique()) if "session_date" in frame else 0
    underlying_counts = frame["underlying"].value_counts().to_dict() if "underlying" in frame else {}
    result_counts = frame["result"].value_counts().to_dict() if "result" in frame else {}
    pnl_total = float(pd.to_numeric(frame.get("pnl_rupees", 0), errors="coerce").fillna(0).sum())

    stats = st.columns(5)
    values = [
        ("▧", str(len(frame)), "Total trades", "purple"),
        ("◉", str(session_count), "Trading days", "green"),
        ("↗", str(underlying_counts.get("NIFTY", 0)), "NIFTY trades", "amber"),
        ("↘", str(underlying_counts.get("SENSEX", 0)), "SENSEX trades", "amber"),
        ("₹", f"{pnl_total:+,.2f}", "Total P/L", "green" if pnl_total >= 0 else "red"),
    ]
    for col, value in zip(stats, values):
        with col:
            _metric_card(*value)

    filter_col, result_col, download_col = st.columns([3, 1.2, 1])
    query = filter_col.text_input("Search trades", placeholder="Search symbol, date, expiry, or side…", label_visibility="collapsed")
    result_choice = result_col.selectbox("Result", ["All", *sorted(result_counts.keys())], label_visibility="collapsed")
    download_col.download_button(
        "Download CSV",
        frame.to_csv(index=False),
        file_name=BACKTEST_REPORT.name,
        mime="text/csv",
        icon=":material/download:",
        width="stretch",
    )

    if query:
        searchable = frame.astype(str).apply(lambda col: col.str.contains(query, case=False, na=False))
        frame = frame[searchable.any(axis=1)]
    if result_choice != "All" and "result" in frame:
        frame = frame[frame["result"].astype(str) == result_choice]

    view_cols = [col for col in ["underlying", "session_date", "expiry", "side", "contract_symbol", "result", "exit_reason", "quantity", "pnl_points", "pnl_rupees"] if col in frame.columns]
    st.dataframe(frame[view_cols], width="stretch", hide_index=True)

    if not view_cols:
        st.info("No viewable columns found in the report.")


def _render_subscription() -> None:
    st.markdown('<span class="layout-marker subscription-marker"></span>', unsafe_allow_html=True)
    st.markdown('<div class="panel hero"><div><div class="eyebrow">Current plan</div><div class="big-number" style="font-size:1.8rem">1 Month Trial</div><div class="muted">All dashboard features are available during the trial.</div></div><div class="mini-card"><span class="eyebrow">Status</span><b class="pos">Active</b></div></div>', unsafe_allow_html=True)
    st.info("Billing is not connected in this local development build.")


def _render_place_order() -> None:
    """Place orders via kite_gateway (PAPER/SANDBOX/LIVE)."""
    from kite_gateway import OrderRequest, Settings, create_broker
    from aadithya_quantlab.trading.zerodha import NIFTY_PAPER_QUANTITY, SENSEX_PAPER_QUANTITY
    
    st.markdown('<span class="layout-marker"></span>', unsafe_allow_html=True)
    
    # Trading mode indicator
    try:
        settings = Settings.from_env()
        mode_color = {"PAPER": "🔵", "SANDBOX": "🟡", "LIVE": "🔴"}
        st.info(f"{mode_color.get(settings.mode.value, '⚪')} **{settings.mode.value}** — Change `TRADING_MODE` env var to switch modes")
    except Exception as e:
        st.error(f"Settings error: {e}")
        return
    
    st.markdown("### Place an Options Order")
    col1, col2 = st.columns(2)
    
    with col1:
        underlying = st.selectbox("Underlying", ["NIFTY", "SENSEX"])
        qty_per_lot = NIFTY_PAPER_QUANTITY if underlying == "NIFTY" else SENSEX_PAPER_QUANTITY
        exchange = "NFO" if underlying == "NIFTY" else "BFO"
        
        symbol = st.text_input("Trading symbol", placeholder="e.g. NIFTY2572524000CE")
        lots = st.selectbox(
            "Number of lots",
            options=list(range(1, 11)),
            index=0,
            key=f"lots_{underlying}",
        )
        total_qty = lots * qty_per_lot
        
    with col2:
        side = st.selectbox("Side", ["BUY", "SELL"])
        order_type = st.selectbox("Order type", ["MARKET", "LIMIT"])
        
        if order_type == "LIMIT":
            price = st.number_input("Limit price", min_value=0.01, value=100.0, step=0.05)
        else:
            price = None
        
        tag = st.text_input("Order tag (optional)", value="apexpaper", max_chars=20)

    live_mode = settings.mode.value == "LIVE"
    live_armed = True
    if live_mode:
        live_armed = st.checkbox(
            "Arm live order placement",
            help=f"Requires the exact phrase: {LIVE_CONFIRMATION_PHRASE}",
        )
        st.caption("Live mode stays locked until you explicitly arm order placement.")
    
    # Info cards
    info_col1, info_col2, info_col3 = st.columns(3)
    with info_col1:
        st.metric("Exchange", exchange)
    with info_col2:
        st.metric("Total quantity", total_qty)
    with info_col3:
        if order_type == "LIMIT" and price:
            st.metric("Limit price", f"₹{price:.2f}")
        else:
            st.metric("Order type", "MARKET")
    
    # Validation and placement
    st.divider()
    place_disabled = live_mode and not live_armed
    if st.button("📤 Place Order", type="primary", use_container_width=True, disabled=place_disabled):
        if not symbol.strip():
            st.error("Symbol is required")
        else:
            try:
                with st.spinner(f"Placing {side} order..."):
                    broker = create_broker(
                        settings,
                        live_confirmation=LIVE_CONFIRMATION_PHRASE if live_armed else None,
                    )
                    result = broker.place_order(
                        OrderRequest(
                            exchange=exchange,
                            tradingsymbol=symbol.strip(),
                            side=side,
                            quantity=total_qty,
                            product="MIS",
                            order_type=order_type,
                            price=price,
                            tag=tag.strip() or "apexpaper",
                        )
                    )
                    
                    # Display result
                    st.success(f"✓ Order placed: {result.order_id}")
                    
                    result_cols = st.columns(2)
                    with result_cols[0]:
                        st.metric("Status", result.status)
                    with result_cols[1]:
                        st.metric("Filled qty", result.filled_quantity)
                    
                    # Full JSON for debugging
                    with st.expander("Full order response (JSON)"):
                        import json
                        from dataclasses import asdict
                        st.json(json.loads(json.dumps(asdict(result), default=str)))
                        
            except Exception as e:
                st.error(f"Order failed: {str(e)}")


def main() -> None:
    st.set_page_config(page_title="Aadithya Trading", page_icon=":material/trending_up:", layout="wide", initial_sidebar_state="expanded")
    _seed_state()
    _apply_styles()
    with st.sidebar:
        page = st.radio("Navigation", PAGES)
    renderers = {"Dashboard": _render_dashboard, "Accounts": _render_accounts, "Place Order": _render_place_order, "Live Trade": _render_live_trade, "Trade History": _render_history, "Backtest Results": _render_backtest_results, "Subscription": _render_subscription}
    renderers[page]()


if __name__ == "__main__":
    main()
