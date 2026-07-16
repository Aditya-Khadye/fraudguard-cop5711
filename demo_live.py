#!/usr/bin/env python3
# live demo: inserts a new transaction every couple seconds so the dashboard
# moves on its own. fraud ones get picked up by the trigger -> alert appears.
# run the flask app in another terminal, open http://127.0.0.1:5001/?live=1
# ctrl+c to stop. reload sql/02_seed_data.sql to reset afterwards.

import argparse, os, random, time
import psycopg2

DSN = os.environ.get("FRAUDGUARD_DSN", "host=127.0.0.1 dbname=fraudguard user=postgres password=postgres")

ap = argparse.ArgumentParser()
ap.add_argument("--interval", type=float, default=2.0, help="seconds between transactions")
ap.add_argument("--fraud-rate", type=float, default=0.15, help="probability a txn is fraud (kept high for demo)")
args = ap.parse_args()

conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT card_id FROM card")
cards = [r[0] for r in cur.fetchall()]
cur.execute("SELECT merchant_id, merchant_name, merchant_state FROM merchant")
merchants = cur.fetchall()
cur.execute("SELECT COALESCE(MAX(transaction_id), 0) FROM transaction")
next_id = cur.fetchone()[0] + 1

print(f"feeding transactions every {args.interval}s (fraud rate {args.fraud_rate:.0%}), ctrl+c to stop\n")
try:
    while True:
        mid, mname, mstate = random.choice(merchants)
        amount = round(random.uniform(4, 900), 2)
        fraud = random.random() < args.fraud_rate
        chip = random.choice(["Chip Transaction", "Chip Transaction", "Swipe Transaction", "Online Transaction"])
        cur.execute(
            """INSERT INTO transaction
               (transaction_id, card_id, merchant_id, txn_timestamp, amount, use_chip, is_fraud, error_flag)
               VALUES (%s, %s, %s, now(), %s, %s, %s, false)""",
            (next_id, random.choice(cards), mid, amount, chip, fraud))
        tag = "  << FRAUD, alert opened by trigger" if fraud else ""
        print(f"txn {next_id}: ${amount:>7.2f}  {mname} ({mstate}){tag}")
        next_id += 1
        time.sleep(args.interval)
except KeyboardInterrupt:
    print("\nstopped")
