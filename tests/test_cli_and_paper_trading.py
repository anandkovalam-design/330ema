from pathlib import Path

from aadithya_quantlab.data.samples import make_synthetic_intraday_bars
from aadithya_quantlab.experiments.run_exp001 import main as run_exp001_main
from aadithya_quantlab.trading.paper import PaperLedger
from aadithya_quantlab.trading.run_paper_order import main as run_paper_order_main
from aadithya_quantlab.trading.zerodha import (
    NIFTY_PAPER_QUANTITY,
    build_kite_login_url,
    build_nifty_option_paper_order,
)


def test_exp001_cli_writes_report_and_labeled_csv(tmp_path: Path) -> None:
    input_path = tmp_path / "bars.csv"
    output_path = tmp_path / "report.md"
    labeled_path = tmp_path / "labeled.csv"
    bars = make_synthetic_intraday_bars(("up", "range"), bars_per_session=10)
    bars["source"] = "synthetic"
    bars["adjustment_flag"] = ""
    bars.to_csv(input_path, index=False)

    exit_code = run_exp001_main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--labeled-output",
            str(labeled_path),
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    assert labeled_path.exists()


def test_paper_ledger_records_order(tmp_path: Path) -> None:
    ledger_path = tmp_path / "paper_orders.csv"
    order = build_nifty_option_paper_order("NIFTY26JUL24000CE", "BUY", NIFTY_PAPER_QUANTITY)

    record = PaperLedger(ledger_path).record_order(order)
    orders = PaperLedger(ledger_path).read_orders()

    assert record["mode"] == "PAPER"
    assert len(orders) == 1


def test_paper_order_cli_writes_ledger(tmp_path: Path) -> None:
    ledger_path = tmp_path / "paper_orders.csv"

    exit_code = run_paper_order_main(
        [
            "--symbol",
            "NIFTY26JUL24000CE",
            "--side",
            "BUY",
            "--qty",
            str(NIFTY_PAPER_QUANTITY),
            "--ledger",
            str(ledger_path),
        ]
    )

    assert exit_code == 0
    assert ledger_path.exists()


def test_nifty_paper_order_rejects_wrong_quantity() -> None:
    try:
        build_nifty_option_paper_order("NIFTY26JUL24000CE", "BUY", 75)
    except ValueError as error:
        assert "quantity 65" in str(error)
        return
    raise AssertionError("Expected NIFTY paper order quantity guard to reject 75.")


def test_build_kite_login_url() -> None:
    assert build_kite_login_url("abc123") == "https://kite.zerodha.com/connect/login?api_key=abc123&v=3"
