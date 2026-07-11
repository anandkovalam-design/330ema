# Mathematical Specification 001: Market State Taxonomy

Status: Draft v0.2  
Linked paper: `research/papers/001_market_state_taxonomy_intraday_nifty/PAPER.md`

## Purpose

This document defines the first deterministic feature and labeling rules for Paper 001. The definitions are designed for historical replay and live inference, so each value at bar `t` must use only data available at or before `t`.

## Inputs

For each symbol and session, the canonical bar fields are:

- `timestamp`
- `session_date`
- `open`
- `high`
- `low`
- `close`
- `volume`

Rows must be sorted by `symbol`, `session_date`, and `timestamp` before feature computation.

## Core Variables

Let `i` be the zero-based bar index within a trading session.

### Opening Range

For an opening window of `n` bars:

- `ORH_t = max(H_0, ..., H_min(i,n-1))`
- `ORL_t = min(L_0, ..., L_min(i,n-1))`
- `ORM_t = (ORH_t + ORL_t) / 2`
- `ORW_t = ORH_t - ORL_t`

Opening range location:

`ORLoc_t = (C_t - ORL_t) / max(ORW_t, epsilon)`

### Session VWAP

For bars from session open through `t`:

`VWAP_t = sum(((H_j + L_j + C_j) / 3) * V_j) / max(sum(V_j), epsilon)`

If volume is unavailable, use an unweighted cumulative typical price mean and mark the feature source as proxy.

VWAP displacement:

`VWAPDisp_t = (C_t - VWAP_t) / max(ATR_k,t, epsilon)`

### True Range and ATR

`TR_t = max(H_t - L_t, abs(H_t - C_{t-1}), abs(L_t - C_{t-1}))`

For the first bar of a session:

`TR_0 = H_0 - L_0`

Rolling ATR:

`ATR_k,t = mean(TR_{t-k+1}, ..., TR_t)`

with `min_periods = 1` for baseline labeling.

### Realized Volatility

`r_t = C_t / C_{t-1} - 1`

`RV_k,t = std(r_{t-k+1}, ..., r_t)`

Baseline implementation uses population standard deviation and fills unavailable first-bar values with `0`.

### Directional Efficiency

For a window of `k` bars:

`DE_k,t = abs(C_t - C_{t-k+1}) / max(sum(abs(C_j - C_{j-1})), epsilon)`

`DE_k,t` approaches `1` when movement is persistent and approaches `0` when travel is noisy.

Signed directional efficiency:

`SDE_k,t = sign(C_t - C_{t-k+1}) * DE_k,t`

### Range Extension Ratio

Session high and low through `t`:

- `SH_t = max(H_0, ..., H_t)`
- `SL_t = min(L_0, ..., L_t)`

Range extension ratio:

`RER_t = (SH_t - SL_t) / max(ORW_t, epsilon)`

### Compression Score

Let `ATRBase_t` be a rolling median ATR over a longer baseline window `m`.

`Compression_t = ATR_k,t / max(ATRBase_t, epsilon)`

Values below `1` indicate volatility below the recent baseline.

## State Assignment Priority

The baseline labeler applies states in this order:

1. `S01 Opening Discovery`: `i < opening_window_bars`
2. `S07 Late Session Repricing`: `i >= late_session_start_bar` and expansion or directional movement is elevated
3. `S06 Failed Breakout`: prior close broke outside opening range and current close re-entered it
4. `S02 Directional Expansion Up`: close is above opening range high, signed efficiency is positive, and range extension is high
5. `S03 Directional Expansion Down`: close is below opening range low, signed efficiency is negative, and range extension is high
6. `S05 Volatility Compression`: compression score is low and directional efficiency is low
7. `S04 Balanced Range`: close is inside opening range or near VWAP with low directional efficiency
8. `S08 Disorderly / Noisy`: fallback state

## Baseline Thresholds

| Parameter | Default | Purpose |
| --- | --- | --- |
| `opening_window_bars` | `3` | First bars used for opening discovery and opening range. |
| `feature_window_bars` | `5` | ATR, realized volatility, and efficiency window. |
| `baseline_window_bars` | `20` | Compression baseline window. |
| `late_session_start_bar` | `60` | First bar eligible for late-session state. |
| `range_extension_threshold` | `1.20` | Minimum extension for directional expansion. |
| `efficiency_threshold` | `0.55` | Minimum directional efficiency for expansion. |
| `compression_threshold` | `0.75` | Maximum compression ratio for compression state. |
| `near_vwap_atr` | `0.50` | ATR-normalized distance considered near VWAP. |

These thresholds are research defaults, not fitted constants.

## Leakage Rule

No feature may use a full-session statistic unless it is explicitly shifted or computed cumulatively through `t`. Forward returns, maximum favorable excursion, and maximum adverse excursion are validation outputs only; they must never participate in label creation.
