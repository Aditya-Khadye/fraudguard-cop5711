#!/usr/bin/env python3
"""
FraudGuard ETL loader
=====================
Loads the IBM / Kaggle synthetic credit card transaction dataset into PostgreSQL
for the COP5711 FraudGuard project.

What it does
------------
1. Reads the three source files:
     - sd254_users.csv                        -> customer
     - sd254_cards.csv                         -> card
     - credit_card_transactions-ibm_v2.csv     -> transaction (+ normalized merchant / merchant_category)
   (The transactions filename is auto-detected; the Box/TabFormer file
    card_transaction.v1.csv works too. If only the transactions file is present,
    customer and card are still built as ID-keyed skeleton tables.)
2. Takes a capped, reproducible subsample of the transactions (default 200k rows)
   so you are not loading all ~24M rows for a course project.
3. Normalizes the merchant fields out of each transaction into their own
   merchant and merchant_category tables (moves the schema toward 3NF).
4. Raises a fraud_alert row for every transaction flagged Is Fraud = Yes.
5. Bulk-loads everything with COPY, in foreign-key-safe order.

Credentials
-----------
No passwords are stored in this file. Connection settings come from a DSN you
pass with --dsn, or from the standard libpq environment variables
(PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD).

Examples
--------
    # dry run: transform only, print row counts, touch no database
    python etl_load.py --data-dir ./data --dry-run

    # real load into a local database, resetting the schema first
    export PGPASSWORD=postgres
    python etl_load.py --data-dir ./data \
        --dsn "host=127.0.0.1 dbname=fraudguard user=postgres" \
        --reset --sample-size 200000
"""

import argparse
import io
import os
import sys
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Config defaults
# ----------------------------------------------------------------------------
DEFAULT_SAMPLE = 200_000
EST_TOTAL_TXNS = 24_386_900          # approx rows in the full transactions file
TXN_FILE_CANDIDATES = [
    "credit_card_transactions-ibm_v2.csv",   # Kaggle
    "card_transaction.v1.csv",               # IBM Box / TabFormer
    "User0_credit_card_transactions.csv",    # some mirrors
]
USERS_FILE = "sd254_users.csv"
CARDS_FILE = "sd254_cards.csv"

# Partial MCC lookup for readable category names. Unknown codes fall back to
# "MCC <code>". Extend this from a public ISO 18245 list if you want fuller coverage.
MCC_DESCRIPTIONS = {
    4111: "Local/Suburban Commuter Transport", 4121: "Taxicabs and Limousines",
    4131: "Bus Lines", 4784: "Tolls and Bridge Fees", 4814: "Telecom Services",
    4829: "Money Transfer", 4899: "Cable/Satellite/Pay TV", 4900: "Utilities",
    5251: "Hardware Stores", 5300: "Wholesale Clubs", 5310: "Discount Stores",
    5311: "Department Stores", 5411: "Grocery Stores/Supermarkets",
    5412: "Convenience Stores", 5499: "Misc Food Stores", 5541: "Service Stations (Fuel)",
    5542: "Automated Fuel Dispensers", 5651: "Family Clothing Stores",
    5661: "Shoe Stores", 5691: "Men's/Women's Clothing", 5712: "Furniture/Home Furnishings",
    5732: "Electronics Stores", 5812: "Eating Places/Restaurants",
    5813: "Bars/Taverns/Nightclubs", 5814: "Fast Food Restaurants",
    5912: "Drug Stores/Pharmacies", 5921: "Package Stores (Beer/Wine/Liquor)",
    5941: "Sporting Goods Stores", 5942: "Book Stores", 5947: "Gift/Novelty Stores",
    5964: "Direct Marketing/Catalog", 5967: "Direct Marketing/Inbound Tele",
    5977: "Cosmetic Stores", 5992: "Florists", 5999: "Misc Retail",
    6011: "ATM/Cash Withdrawal", 6300: "Insurance", 7011: "Hotels/Motels/Resorts",
    7230: "Beauty/Barber Shops", 7298: "Health and Beauty Spas",
    7372: "Computer Programming Services", 7538: "Auto Service Shops",
    7542: "Car Washes", 7801: "Online Gambling", 7802: "Horse/Dog Racing",
    7832: "Motion Picture Theaters", 7995: "Betting/Casino Gaming",
    8011: "Doctors/Physicians", 8021: "Dentists", 8043: "Optometrists",
    8062: "Hospitals", 8099: "Health Practitioners", 8111: "Legal Services",
    8931: "Accounting/Bookkeeping", 9402: "Postal Services",
}


# ----------------------------------------------------------------------------
# Parsing helpers (input is read as strings; we coerce types ourselves)
# ----------------------------------------------------------------------------
def money(series: pd.Series) -> pd.Series:
    """'$1,234.56' or '-$8.42' -> float; '' -> NaN."""
    cleaned = (series.astype(str)
               .str.replace("$", "", regex=False)
               .str.replace(",", "", regex=False)
               .str.strip()
               .replace("", np.nan))
    return pd.to_numeric(cleaned, errors="coerce")


def to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace("", np.nan), errors="coerce").astype("Int64")


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace("", np.nan), errors="coerce")


def yesno(series: pd.Series) -> pd.Series:
    """'Yes'/'No' (any case) -> 't'/'f' for a Postgres boolean; '' -> None."""
    m = {"YES": "t", "NO": "f", "TRUE": "t", "FALSE": "f", "Y": "t", "N": "f"}
    return series.astype(str).str.strip().str.upper().map(m)


def nonempty_bool(series: pd.Series) -> pd.Series:
    """True when the field has any content (used for the Errors? -> error_flag)."""
    return np.where(series.astype(str).str.strip().replace("nan", "") != "", "t", "f")


# ----------------------------------------------------------------------------
# Loaders / transforms
# ----------------------------------------------------------------------------
def find_transactions_file(data_dir: str, override: str | None) -> str:
    if override:
        path = override if os.path.isabs(override) else os.path.join(data_dir, override)
        if not os.path.exists(path):
            sys.exit(f"Transactions file not found: {path}")
        return path
    for name in TXN_FILE_CANDIDATES:
        path = os.path.join(data_dir, name)
        if os.path.exists(path):
            return path
    sys.exit(f"No transactions file found in {data_dir}. Looked for: {TXN_FILE_CANDIDATES}")


def read_csv_str(path: str, **kw) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, **kw)
    df.columns = df.columns.str.strip()
    return df


def load_customers(path: str) -> pd.DataFrame:
    """users file has no id column; customer_id is the 0-based row index."""
    src = read_csv_str(path)
    out = pd.DataFrame()
    out["customer_id"] = np.arange(len(src), dtype=np.int64)
    out["current_age"] = to_int(src["Current Age"])
    out["retirement_age"] = to_int(src["Retirement Age"])
    out["birth_year"] = to_int(src["Birth Year"])
    out["birth_month"] = to_int(src["Birth Month"])
    out["gender"] = src["Gender"]
    out["address"] = src["Address"]
    out["city"] = src["City"]
    out["state"] = src["State"]
    out["zipcode"] = src["Zipcode"]
    out["latitude"] = to_num(src["Latitude"])
    out["longitude"] = to_num(src["Longitude"])
    out["per_capita_income"] = money(src["Per Capita Income - Zipcode"])
    out["yearly_income"] = money(src["Yearly Income - Person"])
    out["total_debt"] = money(src["Total Debt"])
    out["fico_score"] = to_int(src["FICO Score"])
    out["num_credit_cards"] = to_int(src["Num Credit Cards"])
    return out


def load_cards(path: str) -> tuple[pd.DataFrame, dict]:
    """Returns the card dataframe plus a (customer_id, card_index) -> card_id map."""
    src = read_csv_str(path)
    out = pd.DataFrame()
    out["card_id"] = np.arange(1, len(src) + 1, dtype=np.int64)
    out["customer_id"] = to_int(src["User"])
    out["card_index"] = to_int(src["CARD INDEX"])
    out["card_brand"] = src["Card Brand"]
    out["card_type"] = src["Card Type"]
    out["card_number_masked"] = src["Card Number"].astype(str).str[-4:].radd("****")
    out["expires"] = src["Expires"]
    out["has_chip"] = yesno(src["Has Chip"])
    out["cards_issued"] = to_int(src["Cards Issued"])
    out["credit_limit"] = money(src["Credit Limit"])
    out["acct_open_date"] = src["Acct Open Date"]
    key = list(zip(out["customer_id"].astype("Int64"), out["card_index"].astype("Int64")))
    card_map = {k: cid for k, cid in zip(key, out["card_id"])}
    return out, card_map


def sample_transactions(path: str, sample_size: int, method: str, seed: int) -> pd.DataFrame:
    if method == "head":
        return read_csv_str(path, nrows=sample_size)
    # Bernoulli sampling over chunks: memory-safe, reproducible, spreads across all users/regions
    rng = np.random.default_rng(seed)
    p = min(1.0, (sample_size / EST_TOTAL_TXNS) * 1.2)  # slight oversample, trimmed later
    kept = []
    for chunk in pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=500_000):
        chunk.columns = chunk.columns.str.strip()
        mask = rng.random(len(chunk)) < p
        if mask.any():
            kept.append(chunk.loc[mask])
    if not kept:
        return read_csv_str(path, nrows=sample_size)
    df = pd.concat(kept, ignore_index=True)
    if len(df) > sample_size:
        df = df.sample(n=sample_size, random_state=seed).reset_index(drop=True)
    return df


def build_from_transactions(txn: pd.DataFrame, card_map: dict):
    """Split a sampled transactions frame into merchant_category, merchant,
    transaction and fraud_alert frames."""
    mkeys = ["Merchant Name", "Merchant City", "Merchant State", "Zip", "MCC"]
    for c in mkeys:
        txn[c] = txn[c].astype(str).str.strip().replace("nan", "")

    # merchant_category
    mcc_vals = pd.to_numeric(txn["MCC"].replace("", np.nan), errors="coerce").dropna().astype(int)
    mcat = pd.DataFrame({"mcc": sorted(mcc_vals.unique())})
    mcat["category_description"] = mcat["mcc"].map(
        lambda c: MCC_DESCRIPTIONS.get(int(c), f"MCC {int(c)}"))

    # merchant: one surrogate id per distinct (name, city, state, zip, mcc)
    merch = txn[mkeys].drop_duplicates().reset_index(drop=True)
    merch.insert(0, "merchant_id", np.arange(1, len(merch) + 1, dtype=np.int64))
    txn = txn.merge(merch, on=mkeys, how="left")
    merch_out = pd.DataFrame({
        "merchant_id": merch["merchant_id"],
        "merchant_name": merch["Merchant Name"].replace("", np.nan),
        "merchant_city": merch["Merchant City"].replace("", np.nan),
        "merchant_state": merch["Merchant State"].replace("", np.nan),
        "zip": merch["Zip"].replace("", np.nan),
        "mcc": pd.to_numeric(merch["MCC"].replace("", np.nan), errors="coerce").astype("Int64"),
    })

    # transaction
    ts_str = (txn["Year"] + "-" + txn["Month"].str.zfill(2) + "-"
              + txn["Day"].str.zfill(2) + " " + txn["Time"])
    key = list(zip(to_int(txn["User"]).astype("Int64"), to_int(txn["Card"]).astype("Int64")))
    tx_out = pd.DataFrame()
    tx_out["transaction_id"] = np.arange(1, len(txn) + 1, dtype=np.int64)
    tx_out["card_id"] = [card_map.get(k) for k in key]
    tx_out["merchant_id"] = txn["merchant_id"].to_numpy()
    tx_out["txn_timestamp"] = pd.to_datetime(ts_str, format="%Y-%m-%d %H:%M", errors="coerce")
    tx_out["amount"] = money(txn["Amount"])
    tx_out["use_chip"] = txn["Use Chip"].replace("", np.nan)
    tx_out["is_fraud"] = yesno(txn["Is Fraud?"])
    tx_out["error_flag"] = nonempty_bool(txn["Errors?"]) if "Errors?" in txn.columns else "f"

    # drop transactions whose card could not be resolved (only happens with the
    # Box transactions-only file when no cards file was provided)
    resolved = tx_out["card_id"].notna()
    dropped = int((~resolved).sum())
    tx_out = tx_out[resolved].copy()
    tx_out["card_id"] = tx_out["card_id"].astype("Int64")

    # fraud_alert: one open alert per flagged transaction
    flagged = tx_out.loc[tx_out["is_fraud"] == "t", ["transaction_id", "txn_timestamp"]]
    alerts = pd.DataFrame({
        "transaction_id": flagged["transaction_id"].to_numpy(),
        "alert_status": "OPEN",
        "reviewed_by": np.nan,
        "created_at": flagged["txn_timestamp"].to_numpy(),
    })
    return mcat, merch_out, tx_out, alerts, dropped


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
DDL = """
CREATE TABLE merchant_category (
    mcc                  INTEGER PRIMARY KEY,
    category_description TEXT
);
CREATE TABLE customer (
    customer_id       INTEGER PRIMARY KEY,
    current_age       INTEGER, retirement_age INTEGER,
    birth_year        INTEGER, birth_month    INTEGER,
    gender            TEXT, address TEXT, city TEXT, state TEXT, zipcode TEXT,
    latitude          NUMERIC(9,6), longitude NUMERIC(9,6),
    per_capita_income NUMERIC(14,2), yearly_income NUMERIC(14,2), total_debt NUMERIC(14,2),
    fico_score        INTEGER, num_credit_cards INTEGER
);
CREATE TABLE card (
    card_id            INTEGER PRIMARY KEY,
    customer_id        INTEGER NOT NULL REFERENCES customer(customer_id),
    card_index         INTEGER,
    card_brand         TEXT, card_type TEXT, card_number_masked TEXT,
    expires            TEXT, has_chip BOOLEAN, cards_issued INTEGER,
    credit_limit       NUMERIC(14,2), acct_open_date TEXT,
    UNIQUE (customer_id, card_index)
);
CREATE TABLE merchant (
    merchant_id    INTEGER PRIMARY KEY,
    merchant_name  TEXT, merchant_city TEXT, merchant_state TEXT, zip TEXT,
    mcc            INTEGER REFERENCES merchant_category(mcc)
);
CREATE TABLE transaction (
    transaction_id BIGINT PRIMARY KEY,
    card_id        INTEGER NOT NULL REFERENCES card(card_id),
    merchant_id    INTEGER NOT NULL REFERENCES merchant(merchant_id),
    txn_timestamp  TIMESTAMP,
    amount         NUMERIC(12,2),
    use_chip       TEXT,
    is_fraud       BOOLEAN,
    error_flag     BOOLEAN
);
CREATE TABLE fraud_alert (
    alert_id       BIGSERIAL PRIMARY KEY,
    transaction_id BIGINT NOT NULL REFERENCES transaction(transaction_id),
    alert_status   TEXT DEFAULT 'OPEN',
    reviewed_by    TEXT,
    created_at     TIMESTAMP
);
CREATE INDEX idx_txn_card     ON transaction(card_id);
CREATE INDEX idx_txn_merchant ON transaction(merchant_id);
CREATE INDEX idx_txn_time     ON transaction(txn_timestamp);
CREATE INDEX idx_txn_fraud    ON transaction(is_fraud);
CREATE INDEX idx_merch_state  ON merchant(merchant_state);
CREATE INDEX idx_merch_mcc    ON merchant(mcc);
"""

DROP = """
DROP TABLE IF EXISTS fraud_alert CASCADE;
DROP TABLE IF EXISTS transaction CASCADE;
DROP TABLE IF EXISTS merchant CASCADE;
DROP TABLE IF EXISTS merchant_category CASCADE;
DROP TABLE IF EXISTS card CASCADE;
DROP TABLE IF EXISTS customer CASCADE;
"""


def copy_df(conn, df: pd.DataFrame, table: str, columns: list[str]) -> None:
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, columns=columns, na_rep="")
    buf.seek(0)
    with conn.cursor() as cur:
        cur.copy_expert(
            f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, NULL '')",
            buf,
        )


def load_to_db(dsn, reset, mcat, cust, card, merch, tx, alerts):
    import psycopg2
    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect()
    try:
        with conn:
            with conn.cursor() as cur:
                if reset:
                    cur.execute(DROP)
                cur.execute(DDL)
        with conn:  # single transaction for the whole load
            copy_df(conn, mcat, "merchant_category", ["mcc", "category_description"])
            copy_df(conn, cust, "customer", list(cust.columns))
            copy_df(conn, card, "card", list(card.columns))
            copy_df(conn, merch, "merchant",
                    ["merchant_id", "merchant_name", "merchant_city", "merchant_state", "zip", "mcc"])
            copy_df(conn, tx, "transaction",
                    ["transaction_id", "card_id", "merchant_id", "txn_timestamp",
                     "amount", "use_chip", "is_fraud", "error_flag"])
            if len(alerts):
                copy_df(conn, alerts, "fraud_alert",
                        ["transaction_id", "alert_status", "reviewed_by", "created_at"])
            with conn.cursor() as cur:
                cur.execute("ANALYZE;")
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="FraudGuard ETL loader")
    ap.add_argument("--data-dir", default="./data", help="Folder holding the CSV files")
    ap.add_argument("--users", default=None, help=f"Override users filename (default {USERS_FILE})")
    ap.add_argument("--cards", default=None, help=f"Override cards filename (default {CARDS_FILE})")
    ap.add_argument("--transactions", default=None, help="Override transactions filename")
    ap.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE)
    ap.add_argument("--sample-method", choices=["bernoulli", "head"], default="bernoulli")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dsn", default=None, help="libpq DSN; else uses PG* env vars")
    ap.add_argument("--reset", action="store_true", help="Drop and recreate tables first")
    ap.add_argument("--dry-run", action="store_true", help="Transform and report only; no DB writes")
    args = ap.parse_args()

    users_path = os.path.join(args.data_dir, args.users or USERS_FILE)
    cards_path = os.path.join(args.data_dir, args.cards or CARDS_FILE)
    txn_path = find_transactions_file(args.data_dir, args.transactions)

    have_users = os.path.exists(users_path)
    have_cards = os.path.exists(cards_path)

    print(f"[1/4] Transactions file: {txn_path}")
    print(f"      Users file:        {users_path} ({'found' if have_users else 'MISSING'})")
    print(f"      Cards file:        {cards_path} ({'found' if have_cards else 'MISSING'})")

    if have_users:
        cust = load_customers(users_path)
    else:
        cust = pd.DataFrame(columns=["customer_id"])  # filled from transactions below
    if have_cards:
        card, card_map = load_cards(cards_path)
    else:
        card, card_map = pd.DataFrame(), {}

    print(f"[2/4] Sampling transactions (method={args.sample_method}, target={args.sample_size:,})")
    txn = sample_transactions(txn_path, args.sample_size, args.sample_method, args.seed)
    print(f"      Sampled {len(txn):,} transaction rows")

    # If no cards file, synthesize skeleton customer/card tables from the txns
    if not have_cards:
        pairs = (pd.DataFrame({"customer_id": to_int(txn["User"]), "card_index": to_int(txn["Card"])})
                 .dropna().drop_duplicates().reset_index(drop=True))
        pairs["card_id"] = np.arange(1, len(pairs) + 1, dtype=np.int64)
        card_map = {(int(u), int(ci)): cid
                    for u, ci, cid in zip(pairs["customer_id"], pairs["card_index"], pairs["card_id"])}
        card = pd.DataFrame({"card_id": pairs["card_id"], "customer_id": pairs["customer_id"].astype(int),
                             "card_index": pairs["card_index"].astype(int),
                             "card_brand": np.nan, "card_type": np.nan, "card_number_masked": np.nan,
                             "expires": np.nan, "has_chip": np.nan, "cards_issued": pd.NA,
                             "credit_limit": np.nan, "acct_open_date": np.nan})
        if not have_users:
            cust = pd.DataFrame({"customer_id": sorted(pairs["customer_id"].astype(int).unique())})
            for col in ["current_age", "retirement_age", "birth_year", "birth_month", "gender",
                        "address", "city", "state", "zipcode", "latitude", "longitude",
                        "per_capita_income", "yearly_income", "total_debt", "fico_score",
                        "num_credit_cards"]:
                cust[col] = np.nan

    print("[3/4] Normalizing merchants and building tables")
    mcat, merch, tx, alerts, dropped = build_from_transactions(txn, card_map)
    if dropped:
        print(f"      Note: dropped {dropped:,} transactions with unresolvable card_id")

    print("      Row counts:")
    for name, frame in [("merchant_category", mcat), ("customer", cust), ("card", card),
                        ("merchant", merch), ("transaction", tx), ("fraud_alert", alerts)]:
        print(f"        {name:<18} {len(frame):>9,}")
    fraud_rate = (tx["is_fraud"] == "t").mean() if len(tx) else 0
    print(f"      Fraud rate in sample: {fraud_rate:.3%}")

    if args.dry_run:
        print("[4/4] Dry run: no database writes.")
        return

    print("[4/4] Loading into PostgreSQL ...")
    load_to_db(args.dsn, args.reset, mcat, cust, card, merch, tx, alerts)
    print("      Done. Tables loaded.")


if __name__ == "__main__":
    main()
