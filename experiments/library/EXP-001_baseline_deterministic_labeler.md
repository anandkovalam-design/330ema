# EXP-001: Baseline Deterministic Labeler

Status: Draft protocol  
Source paper: Paper 001  
Code entry point: `aadithya_quantlab.research.market_state_taxonomy.label_market_states`

## Objective

Produce reproducible intraday NIFTY market-state labels from canonical OHLCV bars using only information available up to each bar.

## Hypothesis Link

- H1: Expansion states show stronger forward persistence than balanced or noisy states.
- H2: Balanced range states show weaker trend continuation.
- H4: Time-of-day improves state stability.

## Inputs

- Canonical intraday OHLCV dataset.
- Labeler configuration.
- Feature snapshot version.

## Procedure

1. Validate the input schema.
2. Sort rows by symbol, session date, and timestamp.
3. Compute Paper 001 feature columns.
4. Apply deterministic state priority rules.
5. Persist label outputs with reason codes and labeler version.
6. Generate transition, duration, and state-count diagnostics.

## Primary Outputs

- Labeled bar dataset.
- State transition matrix.
- Median state duration.
- Count and percentage of bars per state.
- Leakage audit result.

## Acceptance Gate

The experiment passes the first research gate when labels are reproducible across runs, no future data is used, and no single fallback state dominates more than 70 percent of non-opening bars on a representative sample.
