# EXP-001 Synthetic Baseline Market State Report

## State Counts

| state_id | state_name | bar_count | bar_percent |
| --- | --- | --- | --- |
| S01 | Opening Discovery | 12 | 0.100000 |
| S02 | Directional Expansion Up | 29 | 0.241667 |
| S03 | Directional Expansion Down | 27 | 0.225000 |
| S04 | Balanced Range | 51 | 0.425000 |
| S05 | Volatility Compression | 1 | 0.008333 |

## Transition Matrix

| state_id | S01 | S02 | S03 | S04 | S05 |
| --- | --- | --- | --- | --- | --- |
| S01 | 0.666667 | 0.166667 | 0.083333 | 0.083333 | 0.000000 |
| S02 | 0.000000 | 0.964286 | 0.000000 | 0.035714 | 0.000000 |
| S03 | 0.000000 | 0.000000 | 1.000000 | 0.000000 | 0.000000 |
| S04 | 0.000000 | 0.000000 | 0.000000 | 0.979592 | 0.020408 |
| S05 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 0.000000 |

## Duration Summary

| state_id | state_name | segment_count | median_bars | mean_bars | max_bars |
| --- | --- | --- | --- | --- | --- |
| S01 | Opening Discovery | 4 | 3.000000 | 3.000000 | 3 |
| S02 | Directional Expansion Up | 2 | 14.500000 | 14.500000 | 27 |
| S03 | Directional Expansion Down | 1 | 27.000000 | 27.000000 | 27 |
| S04 | Balanced Range | 3 | 19.000000 | 17.000000 | 27 |
| S05 | Volatility Compression | 1 | 1.000000 | 1.000000 | 1 |

## Forward Return Summary

| state_id | state_name | observation_count | mean_return | median_return | horizon_bars |
| --- | --- | --- | --- | --- | --- |
| S01 | Opening Discovery | 12 | 0.000060 | 0.000011 | 1 |
| S02 | Directional Expansion Up | 28 | 0.000490 | 0.000541 | 1 |
| S03 | Directional Expansion Down | 26 | -0.000544 | -0.000544 | 1 |
| S04 | Balanced Range | 49 | 0.000002 | 0.000090 | 1 |
| S05 | Volatility Compression | 1 | -0.000090 | -0.000090 | 1 |
| S01 | Opening Discovery | 12 | 0.000242 | 0.000258 | 3 |
| S02 | Directional Expansion Up | 26 | 0.001445 | 0.001624 | 3 |
| S03 | Directional Expansion Down | 24 | -0.001632 | -0.001632 | 3 |
| S04 | Balanced Range | 45 | 0.000002 | 0.000090 | 3 |
| S05 | Volatility Compression | 1 | -0.000090 | -0.000090 | 3 |
| S01 | Opening Discovery | 12 | 0.000148 | 0.000269 | 6 |
| S02 | Directional Expansion Up | 23 | 0.002908 | 0.003251 | 6 |
| S03 | Directional Expansion Down | 21 | -0.003262 | -0.003262 | 6 |
| S04 | Balanced Range | 39 | 0.000000 | 0.000000 | 6 |
| S05 | Volatility Compression | 1 | 0.000000 | 0.000000 | 6 |
