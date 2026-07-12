# EXP-001 Market State Report

## State Counts

| state_id | state_name | bar_count | bar_percent |
| --- | --- | --- | --- |
| S01 | Opening Discovery | 3 | 0.040000 |
| S02 | Directional Expansion Up | 5 | 0.066667 |
| S04 | Balanced Range | 21 | 0.280000 |
| S05 | Volatility Compression | 12 | 0.160000 |
| S06 | Failed Breakout | 5 | 0.066667 |
| S07 | Late Session Repricing | 15 | 0.200000 |
| S08 | Disorderly / Noisy | 14 | 0.186667 |

## Transition Matrix

| state_id | S01 | S02 | S04 | S05 | S06 | S07 | S08 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | 0.666667 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.333333 |
| S02 | 0.000000 | 0.000000 | 0.400000 | 0.200000 | 0.200000 | 0.000000 | 0.200000 |
| S04 | 0.000000 | 0.095238 | 0.523810 | 0.047619 | 0.142857 | 0.047619 | 0.142857 |
| S05 | 0.000000 | 0.166667 | 0.083333 | 0.750000 | 0.000000 | 0.000000 | 0.000000 |
| S06 | 0.000000 | 0.000000 | 0.600000 | 0.000000 | 0.000000 | 0.000000 | 0.400000 |
| S07 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 0.000000 |
| S08 | 0.000000 | 0.071429 | 0.285714 | 0.071429 | 0.071429 | 0.000000 | 0.500000 |

## Duration Summary

| state_id | state_name | segment_count | median_bars | mean_bars | max_bars |
| --- | --- | --- | --- | --- | --- |
| S01 | Opening Discovery | 1 | 3.000000 | 3.000000 | 3 |
| S02 | Directional Expansion Up | 5 | 1.000000 | 1.000000 | 1 |
| S04 | Balanced Range | 10 | 1.000000 | 2.100000 | 7 |
| S05 | Volatility Compression | 3 | 3.000000 | 4.000000 | 8 |
| S06 | Failed Breakout | 5 | 1.000000 | 1.000000 | 1 |
| S07 | Late Session Repricing | 1 | 15.000000 | 15.000000 | 15 |
| S08 | Disorderly / Noisy | 7 | 2.000000 | 2.000000 | 3 |

## Forward Return Summary

| state_id | state_name | observation_count | mean_return | median_return | horizon_bars |
| --- | --- | --- | --- | --- | --- |
| S01 | Opening Discovery | 3 | 0.000617 | 0.000568 | 1 |
| S02 | Directional Expansion Up | 5 | -0.000506 | -0.000619 | 1 |
| S04 | Balanced Range | 21 | 0.000171 | 0.000027 | 1 |
| S05 | Volatility Compression | 12 | 0.000036 | -0.000099 | 1 |
| S06 | Failed Breakout | 5 | 0.000248 | 0.000316 | 1 |
| S07 | Late Session Repricing | 14 | -0.000176 | 0.000291 | 1 |
| S08 | Disorderly / Noisy | 14 | -0.000184 | -0.000165 | 1 |
| S01 | Opening Discovery | 3 | 0.001308 | 0.001116 | 3 |
| S02 | Directional Expansion Up | 5 | -0.000297 | -0.000426 | 3 |
| S04 | Balanced Range | 21 | 0.000325 | 0.000262 | 3 |
| S05 | Volatility Compression | 12 | -0.000235 | -0.000063 | 3 |
| S06 | Failed Breakout | 5 | 0.000592 | 0.000474 | 3 |
| S07 | Late Session Repricing | 12 | -0.000837 | -0.000428 | 3 |
| S08 | Disorderly / Noisy | 14 | -0.000345 | -0.000245 | 3 |
| S01 | Opening Discovery | 3 | 0.001339 | 0.001041 | 6 |
| S02 | Directional Expansion Up | 5 | -0.000663 | -0.001001 | 6 |
| S04 | Balanced Range | 21 | 0.000485 | 0.000603 | 6 |
| S05 | Volatility Compression | 12 | -0.000463 | -0.000579 | 6 |
| S06 | Failed Breakout | 5 | 0.000139 | 0.000385 | 6 |
| S07 | Late Session Repricing | 9 | -0.002373 | -0.002509 | 6 |
| S08 | Disorderly / Noisy | 14 | -0.000100 | 0.000274 | 6 |
