"""P&L calendar calculations kept separate from Streamlit rendering."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Mapping, Sequence


@dataclass(frozen=True)
class PnlThresholds:
    strong_profit: float = 3000.0
    strong_loss: float = -3000.0


def classify_pnl(trade_count: int, total_pnl: float, thresholds: PnlThresholds) -> str:
    if trade_count <= 0:
        return "no-trade"
    if total_pnl >= thresholds.strong_profit:
        return "strong-profit"
    if total_pnl > 0:
        return "profit"
    if total_pnl <= thresholds.strong_loss:
        return "strong-loss"
    if total_pnl < 0:
        return "loss"
    return "flat"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def monthly_summary(rows: Sequence[Mapping[str, object]], year: int, month: int) -> dict[str, object]:
    days_in_month = calendar.monthrange(year, month)[1]
    trading_rows = [row for row in rows if int(row.get("trade_count", 0)) > 0]
    total_trades = sum(int(row.get("trade_count", 0)) for row in trading_rows)
    winning = sum(int(row.get("winning_trades", 0)) for row in trading_rows)
    losing = sum(int(row.get("losing_trades", 0)) for row in trading_rows)
    best = max(trading_rows, key=lambda row: float(row.get("total_pnl", 0.0)), default=None)
    worst = min(trading_rows, key=lambda row: float(row.get("total_pnl", 0.0)), default=None)
    return {
        "net_pnl": sum(float(row.get("total_pnl", 0.0)) for row in trading_rows),
        "trading_days": len(trading_rows),
        "profit_days": sum(float(row.get("total_pnl", 0.0)) > 0 for row in trading_rows),
        "loss_days": sum(float(row.get("total_pnl", 0.0)) < 0 for row in trading_rows),
        "no_trade_days": days_in_month - len(trading_rows),
        "best_day": best,
        "worst_day": worst,
        "total_trades": total_trades,
        "winning_trades": winning,
        "losing_trades": losing,
        "win_rate": (winning / total_trades * 100.0) if total_trades else 0.0,
    }


def calendar_html(year: int, month: int, rows: Sequence[Mapping[str, object]], thresholds: PnlThresholds) -> str:
    by_day = {int(str(row["trade_date"])[-2:]): row for row in rows}
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    colors = {
        "no-trade": ("#ffffff", "#263238"),
        "flat": ("#eef2f4", "#263238"),
        "profit": ("#c8e6c9", "#145a20"),
        "strong-profit": ("#2e7d32", "#ffffff"),
        "loss": ("#ffcdd2", "#8a1c1c"),
        "strong-loss": ("#c62828", "#ffffff"),
    }
    cells: list[str] = []
    for week in weeks:
        for day_number in week:
            if day_number == 0:
                cells.append('<div class="pnl-day pnl-empty"></div>')
                continue
            row = by_day.get(day_number, {})
            pnl = float(row.get("total_pnl", 0.0))
            count = int(row.get("trade_count", 0))
            category = classify_pnl(count, pnl, thresholds)
            background, foreground = colors[category]
            pnl_text = "No trades" if count == 0 else f"₹{pnl:,.2f}"
            cells.append(
                f'<div class="pnl-day" style="background:{background};color:{foreground}">'
                f'<strong>{day_number}</strong><span>{escape(pnl_text)}</span></div>'
            )
    headings = "".join(f"<div class='pnl-heading'>{day}</div>" for day in calendar.day_abbr)
    return (
        "<style>.pnl-calendar{display:grid;grid-template-columns:repeat(7,minmax(72px,1fr));gap:6px}"
        ".pnl-heading{text-align:center;font-weight:700;color:#9cffbd}.pnl-day{min-height:78px;"
        "padding:8px;border-radius:8px;border:1px solid #78909c;display:flex;flex-direction:column;"
        "justify-content:space-between}.pnl-day span{font-size:.82rem}.pnl-empty{opacity:.12}</style>"
        f"<div class='pnl-calendar'>{headings}{''.join(cells)}</div>"
    )
