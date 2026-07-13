# FraudGuard

Credit card transaction and fraud monitoring database.
COP5711, Parallel and Distributed Database Systems, UCF, Summer 2026.

Team: Aditya Khadye, Boopathi, Hardik

## What's in this repo

| File | What it is |
|---|---|
| `FraudGuard_Project_Proposal.docx` | Project proposal: domain, ER diagram, schema draft, scaling notes |
| `er_diagram.png` / `er.dot` | ER diagram and its Graphviz source |
| `etl_load.py` | Tested ETL loader: reads the 3 dataset CSVs, normalizes merchants, bulk loads PostgreSQL |
| `requirements.txt` | Python dependencies |

## Dataset (not committed, ~2.5 GB)

IBM synthetic Credit Card Transactions:
https://www.kaggle.com/datasets/ealtman2019/credit-card-transactions

Download (free Kaggle account required), unzip into a `./data/` folder next to `etl_load.py`. Three files: `sd254_users.csv`, `sd254_cards.csv`, `credit_card_transactions-ibm_v2.csv` (~24M rows; the loader subsamples).

## How to run

```bash
pip install -r requirements.txt
createdb fraudguard        # needs PostgreSQL installed and running

# preview only, no DB writes:
python etl_load.py --data-dir ./data --dry-run

# real load (200k row sample, drops/recreates tables):
export PGPASSWORD=yourpassword
python etl_load.py --data-dir ./data \
  --dsn "host=127.0.0.1 dbname=fraudguard user=postgres" \
  --reset --sample-size 200000
```

Builds 6 tables: `customer`, `card`, `merchant`, `merchant_category`, `transaction`, `fraud_alert`, with FKs and indexes. Verified end to end: 0 orphaned rows on all referential integrity checks.

## Status

**Done:** proposal, schema + ER diagram, dataset selection, ETL loader (tested against PostgreSQL 16).

**Remaining (before Monday office hours):**
1. Analytical queries: fraud rate by region and merchant category, per customer / per card history
2. Distributed transaction demo (two phase commit scenario)
3. Progress summary to show the professor Monday
