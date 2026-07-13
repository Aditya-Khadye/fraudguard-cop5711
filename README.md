# FraudGuard

COP5711 final project - fraud detection database on credit card transactions.

Team: Aditya, Boopathi, Hardik

## Dataset

https://www.kaggle.com/datasets/ealtman2019/credit-card-transactions

Download from kaggle (need a free account) and unzip into a `data/` folder next to the script. 3 csv files - users, cards, transactions. The transactions file is huge (~24M rows) so the loader only takes a sample.

## Setup

```
pip install -r requirements.txt
createdb fraudguard
export PGPASSWORD=yourpassword
python etl_load.py --data-dir ./data --dsn "host=127.0.0.1 dbname=fraudguard user=postgres" --reset --sample-size 200000
```

Add `--dry-run` if you just want to see what it does without touching the db.

Creates 6 tables (customer, card, merchant, merchant_category, transaction, fraud_alert) with FKs and indexes. Tested it end to end, loads clean.

## Files

- FraudGuard_Project_Proposal.docx - the proposal
- er_diagram.png / er.dot - schema diagram (dot file is the graphviz source if you need to edit it)
- etl_load.py - loads everything into postgres

## TODO

- analytical queries (fraud rate by state/category, customer history)
- distributed transaction demo (2pc)
- progress update for monday office hours
