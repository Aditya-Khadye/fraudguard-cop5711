-- FraudGuard schema
-- constraints are defined up front (PK, FK, NOT NULL, UNIQUE, CHECK) before any data goes in,
-- plus a trigger, a stored procedure, and a report view so the db behaves like a real system.

DROP TABLE IF EXISTS fraud_alert CASCADE;
DROP TABLE IF EXISTS transaction CASCADE;
DROP TABLE IF EXISTS merchant CASCADE;
DROP TABLE IF EXISTS merchant_category CASCADE;
DROP TABLE IF EXISTS card CASCADE;
DROP TABLE IF EXISTS customer CASCADE;
DROP VIEW IF EXISTS v_monthly_fraud_summary;

CREATE TABLE merchant_category (
    mcc                  INTEGER PRIMARY KEY,
    category_description TEXT NOT NULL,
    CONSTRAINT chk_mcc_range CHECK (mcc BETWEEN 1 AND 9999)
);

CREATE TABLE customer (
    customer_id       INTEGER PRIMARY KEY,
    current_age       INTEGER CHECK (current_age BETWEEN 18 AND 120),
    gender            TEXT,
    city              TEXT,
    state             CHAR(2),
    zipcode           TEXT,
    latitude          NUMERIC(9,6) CHECK (latitude  BETWEEN -90  AND 90),
    longitude         NUMERIC(9,6) CHECK (longitude BETWEEN -180 AND 180),
    yearly_income     NUMERIC(14,2) CHECK (yearly_income >= 0),
    total_debt        NUMERIC(14,2) CHECK (total_debt >= 0),
    fico_score        INTEGER CHECK (fico_score BETWEEN 300 AND 850)
);

CREATE TABLE card (
    card_id            INTEGER PRIMARY KEY,
    customer_id        INTEGER NOT NULL REFERENCES customer(customer_id),
    card_index         INTEGER NOT NULL,
    card_brand         TEXT NOT NULL,
    card_type          TEXT NOT NULL CHECK (card_type IN ('Credit','Debit','Debit (Prepaid)')),
    card_number_masked CHAR(8),
    expires            CHAR(7),                 -- MM/YYYY
    has_chip           BOOLEAN NOT NULL DEFAULT TRUE,
    credit_limit       NUMERIC(14,2) CHECK (credit_limit >= 0),
    CONSTRAINT uq_customer_cardindex UNIQUE (customer_id, card_index)
);

CREATE TABLE merchant (
    merchant_id    INTEGER PRIMARY KEY,
    merchant_name  TEXT NOT NULL,
    merchant_city  TEXT,
    merchant_state CHAR(2),
    zip            TEXT,
    mcc            INTEGER NOT NULL REFERENCES merchant_category(mcc)
);

CREATE TABLE transaction (
    transaction_id BIGINT PRIMARY KEY,
    card_id        INTEGER NOT NULL REFERENCES card(card_id),
    merchant_id    INTEGER NOT NULL REFERENCES merchant(merchant_id),
    txn_timestamp  TIMESTAMP NOT NULL,
    amount         NUMERIC(12,2) NOT NULL CHECK (amount <> 0),  -- negative = refund/return
    use_chip       TEXT CHECK (use_chip IN ('Swipe Transaction','Chip Transaction','Online Transaction')),
    is_fraud       BOOLEAN NOT NULL DEFAULT FALSE,
    error_flag     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE fraud_alert (
    alert_id       BIGSERIAL PRIMARY KEY,
    transaction_id BIGINT NOT NULL UNIQUE REFERENCES transaction(transaction_id),
    alert_status   TEXT NOT NULL DEFAULT 'OPEN'
                   CHECK (alert_status IN ('OPEN','CONFIRMED','DISMISSED')),
    reviewed_by    TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT now(),
    -- a reviewed alert must say who reviewed it
    CONSTRAINT chk_review CHECK (alert_status = 'OPEN' OR reviewed_by IS NOT NULL)
);

CREATE INDEX idx_txn_card     ON transaction(card_id);
CREATE INDEX idx_txn_merchant ON transaction(merchant_id);
CREATE INDEX idx_txn_time     ON transaction(txn_timestamp);
CREATE INDEX idx_merch_state  ON merchant(merchant_state);

-- TRIGGER: business process. any transaction inserted with is_fraud = true
-- automatically opens a fraud alert for an analyst to review.
CREATE OR REPLACE FUNCTION fn_open_fraud_alert() RETURNS trigger AS $$
BEGIN
    IF NEW.is_fraud THEN
        INSERT INTO fraud_alert (transaction_id, created_at)
        VALUES (NEW.transaction_id, NEW.txn_timestamp);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_open_fraud_alert
AFTER INSERT ON transaction
FOR EACH ROW EXECUTE FUNCTION fn_open_fraud_alert();

-- STORED PROCEDURE: analyst reviews an alert (second half of the business process)
CREATE OR REPLACE PROCEDURE review_alert(p_alert_id BIGINT, p_reviewer TEXT, p_decision TEXT)
LANGUAGE plpgsql AS $$
BEGIN
    IF p_decision NOT IN ('CONFIRMED','DISMISSED') THEN
        RAISE EXCEPTION 'decision must be CONFIRMED or DISMISSED';
    END IF;
    UPDATE fraud_alert
       SET alert_status = p_decision, reviewed_by = p_reviewer
     WHERE alert_id = p_alert_id AND alert_status = 'OPEN';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'alert % not found or already reviewed', p_alert_id;
    END IF;
END;
$$;

-- VIEW: monthly report base (fraud + volume per state per month)
CREATE VIEW v_monthly_fraud_summary AS
SELECT date_trunc('month', t.txn_timestamp)::date AS month,
       m.merchant_state,
       COUNT(*)                                   AS txn_count,
       SUM(t.amount)                              AS total_amount,
       COUNT(*) FILTER (WHERE t.is_fraud)         AS fraud_count,
       ROUND(100.0 * COUNT(*) FILTER (WHERE t.is_fraud) / COUNT(*), 2) AS fraud_pct
FROM transaction t
JOIN merchant m ON m.merchant_id = t.merchant_id
GROUP BY 1, 2;
