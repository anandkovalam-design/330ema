# Paper 001: Market State Taxonomy for Intraday NIFTY

Status: Draft v0.1  
Program: Aadithya QuantLab  
Owner: Research  
Created: 2026-07-11  
Document type: Version-controlled research paper  
Primary outputs: taxonomy, state labels, feature candidates, validation protocol

## Abstract

This paper defines an initial taxonomy for intraday NIFTY market states. The goal is to replace vague labels such as "trend day", "range day", or "choppy market" with measurable state definitions that can support feature engineering, experiment design, strategy gating, and post-trade diagnosis.

The taxonomy is intentionally model-agnostic. It does not prescribe an entry signal. Instead, it defines observable state variables and state classes that downstream models can consume, test, reject, or refine.

## Research Question

Can intraday NIFTY price action be partitioned into a compact set of market states that are:

1. Observable using only information available up to the decision timestamp.
2. Stable enough to support intraday decision-making.
3. Distinct enough to explain materially different risk, reward, and execution behavior.
4. Testable through historical replay without lookahead leakage.

## Scope

Included:

- NIFTY spot or tradable proxy intraday OHLCV data.
- Session-level and rolling intraday features.
- State definitions suitable for backtesting and live inference.
- Validation methods for stability, separability, and usefulness.

Excluded from v0.1:

- Option-chain state taxonomy.
- Individual stock breadth models.
- Strategy-specific entry or exit rules.
- Broker execution integration.

## Notation

Let each intraday bar be indexed by `t`, with open `O_t`, high `H_t`, low `L_t`, close `C_t`, volume `V_t`, and timestamp `T_t`.

Let the trading session be indexed by `d`. For each session:

- `OR_n(d)` is the opening range using the first `n` minutes.
- `VWAP_t` is the session-anchored volume weighted average price up to `t`.
- `ATR_k,t` is a rolling true-range estimate over `k` bars.
- `R_t = C_t - C_{t-1}` is the close-to-close return.
- `RV_t` is rolling realized volatility.
- `M_t` is a directional movement score.
- `E_t` is an expansion score.
- `B_t` is a balance or compression score.

Formal definitions will be maintained in `research/mathematical_specifications/001_market_state_taxonomy.md`.

## Taxonomy v0.1

The initial taxonomy defines eight intraday market states.

| State ID | State Name | Description | Expected Behavior |
| --- | --- | --- | --- |
| `S01` | Opening Discovery | Early session repricing while the market establishes range, direction, and liquidity. | High uncertainty, high false-break risk. |
| `S02` | Directional Expansion Up | Price expands above prior intraday reference levels with persistent positive movement. | Trend-following signals may improve; mean reversion may degrade. |
| `S03` | Directional Expansion Down | Price expands below prior intraday reference levels with persistent negative movement. | Short-side trend behavior dominates; bounce attempts need stricter filters. |
| `S04` | Balanced Range | Price oscillates around session value without sustained directional displacement. | Mean-reversion behavior may dominate. |
| `S05` | Volatility Compression | Range, realized volatility, and directional movement contract relative to recent context. | Breakout preparation state; signal confidence should be reduced until expansion confirms. |
| `S06` | Failed Breakout | Price exits a reference range and then re-enters with adverse follow-through. | Trapped-position dynamics; reversal risk increases. |
| `S07` | Late Session Repricing | Post-midday move with expansion or unwind into closing period. | Higher urgency, liquidity and slippage sensitivity. |
| `S08` | Disorderly / Noisy | Volatile movement without directional persistence or clean balance. | Reduce model conviction; prefer capital preservation. |

## Candidate State Variables

The first feature set should be limited, inspectable, and robust.

| Variable | Purpose | Candidate Feature Library ID |
| --- | --- | --- |
| Opening range location | Measures price relative to early-session auction. | `FL-001` |
| VWAP displacement | Measures distance from session value. | `FL-002` |
| Rolling realized volatility | Identifies expansion and compression. | `FL-003` |
| Directional efficiency | Distinguishes persistent movement from noise. | `FL-004` |
| Range extension ratio | Measures session range extension beyond opening range. | `FL-005` |
| Re-entry after breakout | Detects failed range exits. | `FL-006` |
| Time-of-day bucket | Separates opening, midday, and late-session behavior. | `FL-007` |

## Labeling Framework

State labels should be produced by a deterministic baseline before any supervised model is attempted.

Baseline label process:

1. Segment the session into time-aware bars.
2. Compute only features available at or before each timestamp.
3. Apply rule-based state assignment with explicit priority ordering.
4. Store label, confidence score, and reason codes.
5. Compare labels against forward returns only during validation, never during label creation.

Priority order for v0.1:

1. Opening Discovery.
2. Late Session Repricing.
3. Failed Breakout.
4. Directional Expansion Up or Down.
5. Volatility Compression.
6. Balanced Range.
7. Disorderly / Noisy.

## Hypotheses

H1: Directional expansion states show higher forward directional persistence than balanced or noisy states.

H2: Balanced range states show lower trend continuation and higher reversion-to-value behavior than expansion states.

H3: Failed breakout states show asymmetric adverse excursion against the breakout direction.

H4: State labels are more stable when time-of-day is included as an explicit variable.

H5: Strategy performance diagnostics improve when trades are grouped by market state instead of evaluated only at portfolio level.

## Validation Plan

Validation must answer three questions.

1. Stability: Do adjacent bars avoid excessive label flipping?
2. Separability: Do states show different distributions of forward return, volatility, drawdown, and excursion?
3. Utility: Do state-aware filters improve risk-adjusted outcomes for simple benchmark strategies?

Minimum validation outputs:

- Transition matrix between states.
- Median state duration.
- Forward return distribution by state and horizon.
- Maximum favorable excursion and maximum adverse excursion by state.
- Benchmark strategy performance with and without state filters.
- Sensitivity analysis across bar interval, opening-range length, and volatility window.

## Data Requirements

Canonical intraday dataset fields:

- `symbol`
- `timestamp`
- `session_date`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `source`
- `adjustment_flag`

Derived label dataset fields:

- `symbol`
- `timestamp`
- `session_date`
- `state_id`
- `state_confidence`
- `reason_codes`
- `feature_snapshot_version`
- `labeler_version`

The schema contract will be specified in `research/data_schema/intraday_ohlcv_schema.md`.

## Experiment Register

Initial experiments:

| Experiment ID | Title | Purpose |
| --- | --- | --- |
| `EXP-001` | Baseline deterministic labeler | Produce first state labels and transition diagnostics. |
| `EXP-002` | Forward distribution separability | Test whether state classes produce distinct forward behavior. |
| `EXP-003` | State-aware benchmark filters | Test whether simple strategy gates improve diagnostics. |

Detailed protocols will live in `experiments/library/`.

## Risks and Failure Modes

- Overfitting taxonomy thresholds to a small market regime.
- Hidden lookahead leakage in session statistics.
- State labels that are intuitive but not predictive or diagnostically useful.
- Excessive label churn that makes live usage impractical.
- Treating taxonomy as fixed before enough experiments have challenged it.

## Acceptance Criteria for v1.0

Paper 001 can move from draft to v1.0 when:

1. Formal mathematical definitions exist for all state variables.
2. A deterministic labeler can reproduce labels from canonical OHLCV data.
3. At least three validation reports are generated from historical replay.
4. All hypotheses are marked supported, rejected, or inconclusive.
5. Feature and experiment registry entries link back to this paper.

## Version History

| Version | Date | Notes |
| --- | --- | --- |
| v0.1 | 2026-07-11 | Initial taxonomy, hypotheses, data requirements, and validation plan. |

