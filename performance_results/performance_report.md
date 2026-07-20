# FraudGuard PostgreSQL Performance Results

PostgreSQL server: `18.4`

The benchmark used `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`. Each value is the median execution time across repeated runs. Synthetic scale rows and index changes were performed inside one transaction and rolled back after the test.

| Rows | Benchmark | Baseline ms | Optimized ms | Change |
|---:|---|---:|---:|---:|
| 2,000 | card_history | 0.1420 | 0.2020 | -42.25% |
| 2,000 | insert_fraud_with_trigger | 0.4170 | 0.3750 | +10.07% |
| 2,000 | insert_nonfraud | 0.2960 | 0.4160 | -40.54% |
| 2,000 | merchant_history | 0.0740 | 0.0810 | -9.46% |
| 2,000 | monthly_state_report | 1.3110 | 1.0410 | +20.59% |
| 2,000 | open_alert_queue | 0.5080 | 0.5830 | -14.76% |
| 2,000 | recent_fraud_investigation | 0.3130 | 0.1510 | +51.76% |
| 20,000 | card_history | 0.7360 | 0.5920 | +19.57% |
| 20,000 | insert_fraud_with_trigger | 0.2820 | 0.3630 | -28.72% |
| 20,000 | insert_nonfraud | 0.1850 | 0.2830 | -52.97% |
| 20,000 | merchant_history | 0.3290 | 0.1640 | +50.15% |
| 20,000 | monthly_state_report | 1.7700 | 1.2900 | +27.12% |
| 20,000 | open_alert_queue | 3.5310 | 0.5660 | +83.97% |
| 20,000 | recent_fraud_investigation | 0.5150 | 0.1930 | +62.52% |
| 100,000 | card_history | 4.8450 | 0.3540 | +92.69% |
| 100,000 | insert_fraud_with_trigger | 0.4220 | 0.4560 | -8.06% |
| 100,000 | insert_nonfraud | 0.2670 | 0.3470 | -29.96% |
| 100,000 | merchant_history | 2.9810 | 0.1700 | +94.30% |
| 100,000 | monthly_state_report | 1.3230 | 1.4800 | -11.87% |
| 100,000 | open_alert_queue | 17.4350 | 0.7110 | +95.92% |
| 100,000 | recent_fraud_investigation | 0.5040 | 0.2010 | +60.12% |
| 200,000 | card_history | 3.9940 | 0.3270 | +91.81% |
| 200,000 | insert_fraud_with_trigger | 0.3090 | 0.4490 | -45.31% |
| 200,000 | insert_nonfraud | 0.2110 | 0.4360 | -106.64% |
| 200,000 | merchant_history | 3.6790 | 0.1380 | +96.25% |
| 200,000 | monthly_state_report | 1.0240 | 0.8760 | +14.45% |
| 200,000 | open_alert_queue | 66.8400 | 0.7200 | +98.92% |
| 200,000 | recent_fraud_investigation | 0.2730 | 0.1540 | +43.59% |

## Interpretation guide

- Positive change means the optimized index set was faster.
- Small or negative changes at 2,000 rows are normal because sequential scans can be cheaper on tiny tables.
- The most important evidence is whether card history, merchant history, recent fraud lookup, and the open-alert queue improve as row counts grow.
- Insert tests show the write cost of maintaining indexes and the additional trigger cost for fraudulent transactions.

Full measurements are in `performance_results.csv`; representative JSON plans are in `performance_plans.json`.
