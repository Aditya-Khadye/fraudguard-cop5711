# FraudGuard

COP5711 final project - fraud detection database on credit card transactions.

Team: Aditya, Boopathi, Hardik

## Quick start

```
createdb fraudguard
psql -d fraudguard -f sql/01_schema.sql -f sql/02_seed_data.sql
```

That's the graded deliverable path: constraints go in first (PK/FK/NOT NULL/UNIQUE/CHECK), then seed data inserted with plain INSERT queries (~100 rows in each dimension table, 2k transactions so the analytical queries have something to chew on). The schema also has a trigger that auto-opens a fraud_alert whenever a transaction comes in flagged as fraud, a review_alert() stored procedure for the analyst workflow, and a v_monthly_fraud_summary view for report queries.

## App (frontend + backend)

```
pip install -r requirements.txt
python app/app.py
```

Runs at http://127.0.0.1:5001. Flask backend + plain html frontend: monthly fraud summary table and the alert review queue. The confirm/dismiss buttons call the review_alert() stored procedure, and /api/monthly_summary is the json endpoint. If your postgres login differs, set FRAUDGUARD_DSN (default assumes user postgres / password postgres locally).

## Live demo

Terminal 1: `python3 app/app.py` and open http://127.0.0.1:5001/?live=1 (auto-refreshes every 4s).
Terminal 2: `python3 demo_live.py` - inserts a new transaction every 2s, some fraudulent. Watch alerts appear in the queue on their own (that's the trigger), then confirm/dismiss them live (that's the stored procedure). Reload sql/02_seed_data.sql afterwards to reset.

## Analytical queries

sql/03_queries.sql - needs 6-8 total, each one complex (joins, nested queries, window functions, views), monthly report / business process style. No plain summary stats, prof was explicit about that. Q1 is done as a template (view + window function), Q2-Q8 are stubbed with what each should do.

## Full dataset (optional, for the distributed/scale part)

Real data if we want it: https://www.kaggle.com/datasets/ealtman2019/credit-card-transactions (24M transactions, free account needed). Unzip into `data/` and run:

```
pip install -r requirements.txt
python etl_load.py --data-dir ./data --dsn "host=127.0.0.1 dbname=fraudguard user=postgres" --reset --sample-size 200000
```

## Files

- sql/01_schema.sql - schema, constraints first, trigger + stored proc + view
- sql/02_seed_data.sql - seed data, INSERT statements (dims ~100 rows, 2k transactions)
- sql/03_queries.sql - 1 example query done, 7 stubs with specs
- app/ - flask app (backend + frontend)
- demo_live.py - feeds live transactions for demos
- etl_load.py - loads the real kaggle dataset at scale
- FraudGuard_Project_Proposal.docx - the proposal
- er_diagram.png / er.dot - schema diagram

## TODO (updated after the 7/13 progress check)

- finish Q2-Q8 in sql/03_queries.sql (complex, report-style)
- distributed writeup: which tables get sharded (transaction, by merchant_state or hash), which stay small and replicated everywhere (merchant_category, merchant), and why
- normalization quiz is up on webcourses
