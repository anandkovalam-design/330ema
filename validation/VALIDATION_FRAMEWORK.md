# Validation Framework

The validation framework checks whether research claims survive historical replay and sensitivity analysis.

## Required Gates

- Data integrity gate.
- Lookahead leakage gate.
- Label stability gate.
- State separability gate.
- Benchmark utility gate.
- Reproducibility gate.

## Paper 001 Diagnostics

The first validation layer for Paper 001 is implemented by:

- `aadithya_quantlab.experiments.exp001_baseline_labeler.run_exp001`
- `aadithya_quantlab.validation.market_state_reports.build_market_state_report`

Required report tables:

- State counts.
- State transition matrix.
- State duration summary.
- Forward return summary by state and horizon.

Markdown reports can be rendered with:

- `aadithya_quantlab.validation.market_state_reports.format_market_state_report_markdown`
- `aadithya_quantlab.validation.market_state_reports.write_market_state_report_markdown`
