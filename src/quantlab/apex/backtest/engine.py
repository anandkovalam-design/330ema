"""APEX Pro historical backtest engine.

This engine replays 5-minute candles through ApexPipeline and permits trades
only through typed APEX outputs with paper execution controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from quantlab.apex.agents.base import Candle, DataValidationStatus, DecisionAction, MarketSnapshot, OptionContract
from quantlab.apex.orchestration.pipeline import ApexPipeline


IST = ZoneInfo("Asia/Kolkata")

ALLOWED_APEX_ACTIONS = {
    "TRADE_CALL",
    "TRADE_PUT",
    "WATCH",
    "NO_TRADE",
    "INSUFFICIENT_DATA",
}

ALLOWED_MARKET_STATES = {
    "directional_bullish",
    "directional_bearish",
    "range_bound",
    "volatility_expansion",
    "contraction",
    "breakout_attempt",
    "failed_breakout",
    "reversal",
    "uncertain",
}


@dataclass(frozen=True)
class ApexBacktestConfig:
    underlying_csv: Path
    options_dir: Path | None = None
    no_proxy_options: bool = False
    max_trades_per_day: int = 3
    start_date: str | None = None
    end_date: str | None = None
    audit_dir: Path = Path("reports/apex_audit_backtest")
    trades_csv: Path = Path("reports/apex_backtest_trades.csv")
    summary_md: Path = Path("reports/apex_backtest_summary.md")
    stop_loss_pct: float = 0.10
    target_points: float = 25.0
    trail_activation_points: float = 10.0
    trailing_points: float = 5.0
    time_exit_bars: int = 12


@dataclass
class FrozenMarketSnapshot:
    index: int
    timestamp: datetime
    market_snapshot: MarketSnapshot


@dataclass
class OpenTrade:
    session_date: str
    direction: str
    symbol: str
    strike: int
    expiry: str
    option_type: str
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    trail_activation_price: float
    trailing_active: bool
    trailing_stop: float | None
    high_watermark: float
    low_watermark: float
    bars_open: int
    option_proxy_used: bool
    audit_fields: dict[str, Any]
    entry_spot: float


@dataclass
class ApexBacktestResult:
    trades: pd.DataFrame
    summary_markdown: str
    stopped_early: bool
    stop_reason: str | None
    audit_run_id: str


@dataclass
class _Telemetry:
    skeptic_veto_reasons: dict[str, int] = field(default_factory=dict)
    risk_reject_reasons: dict[str, int] = field(default_factory=dict)
    options_reject_reasons: dict[str, int] = field(default_factory=dict)
    proxy_option_usage_count: int = 0


class ApexProBacktestEngine:
    """Historical 5-minute replay engine using strict APEX pipeline decisions."""

    def __init__(
        self,
        config: ApexBacktestConfig,
        pipeline_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.config = config
        self._pipeline_factory = pipeline_factory or (lambda audit_dir: ApexPipeline(audit_dir=audit_dir))
        self.audit_run_id = self._build_run_id(config)

    @staticmethod
    def _build_run_id(config: ApexBacktestConfig) -> str:
        payload = "|".join(
            [
                str(config.no_proxy_options),
                str(config.max_trades_per_day),
                str(config.start_date or ""),
                str(config.end_date or ""),
                f"sl={config.stop_loss_pct}",
                f"tp={config.target_points}",
                f"trail_act={config.trail_activation_points}",
                f"trail={config.trailing_points}",
                f"time_exit={config.time_exit_bars}",
            ]
        )
        return f"apex-pro-{sha1(payload.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def _to_aware_timestamp(value: object) -> datetime:
        ts = pd.Timestamp(pd.to_datetime(value, errors="coerce"))
        if pd.isna(ts):
            raise ValueError(f"Invalid timestamp value: {value}")
        if ts.tzinfo is None:
            ts = ts.tz_localize(IST)
        return ts.to_pydatetime()

    @staticmethod
    def _to_float(value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _load_underlying(self) -> pd.DataFrame:
        frame = pd.read_csv(self.config.underlying_csv)
        expected = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = [c for c in expected if c not in frame.columns]
        if missing:
            raise ValueError(f"Underlying CSV missing columns: {missing}")

        frame = frame.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if frame["timestamp"].dt.tz is None:
            frame["timestamp"] = frame["timestamp"].dt.tz_localize(IST)
        frame["session_date"] = frame["timestamp"].dt.date.astype(str)

        if self.config.start_date:
            start_ts = pd.Timestamp(self.config.start_date).tz_localize(IST)
            frame = frame[frame["timestamp"] >= start_ts]
        if self.config.end_date:
            end_ts = pd.Timestamp(self.config.end_date).tz_localize(IST) + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
            frame = frame[frame["timestamp"] <= end_ts]

        frame = frame.reset_index(drop=True)
        if frame.empty:
            raise ValueError("No underlying candles in selected date range.")
        return frame

    def _load_options(self) -> pd.DataFrame:
        if self.config.options_dir is None or not self.config.options_dir.exists():
            return pd.DataFrame()

        files = sorted(self.config.options_dir.rglob("*.csv"))
        if not files:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for file in files:
            try:
                raw = pd.read_csv(file)
            except Exception:
                continue
            normalized = self._normalize_option_frame(raw)
            if not normalized.empty:
                frames.append(normalized)

        if not frames:
            return pd.DataFrame()

        frame = pd.concat(frames, ignore_index=True)
        frame = frame.sort_values(["timestamp", "symbol"]).drop_duplicates(
            subset=["timestamp", "symbol"], keep="last"
        )
        return frame.reset_index(drop=True)

    def _normalize_option_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame()

        rename_map = {
            "tradingsymbol": "symbol",
            "instrument_type": "option_type",
            "date": "timestamp",
            "last_price": "close",
            "ltp": "close",
        }
        work = frame.rename(columns={k: v for k, v in rename_map.items() if k in frame.columns}).copy()

        required = {"timestamp", "symbol", "strike", "expiry", "option_type", "close"}
        if not required.issubset(work.columns):
            return pd.DataFrame()

        work["timestamp"] = pd.to_datetime(work["timestamp"], errors="coerce")
        work = work.dropna(subset=["timestamp", "symbol", "strike", "expiry", "option_type", "close"])
        if work["timestamp"].dt.tz is None:
            work["timestamp"] = work["timestamp"].dt.tz_localize(IST)

        work["symbol"] = work["symbol"].astype(str)
        work["strike"] = work["strike"].astype(float).astype(int)
        work["expiry"] = pd.to_datetime(work["expiry"], errors="coerce")
        work = work.dropna(subset=["expiry"])
        work["option_type"] = work["option_type"].astype(str).str.upper()
        work = work[work["option_type"].isin(["CE", "PE"])]

        work["close"] = work["close"].astype(float)
        work["open"] = work["open"].astype(float) if "open" in work.columns else work["close"]
        work["high"] = work["high"].astype(float) if "high" in work.columns else work["close"]
        work["low"] = work["low"].astype(float) if "low" in work.columns else work["close"]
        work["volume"] = work["volume"].astype(float) if "volume" in work.columns else 1000.0

        return work[["timestamp", "symbol", "strike", "expiry", "option_type", "open", "high", "low", "close", "volume"]]

    def _build_candle(self, row: pd.Series) -> Candle:
        return Candle(
            timestamp=self._to_aware_timestamp(row["timestamp"]),
            open=self._to_float(row["open"], 0.0),
            high=self._to_float(row["high"], 0.0),
            low=self._to_float(row["low"], 0.0),
            close=self._to_float(row["close"], 0.0),
            volume=self._to_float(row.get("volume", 0.0), 0.0),
            arrived_at=self._to_aware_timestamp(row["timestamp"]),
        )

    @staticmethod
    def _next_weekly_expiry(day: datetime) -> datetime:
        days_to_tuesday = (1 - day.weekday()) % 7
        expiry = (day + timedelta(days=days_to_tuesday)).replace(hour=15, minute=30, second=0, microsecond=0)
        return expiry

    def _proxy_price(self, spot_price: float, option_type: str, strike: int) -> float:
        intrinsic = max(spot_price - strike, 0.0) if option_type == "CE" else max(strike - spot_price, 0.0)
        return max(20.0, 0.15 * intrinsic + 0.004 * spot_price)

    def _option_chain_for_timestamp(
        self,
        options_frame: pd.DataFrame,
        ts: datetime,
        spot_price: float,
    ) -> tuple[list[OptionContract], bool]:
        if options_frame.empty:
            if self.config.no_proxy_options:
                return [], False
            atm = int(round(spot_price / 50.0) * 50)
            expiry = self._next_weekly_expiry(ts)
            chain = [
                OptionContract(
                    symbol=f"PROXY_{atm}CE",
                    expiry=expiry,
                    strike=atm,
                    option_type="CE",
                    bid=self._proxy_price(spot_price, "CE", atm) * 0.99,
                    ask=self._proxy_price(spot_price, "CE", atm) * 1.01,
                    ltp=self._proxy_price(spot_price, "CE", atm),
                    oi=2000,
                ),
                OptionContract(
                    symbol=f"PROXY_{atm}PE",
                    expiry=expiry,
                    strike=atm,
                    option_type="PE",
                    bid=self._proxy_price(spot_price, "PE", atm) * 0.99,
                    ask=self._proxy_price(spot_price, "PE", atm) * 1.01,
                    ltp=self._proxy_price(spot_price, "PE", atm),
                    oi=2000,
                ),
            ]
            return chain, True

        ts_mask = options_frame["timestamp"] == pd.Timestamp(ts)
        rows = options_frame[ts_mask]
        if rows.empty:
            if self.config.no_proxy_options:
                return [], False
            atm = int(round(spot_price / 50.0) * 50)
            expiry = self._next_weekly_expiry(ts)
            chain = [
                OptionContract(
                    symbol=f"PROXY_{atm}CE",
                    expiry=expiry,
                    strike=atm,
                    option_type="CE",
                    bid=self._proxy_price(spot_price, "CE", atm) * 0.99,
                    ask=self._proxy_price(spot_price, "CE", atm) * 1.01,
                    ltp=self._proxy_price(spot_price, "CE", atm),
                    oi=2000,
                ),
                OptionContract(
                    symbol=f"PROXY_{atm}PE",
                    expiry=expiry,
                    strike=atm,
                    option_type="PE",
                    bid=self._proxy_price(spot_price, "PE", atm) * 0.99,
                    ask=self._proxy_price(spot_price, "PE", atm) * 1.01,
                    ltp=self._proxy_price(spot_price, "PE", atm),
                    oi=2000,
                ),
            ]
            return chain, True

        contracts: list[OptionContract] = []
        for _, row in rows.iterrows():
            ltp = max(self._to_float(row["close"], 0.0), 0.01)
            bid = max(ltp * 0.99, 0.01)
            ask = max(ltp * 1.01, bid)
            contracts.append(
                OptionContract(
                    symbol=str(row["symbol"]),
                    expiry=self._to_aware_timestamp(row["expiry"]),
                    strike=int(row["strike"]),
                    option_type=str(row["option_type"]),
                    bid=bid,
                    ask=ask,
                    ltp=ltp,
                    oi=max(int(self._to_float(row.get("volume", 1000.0), 1000.0)), 1),
                )
            )
        return contracts, False

    def _option_price_at(
        self,
        options_frame: pd.DataFrame,
        symbol: str,
        ts: datetime,
        *,
        spot_price: float,
        option_type: str,
        strike: int,
        allow_proxy: bool,
    ) -> tuple[float | None, bool]:
        if symbol.startswith("PROXY_"):
            if not allow_proxy:
                return None, False
            return self._proxy_price(spot_price, option_type, strike), True

        if options_frame.empty:
            if not allow_proxy:
                return None, False
            return self._proxy_price(spot_price, option_type, strike), True

        rows = options_frame[(options_frame["symbol"] == symbol) & (options_frame["timestamp"] <= pd.Timestamp(ts))]
        if rows.empty:
            if not allow_proxy:
                return None, False
            return self._proxy_price(spot_price, option_type, strike), True

        row = rows.sort_values("timestamp").iloc[-1]
        return max(self._to_float(row["close"], 0.0), 0.01), False

    def _build_snapshot(
        self,
        index: int,
        spot_frame: pd.DataFrame,
        options_frame: pd.DataFrame,
    ) -> tuple[FrozenMarketSnapshot, bool]:
        row = spot_frame.iloc[index]
        ts = self._to_aware_timestamp(row["timestamp"])

        spot_hist = [self._build_candle(r) for _, r in spot_frame.iloc[: index + 1].iterrows()]
        futures_hist = list(spot_hist)

        vix_hist: list[Candle] = []
        vix_base = 15.0
        for i, candle in enumerate(spot_hist):
            prior_close = spot_hist[i - 1].close if i > 0 else candle.open
            delta = abs(candle.close - prior_close) / max(abs(prior_close), 1e-6)
            vix_close = vix_base + min(delta * 1000.0, 10.0)
            vix_hist.append(
                Candle(
                    timestamp=candle.timestamp,
                    open=vix_close,
                    high=vix_close + 0.1,
                    low=max(vix_close - 0.1, 0.1),
                    close=vix_close,
                    volume=1000.0,
                    arrived_at=candle.timestamp,
                )
            )

        option_chain, used_proxy_chain = self._option_chain_for_timestamp(
            options_frame,
            ts,
            spot_price=float(row["close"]),
        )

        snapshot = MarketSnapshot(
            snapshot_id=f"snap-{ts.strftime('%Y%m%dT%H%M')}",
            captured_at=ts,
            nifty_spot=spot_hist,
            nifty_futures=futures_hist,
            india_vix=vix_hist,
            option_chain=option_chain,
        )
        return FrozenMarketSnapshot(index=index, timestamp=ts, market_snapshot=snapshot), used_proxy_chain

    @staticmethod
    def _normalize_reason_codes(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ";".join([str(v) for v in value])
        return str(value)

    @staticmethod
    def _normalize_skeptic_status(report: dict[str, Any]) -> str:
        status_value = report.get("status")
        status = "" if status_value is None else str(status_value).strip()
        if status:
            return status
        return "VETO" if bool(report.get("veto", False)) else "CLEAR"

    @staticmethod
    def _normalize_risk_status(report: dict[str, Any]) -> str:
        if not report:
            return "NOT_RUN"
        status_value = report.get("status")
        status = "" if status_value is None else str(status_value).strip()
        if status:
            return status
        approval = str(report.get("approval", "")).strip()
        return approval or "NOT_RUN"

    @staticmethod
    def _normalize_options_status(report: dict[str, Any]) -> str:
        if not report:
            return "NOT_RUN"
        status_value = report.get("status")
        status = "" if status_value is None else str(status_value).strip()
        if status:
            return status
        if "approved" in report:
            return "APPROVED" if bool(report.get("approved", False)) else "REJECTED"
        return "NOT_RUN"

    @staticmethod
    def _decision_confidence(decision_report: dict[str, Any]) -> float:
        decision_record = decision_report.get("decision_record")
        if isinstance(decision_record, dict) and "confidence" in decision_record:
            try:
                return float(decision_record["confidence"])
            except (TypeError, ValueError):
                pass
        return float(decision_report.get("confidence", 0.0))

    def run(self) -> ApexBacktestResult:
        spot_frame = self._load_underlying()
        options_frame = self._load_options()
        telemetry = _Telemetry()

        run_audit_dir = self.config.audit_dir / self.audit_run_id
        run_audit_dir.mkdir(parents=True, exist_ok=True)
        pipeline = self._pipeline_factory(run_audit_dir)
        pipeline.state.max_trades_per_session = self.config.max_trades_per_day

        rows: list[dict[str, Any]] = []
        open_trade: OpenTrade | None = None
        stopped_early = False
        stop_reason: str | None = None
        current_session: str | None = None
        trades_opened_today = 0

        for i in range(len(spot_frame)):
            row = spot_frame.iloc[i]
            ts = self._to_aware_timestamp(row["timestamp"])
            session_date = str(row["session_date"])
            next_session_date = (
                str(spot_frame.iloc[i + 1]["session_date"]) if i + 1 < len(spot_frame) else session_date
            )
            is_session_end = i + 1 >= len(spot_frame) or next_session_date != session_date
            spot_price = self._to_float(row["close"], 0.0)

            if current_session != session_date:
                current_session = session_date
                pipeline.state.session_risk.trades_today = 0
                pipeline.state.session_risk.daily_realized_pnl = 0.0
                trades_opened_today = 0
                if open_trade is not None:
                    # Ensure no overnight carry.
                    close_price, close_proxy = self._option_price_at(
                        options_frame,
                        open_trade.symbol,
                        ts,
                        spot_price=spot_price,
                        option_type=open_trade.option_type,
                        strike=open_trade.strike,
                        allow_proxy=not self.config.no_proxy_options,
                    )
                    if close_price is None:
                        close_price = open_trade.entry_price
                    rows.append(self._close_trade_row(open_trade, ts, close_price, "EOD_SQUARE_OFF", close_proxy))
                    pipeline.state.session_risk.open_position = None
                    open_trade = None

            if open_trade is not None:
                close_row = self._update_open_trade(
                    open_trade,
                    ts,
                    spot_price,
                    options_frame,
                    is_last=(i == len(spot_frame) - 1),
                    is_session_end=is_session_end,
                )
                if close_row is not None:
                    rows.append(close_row)
                    pipeline.state.session_risk.open_position = None
                    pipeline.state.session_risk.daily_realized_pnl += float(close_row["pnl"])
                    open_trade = None

            frozen_snapshot, used_proxy_chain = self._build_snapshot(i, spot_frame, options_frame)
            result = pipeline.run(frozen_snapshot.market_snapshot)
            reports = result.reports

            market_data = reports.get("market_data", {})
            validation_status = str(market_data.get("validation_status", ""))
            if validation_status == DataValidationStatus.DATA_INVALID.value:
                stopped_early = True
                stop_reason = "DATA_INVALID"
                break

            skeptic_report = reports.get("skeptic", {})
            risk_report = reports.get("risk", {})
            options_report = reports.get("options", {})
            decision_report = reports.get("decision", {})
            execution_report = reports.get("paper_execution", {})
            market_state_report = reports.get("market_state", {})
            bull_report = reports.get("bull_research", {})
            bear_report = reports.get("bear_research", {})

            skeptic_reasons = skeptic_report.get("reason_codes", [])
            if bool(skeptic_report.get("veto", False)):
                for code in skeptic_reasons:
                    telemetry.skeptic_veto_reasons[str(code)] = telemetry.skeptic_veto_reasons.get(str(code), 0) + 1

            risk_status = self._normalize_risk_status(risk_report)
            if risk_status != "APPROVED":
                for code in risk_report.get("reason_codes", []) or []:
                    telemetry.risk_reject_reasons[str(code)] = telemetry.risk_reject_reasons.get(str(code), 0) + 1

            options_status = self._normalize_options_status(options_report)
            options_approved = options_status != "REJECTED"
            if options_status == "REJECTED":
                for code in options_report.get("reason_codes", []) or []:
                    telemetry.options_reject_reasons[str(code)] = telemetry.options_reject_reasons.get(str(code), 0) + 1

            action = str(decision_report.get("action", result.action.value if isinstance(result.action, DecisionAction) else ""))
            executed = bool(execution_report.get("executed", False))
            skeptic_veto = bool(skeptic_report.get("veto", False))

            should_open_trade = (
                action in {DecisionAction.TRADE_CALL.value, DecisionAction.TRADE_PUT.value}
                and risk_status == "APPROVED"
                and executed
                and not skeptic_veto
                and open_trade is None
                and trades_opened_today < self.config.max_trades_per_day
                and not is_session_end
            )
            if not should_open_trade:
                continue

            option_selection = options_report.get("selection", {}) or {}
            option_symbol = str(option_selection.get("contract_symbol", ""))
            option_type = "CE" if action == DecisionAction.TRADE_CALL.value else "PE"
            strike = int(option_selection.get("strike", int(round(spot_price / 50.0) * 50)))
            expiry_val = option_selection.get("expiry")
            if expiry_val:
                expiry = str(pd.Timestamp(expiry_val).date())
            else:
                expiry = str(self._next_weekly_expiry(ts).date())

            allow_proxy = not self.config.no_proxy_options
            entry_price, used_proxy_price = self._option_price_at(
                options_frame,
                option_symbol,
                ts,
                spot_price=spot_price,
                option_type=option_type,
                strike=strike,
                allow_proxy=allow_proxy,
            )

            if entry_price is None:
                continue

            option_proxy_used = bool(used_proxy_chain or used_proxy_price or option_symbol.startswith("PROXY_"))
            if option_proxy_used:
                telemetry.proxy_option_usage_count += 1

            direction = "CALL" if action == DecisionAction.TRADE_CALL.value else "PUT"
            stop_price = entry_price * (1.0 - self.config.stop_loss_pct)
            target_price = entry_price + self.config.target_points
            trail_activation_price = entry_price + self.config.trail_activation_points

            symbol = option_symbol or f"PROXY_{strike}{option_type}"
            if option_proxy_used and not symbol.startswith("PROXY_"):
                symbol = f"PROXY_{strike}{option_type}"

            open_trade = OpenTrade(
                session_date=session_date,
                direction=direction,
                symbol=symbol,
                strike=strike,
                expiry=expiry,
                option_type=option_type,
                entry_time=ts,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                trail_activation_price=trail_activation_price,
                trailing_active=False,
                trailing_stop=None,
                high_watermark=entry_price,
                low_watermark=entry_price,
                bars_open=0,
                option_proxy_used=option_proxy_used,
                entry_spot=spot_price,
                audit_fields={
                    "apex_action": action,
                    "apex_confidence": self._decision_confidence(decision_report),
                    "market_state": str(market_state_report.get("state", market_state_report.get("selected_state", "uncertain"))),
                    "bull_confidence": float(bull_report.get("confidence", 0.0)),
                    "bear_confidence": float(bear_report.get("confidence", 0.0)),
                    "skeptic_status": self._normalize_skeptic_status(skeptic_report),
                    "skeptic_reason_codes": self._normalize_reason_codes(skeptic_reasons),
                    "risk_status": risk_status,
                    "risk_reason_codes": self._normalize_reason_codes(risk_report.get("reason_codes", [])) if risk_report else "",
                    "options_status": options_status,
                    "options_reason_codes": self._normalize_reason_codes(options_report.get("reason_codes", [])) if options_report else "",
                    "audit_run_id": self.audit_run_id,
                },
            )
            trades_opened_today += 1

        if open_trade is not None:
            last_ts = self._to_aware_timestamp(spot_frame.iloc[-1]["timestamp"])
            last_spot = self._to_float(spot_frame.iloc[-1]["close"], 0.0)
            close_price, used_proxy = self._option_price_at(
                options_frame,
                open_trade.symbol,
                last_ts,
                spot_price=last_spot,
                option_type=open_trade.option_type,
                strike=open_trade.strike,
                allow_proxy=not self.config.no_proxy_options,
            )
            if close_price is None:
                close_price = open_trade.entry_price
            rows.append(self._close_trade_row(open_trade, last_ts, close_price, "EOD_SQUARE_OFF", used_proxy))

        trades = pd.DataFrame(rows)
        if not trades.empty:
            trades = trades.sort_values(["date", "entry_time"]).reset_index(drop=True)

        self.config.trades_csv.parent.mkdir(parents=True, exist_ok=True)
        trades.to_csv(self.config.trades_csv, index=False)

        summary = self._build_summary(trades, telemetry)
        self.config.summary_md.parent.mkdir(parents=True, exist_ok=True)
        self.config.summary_md.write_text(summary, encoding="utf-8")

        return ApexBacktestResult(
            trades=trades,
            summary_markdown=summary,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            audit_run_id=self.audit_run_id,
        )

    def _update_open_trade(
        self,
        trade: OpenTrade,
        ts: datetime,
        spot_price: float,
        options_frame: pd.DataFrame,
        *,
        is_last: bool,
        is_session_end: bool,
    ) -> dict[str, Any] | None:
        trade.bars_open += 1
        current_price, used_proxy = self._option_price_at(
            options_frame,
            trade.symbol,
            ts,
            spot_price=spot_price,
            option_type=trade.option_type,
            strike=trade.strike,
            allow_proxy=not self.config.no_proxy_options,
        )
        if current_price is None:
            return None

        trade.high_watermark = max(trade.high_watermark, current_price)
        trade.low_watermark = min(trade.low_watermark, current_price)

        if current_price >= trade.target_price:
            return self._close_trade_row(trade, ts, current_price, "TARGET", used_proxy)

        if (not trade.trailing_active) and current_price >= trade.trail_activation_price:
            trade.trailing_active = True
            trade.trailing_stop = current_price - self.config.trailing_points

        if trade.trailing_active and trade.trailing_stop is not None:
            trade.trailing_stop = max(trade.trailing_stop, trade.high_watermark - self.config.trailing_points)
            if current_price <= trade.trailing_stop:
                return self._close_trade_row(trade, ts, current_price, "TRAILING_STOP", used_proxy)

        if current_price <= trade.stop_price:
            return self._close_trade_row(trade, ts, current_price, "STOP_LOSS", used_proxy)

        if trade.bars_open >= self.config.time_exit_bars:
            return self._close_trade_row(trade, ts, current_price, "TIME_EXIT", used_proxy)

        if is_session_end:
            return self._close_trade_row(trade, ts, current_price, "EOD_SQUARE_OFF", used_proxy)

        if is_last:
            return self._close_trade_row(trade, ts, current_price, "EOD_SQUARE_OFF", used_proxy)

        return None

    def _close_trade_row(
        self,
        trade: OpenTrade,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str,
        used_proxy_exit: bool,
    ) -> dict[str, Any]:
        pnl = float(exit_price - trade.entry_price)
        result = "WIN" if pnl > 0 else "LOSS"
        row = {
            "date": trade.session_date,
            "direction": trade.direction,
            "symbol": trade.symbol,
            "strike": trade.strike,
            "expiry": trade.expiry,
            "option_type": trade.option_type,
            "entry_time": trade.entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "entry_price": round(float(trade.entry_price), 6),
            "exit_price": round(float(exit_price), 6),
            "pnl": round(pnl, 6),
            "result": result,
            "exit_reason": exit_reason,
            "apex_action": trade.audit_fields["apex_action"],
            "apex_confidence": trade.audit_fields["apex_confidence"],
            "market_state": trade.audit_fields["market_state"],
            "bull_confidence": trade.audit_fields["bull_confidence"],
            "bear_confidence": trade.audit_fields["bear_confidence"],
            "skeptic_status": trade.audit_fields["skeptic_status"],
            "skeptic_reason_codes": trade.audit_fields["skeptic_reason_codes"],
            "risk_status": trade.audit_fields["risk_status"],
            "risk_reason_codes": trade.audit_fields["risk_reason_codes"],
            "options_status": trade.audit_fields["options_status"],
            "options_reason_codes": trade.audit_fields["options_reason_codes"],
            "option_proxy_used": bool(trade.option_proxy_used),
            "audit_run_id": trade.audit_fields["audit_run_id"],
        }
        # Enforce non-empty mandatory audit fields for every written trade row.
        if not str(row.get("apex_action", "")).strip():
            raise ValueError("Trade row missing apex_action")
        if not str(row.get("audit_run_id", "")).strip():
            raise ValueError("Trade row missing audit_run_id")

        if row["apex_action"] not in ALLOWED_APEX_ACTIONS:
            raise ValueError(f"Invalid apex_action: {row['apex_action']}")

        market_state = str(row.get("market_state", "")).strip()
        if market_state and market_state not in ALLOWED_MARKET_STATES:
            raise ValueError(f"Invalid market_state: {market_state}")

        if row["apex_action"] in {"TRADE_CALL", "TRADE_PUT"}:
            if row["risk_status"] != "APPROVED":
                raise ValueError("Trade row violates rule: TRADE_* requires risk_status=APPROVED")
            if str(row["skeptic_status"]).upper() == "VETO":
                raise ValueError("Trade row violates rule: TRADE_* cannot have skeptic_status=VETO")
            if row["options_status"] == "REJECTED":
                raise ValueError("Trade row violates rule: TRADE_* cannot have options_status=REJECTED")

        if bool(row["option_proxy_used"]) and not str(row["symbol"]).startswith("PROXY_"):
            raise ValueError("Trade row violates rule: option_proxy_used=True requires PROXY_ symbol")

        if self.config.no_proxy_options and bool(row["option_proxy_used"]):
            raise ValueError("Trade row violates rule: --no-proxy-options forbids proxy usage")

        return row

    def _build_summary(self, trades: pd.DataFrame, telemetry: _Telemetry) -> str:
        total = int(len(trades))
        wins = int((trades["result"] == "WIN").sum()) if total else 0
        losses = int((trades["result"] == "LOSS").sum()) if total else 0
        win_rate = (wins / total * 100.0) if total else 0.0
        net_pnl = float(trades["pnl"].sum()) if total else 0.0
        avg_win = float(trades.loc[trades["pnl"] > 0, "pnl"].mean()) if total and wins else 0.0
        avg_loss = float(trades.loc[trades["pnl"] < 0, "pnl"].mean()) if total and losses else 0.0

        gross_profit = float(trades.loc[trades["pnl"] > 0, "pnl"].sum()) if total else 0.0
        gross_loss = abs(float(trades.loc[trades["pnl"] < 0, "pnl"].sum())) if total else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

        max_drawdown = 0.0
        if total:
            eq = trades["pnl"].cumsum()
            peak = eq.cummax()
            drawdown = peak - eq
            max_drawdown = float(drawdown.max())

        call_perf = self._group_perf(trades, "direction", "CALL")
        put_perf = self._group_perf(trades, "direction", "PUT")
        by_state = self._group_table(trades, "market_state")

        by_hour = pd.DataFrame()
        by_weekday = pd.DataFrame()
        if total:
            entries = pd.to_datetime(trades["entry_time"], errors="coerce")
            trades_with_time = trades.copy()
            trades_with_time["hour"] = entries.dt.hour
            trades_with_time["weekday"] = entries.dt.day_name()
            by_hour = self._group_table(trades_with_time, "hour")
            by_weekday = self._group_table(trades_with_time, "weekday")

        comparison = self._compare_against_non_apex_initial()

        lines: list[str] = []
        lines.append("# APEX Pro Backtest Summary")
        lines.append("")
        lines.append(f"- audit_run_id: {self.audit_run_id}")
        lines.append(f"- total trades: {total}")
        lines.append(f"- wins: {wins}")
        lines.append(f"- losses: {losses}")
        lines.append(f"- win rate: {win_rate:.2f}%")
        lines.append(f"- net pnl: {net_pnl:.2f}")
        lines.append(f"- average win: {avg_win:.2f}")
        lines.append(f"- average loss: {avg_loss:.2f}")
        lines.append(f"- profit factor: {profit_factor:.4f}")
        lines.append(f"- max drawdown: {max_drawdown:.2f}")
        lines.append("")
        lines.append("## CALL performance")
        lines.extend(call_perf)
        lines.append("")
        lines.append("## PUT performance")
        lines.extend(put_perf)
        lines.append("")
        lines.append("## performance by market state")
        lines.extend(self._table_lines(by_state))
        lines.append("")
        lines.append("## performance by hour")
        lines.extend(self._table_lines(by_hour))
        lines.append("")
        lines.append("## performance by weekday")
        lines.extend(self._table_lines(by_weekday))
        lines.append("")
        lines.append("## most common skeptic veto reasons")
        lines.extend(self._counter_lines(telemetry.skeptic_veto_reasons))
        lines.append("")
        lines.append("## most common risk rejection reasons")
        lines.extend(self._counter_lines(telemetry.risk_reject_reasons))
        lines.append("")
        lines.append(f"- proxy option usage count: {telemetry.proxy_option_usage_count}")
        lines.append("")
        lines.append("## comparison against non-APEX initial mode if available")
        lines.extend(comparison)
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _group_perf(trades: pd.DataFrame, column: str, value: str) -> list[str]:
        if trades.empty:
            return ["- no trades"]
        subset = trades[trades[column] == value]
        if subset.empty:
            return ["- no trades"]
        count = len(subset)
        net = float(subset["pnl"].sum())
        wins = int((subset["result"] == "WIN").sum())
        losses = int((subset["result"] == "LOSS").sum())
        return [
            f"- trades: {count}",
            f"- wins: {wins}",
            f"- losses: {losses}",
            f"- net pnl: {net:.2f}",
        ]

    @staticmethod
    def _group_table(trades: pd.DataFrame, key: str) -> pd.DataFrame:
        if trades.empty:
            return pd.DataFrame(columns=[key, "trades", "wins", "losses", "net_pnl"])

        grouped = (
            trades.groupby(key, dropna=False)
            .agg(
                trades=("pnl", "count"),
                wins=("result", lambda x: int((x == "WIN").sum())),
                losses=("result", lambda x: int((x == "LOSS").sum())),
                net_pnl=("pnl", "sum"),
            )
            .reset_index()
        )
        return grouped

    @staticmethod
    def _table_lines(frame: pd.DataFrame) -> list[str]:
        if frame.empty:
            return ["- no data"]
        lines = ["| key | trades | wins | losses | net_pnl |", "|---|---:|---:|---:|---:|"]
        key_col = frame.columns[0]
        for _, row in frame.iterrows():
            lines.append(
                f"| {row[key_col]} | {int(row['trades'])} | {int(row['wins'])} | {int(row['losses'])} | {float(row['net_pnl']):.2f} |"
            )
        return lines

    @staticmethod
    def _counter_lines(counter: dict[str, int]) -> list[str]:
        if not counter:
            return ["- none"]
        pairs = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return [f"- {key}: {value}" for key, value in pairs]

    @staticmethod
    def _compare_against_non_apex_initial() -> list[str]:
        reports_dir = Path("validation/reports")
        if not reports_dir.exists():
            return ["- baseline file not available"]

        candidates = sorted(reports_dir.glob("backtest_*_initial*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return ["- baseline file not available"]

        baseline = candidates[0]
        try:
            frame = pd.read_csv(baseline)
        except Exception:
            return [f"- baseline file unreadable: {baseline}"]

        pnl_column = "pnl_points" if "pnl_points" in frame.columns else ("pnl" if "pnl" in frame.columns else None)
        if pnl_column is None:
            return [f"- baseline file lacks pnl column: {baseline}"]

        net = float(frame[pnl_column].sum()) if not frame.empty else 0.0
        return [
            f"- baseline file: {baseline}",
            f"- baseline trades: {len(frame)}",
            f"- baseline net pnl: {net:.2f}",
        ]
