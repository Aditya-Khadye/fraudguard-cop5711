# PostgreSQL Performance Testing and Validation

## Responsibility completed

My project responsibility focused on PostgreSQL implementation quality, performance testing, and validation. I reviewed the schema and application as one database workflow rather than testing only individual SQL statements. The tested workflow is:

`transaction insert -> fraud trigger -> fraud_alert -> dashboard queue -> review_alert() procedure`

## Database improvements

The following changes were added to support correctness and performance:

1. Added `reviewed_at` to `fraud_alert` so alert turnaround time can be measured.
2. Strengthened the alert workflow constraint so open alerts cannot have review information and completed alerts must have both a reviewer and review timestamp.
3. Updated `review_alert()` to reject blank reviewers, reject invalid decisions, prevent a second review, and record `reviewed_at`.
4. Changed the trigger to use the actual alert creation time rather than copying the historical transaction timestamp.
5. Added workload-oriented indexes for card history, merchant history, recent fraud lookup, merchant-category joins, and the open-alert queue.
6. Corrected `etl_load.py` so it loads the same constrained schema used by the graded SQL deliverable instead of silently creating a different schema.
7. Updated the dashboard aggregate to work when the transaction table is empty and limited the displayed alert history to the latest 100 rows.

## Validation approach

`sql/04_validation.sql` provides repeatable database tests and rolls back all test data. It verifies:

- The number of alerts equals the number of fraud-labeled transactions.
- No legitimate transaction has an alert.
- Every fraudulent transaction has exactly one alert.
- The monthly summary view reconciles to the base transaction table.
- Transaction foreign keys do not produce orphan cards or merchants.
- A legitimate insert does not create an alert.
- A fraudulent insert automatically creates an alert.
- `review_alert()` records the decision, reviewer, and review timestamp.
- A reviewed alert cannot be reviewed a second time.
- Invalid decisions and blank reviewers are rejected.
- Zero-dollar transactions, invalid card IDs, and invalid entry methods are rejected.

Run the validation suite with:

```bash
psql -v ON_ERROR_STOP=1 -d fraudguard -f sql/04_validation.sql
```

A successful run ends with:

```text
FraudGuard validation completed successfully; all test changes were rolled back.
```

## Performance methodology

`performance_test.py` measures the following workloads:

- Recent transaction history for a busy card
- Recent transaction history for a busy merchant
- Recent fraud investigation query
- Open fraud-alert queue
- Monthly state report
- Legitimate transaction insert
- Fraudulent transaction insert including trigger overhead

The script uses `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` and records planning time, execution time, top plan node, rows, buffer hits, and buffer reads. It compares:

- **Baseline:** the original single-column indexes
- **Optimized:** composite and partial indexes matched to the application workload

The default test sizes are 2,000, 20,000, 100,000, and 200,000 transactions. Larger sizes are generated temporarily inside a transaction. All inserted benchmark rows and index changes are rolled back, leaving the original database unchanged.

Run:

```bash
python performance_test.py \
  --dsn "host=127.0.0.1 dbname=fraudguard user=postgres password=postgres" \
  --target-rows 2000,20000,100000,200000
```

Generated evidence:

- `performance_results/performance_results.csv`
- `performance_results/performance_report.md`
- `performance_results/performance_plans.json`

The generated Markdown table should be copied into the final report. Actual execution times must come from the PostgreSQL machine used for the demonstration; they should not be estimated or invented from the source code.

## Indexes evaluated

| Index | Workload supported |
|---|---|
| `transaction(card_id, txn_timestamp DESC)` | Card history ordered newest-first |
| `transaction(merchant_id, txn_timestamp DESC)` | Merchant history and merchant investigation |
| `transaction(txn_timestamp DESC) WHERE is_fraud` | Recent fraud-only investigation |
| `fraud_alert(created_at DESC) WHERE alert_status='OPEN'` | Open alert queue |
| `merchant(mcc)` | Merchant-to-category analytical joins |

A sequential scan may still be faster at 2,000 rows. The main question is whether the optimized plans become more effective as transaction volume grows.

## Trigger overhead

The benchmark measures one legitimate insert and one fraudulent insert. The fraudulent insert also executes `fn_open_fraud_alert()` and writes a row to `fraud_alert`, so it should take somewhat longer. This difference is expected because the database is enforcing the alert business process automatically. The final report should use the measured median times from `performance_report.md`.

## Partitioning demonstration

`sql/05_partitioning_demo.sql` builds a monthly range-partitioned clone of the transaction table, copies the currently loaded rows, and compares a one-month report against the unpartitioned table. The entire operation is rolled back.

Run:

```bash
psql -v ON_ERROR_STOP=1 -d fraudguard \
  -f sql/05_partitioning_demo.sql \
  > performance_results/partitioning_output.txt
```

Partitioning is not expected to improve a 2,000-row table consistently. The demonstration should be run after loading or temporarily generating at least 100,000 rows so partition pruning is visible in the plan.

## Final report paragraph

The PostgreSQL portion of FraudGuard was validated as an end-to-end database workflow. Automated tests confirmed referential integrity, check constraints, one-to-one fraud alert creation, alert review rules, and reconciliation of the monthly reporting view. Performance was evaluated with PostgreSQL `EXPLAIN ANALYZE` and buffer statistics at increasing transaction volumes. Composite indexes were tested for card and merchant histories, while partial indexes were tested for recent fraud investigations and the open-alert queue. Insert benchmarks also separated ordinary insert cost from the additional cost of the fraud-alert trigger. Finally, a rollback-only monthly range-partitioning demonstration was used to show how time-based reports can prune unrelated partitions as the transaction table grows. The measured timing table and representative query plans are included in the project results.
