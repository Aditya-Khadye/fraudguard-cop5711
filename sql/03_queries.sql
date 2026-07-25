-- FraudGuard analytical queries (6-8 required)
-- specs from the progress check: each query must be COMPLEX (no plain summary stats),
-- monthly-report / business-process style. mix of joins, nested queries, aggregates,
-- views, window functions.
--
-- Q1 (done, example): month-over-month fraud rate change by state
--     uses the v_monthly_fraud_summary view + a window function
SELECT month, merchant_state, txn_count, fraud_pct,
       fraud_pct - LAG(fraud_pct) OVER (PARTITION BY merchant_state ORDER BY month) AS fraud_pct_change
FROM v_monthly_fraud_summary
ORDER BY merchant_state, month;

-- Q2: top 5 merchants by fraud dollar amount per month
-- window: RANK/PARTITION BY month
SELECT *
FROM (
    SELECT
        date_trunc('month', t.txn_timestamp)::date AS month,
        m.merchant_name,
        m.merchant_state,
        COUNT(*) FILTER (WHERE t.is_fraud)          AS fraud_txn_count,
        SUM(t.amount) FILTER (WHERE t.is_fraud)     AS fraud_dollar_amount,
        ROW_NUMBER() OVER (
            PARTITION BY date_trunc('month', t.txn_timestamp)
            ORDER BY SUM(t.amount) FILTER (WHERE t.is_fraud) DESC NULLS LAST
        ) AS fraud_rank
    FROM transaction t
    JOIN merchant m ON m.merchant_id = t.merchant_id
    GROUP BY date_trunc('month', t.txn_timestamp), m.merchant_id, m.merchant_name, m.merchant_state
    HAVING COUNT(*) FILTER (WHERE t.is_fraud) > 0
) ranked
WHERE fraud_rank <= 5
ORDER BY month, fraud_rank;

-- Q3: customers whose monthly spend exceeds 2x their overall average monthly spend
-- nested/correlated subquery
SELECT
    c.customer_id,
    c.city,
    c.state,
    date_trunc('month', t.txn_timestamp)::date AS month,
    SUM(t.amount)                               AS monthly_spend,
    (
        SELECT AVG(monthly_total)
        FROM (
            SELECT date_trunc('month', t2.txn_timestamp) AS mo,
                   SUM(t2.amount) AS monthly_total
            FROM transaction t2
            JOIN card cd2 ON cd2.card_id = t2.card_id
            WHERE cd2.customer_id = c.customer_id
              AND t2.amount > 0
            GROUP BY date_trunc('month', t2.txn_timestamp)
        ) monthly_sums
    ) AS avg_monthly_spend
FROM customer c
JOIN card cd ON cd.customer_id = c.customer_id
JOIN transaction t ON t.card_id = cd.card_id
WHERE t.amount > 0
GROUP BY c.customer_id, c.city, c.state, date_trunc('month', t.txn_timestamp)
HAVING SUM(t.amount) > 2 * (
    SELECT AVG(monthly_total)
    FROM (
        SELECT date_trunc('month', t2.txn_timestamp) AS mo,
               SUM(t2.amount) AS monthly_total
        FROM transaction t2
        JOIN card cd2 ON cd2.card_id = t2.card_id
        WHERE cd2.customer_id = c.customer_id
          AND t2.amount > 0
        GROUP BY date_trunc('month', t2.txn_timestamp)
    ) monthly_sums
)
ORDER BY month, monthly_spend DESC;

-- Q4: refund/return analysis by merchant category
-- negative amounts = returns, join + HAVING
SELECT
    mc.category_description,
    COUNT(*) FILTER (WHERE t.amount < 0)            AS return_count,
    COUNT(*) FILTER (WHERE t.amount > 0)            AS purchase_count,
    ROUND(SUM(t.amount) FILTER (WHERE t.amount < 0)::numeric, 2) AS total_refund_amount,
    ROUND(SUM(t.amount) FILTER (WHERE t.amount > 0)::numeric, 2) AS total_purchase_amount,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE t.amount < 0) / COUNT(*), 2
    ) AS return_rate_pct
FROM transaction t
JOIN merchant m ON m.merchant_id = t.merchant_id
JOIN merchant_category mc ON mc.mcc = m.mcc
GROUP BY mc.category_description
HAVING COUNT(*) FILTER (WHERE t.amount < 0) > 0
ORDER BY return_count DESC;

-- Q5: alert review workflow report
-- open vs confirmed vs dismissed, avg time-to-review
SELECT
    fa.alert_status,
    COUNT(*)                                        AS alert_count,
    ROUND(AVG(t.amount), 2)                         AS avg_fraud_amount,
    MIN(t.amount)                                   AS min_amount,
    MAX(t.amount)                                   AS max_amount,
    ROUND(
        AVG(
            EXTRACT(EPOCH FROM (fa.reviewed_at - fa.created_at)) / 3600
        ), 2
    ) AS avg_hours_to_review,
    COUNT(*) FILTER (WHERE t.error_flag)            AS alerts_with_errors
FROM fraud_alert fa
JOIN transaction t ON t.transaction_id = fa.transaction_id
GROUP BY fa.alert_status
ORDER BY alert_count DESC;

-- Q6: card-type risk profile: fraud rate by card_type x use_chip
-- multi-dimensional aggregate
SELECT
    cd.card_type,
    t.use_chip,
    COUNT(*)                                        AS total_txns,
    COUNT(*) FILTER (WHERE t.is_fraud)              AS fraud_txns,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE t.is_fraud) / COUNT(*), 2
    )                                               AS fraud_rate_pct,
    ROUND(AVG(t.amount), 2)                         AS avg_txn_amount,
    ROUND(AVG(t.amount) FILTER (WHERE t.is_fraud), 2) AS avg_fraud_amount
FROM transaction t
JOIN card cd ON cd.card_id = t.card_id
GROUP BY cd.card_type, t.use_chip
ORDER BY fraud_rate_pct DESC;

-- Q7: spend vs income: top decile customers by spend/income ratio
-- join customer, NTILE window function
SELECT *
FROM (
    SELECT
        c.customer_id,
        c.city,
        c.state,
        c.yearly_income,
        ROUND(SUM(t.amount) FILTER (WHERE t.amount > 0), 2) AS total_spend,
        ROUND(
            SUM(t.amount) FILTER (WHERE t.amount > 0) / NULLIF(c.yearly_income, 0), 4
        )                                                    AS spend_income_ratio,
        NTILE(10) OVER (
            ORDER BY SUM(t.amount) FILTER (WHERE t.amount > 0) / NULLIF(c.yearly_income, 0) DESC
        )                                                    AS decile
    FROM customer c
    JOIN card cd ON cd.customer_id = c.customer_id
    JOIN transaction t ON t.card_id = cd.card_id
    WHERE c.yearly_income IS NOT NULL
    GROUP BY c.customer_id, c.city, c.state, c.yearly_income
) deciles
WHERE decile = 1
ORDER BY spend_income_ratio DESC;

-- Q8: state pairs: customer state vs merchant state mismatch as fraud signal
-- customers transacting in a different state than their home state
SELECT
    c.state                                         AS customer_home_state,
    m.merchant_state                                AS merchant_state,
    COUNT(*)                                        AS total_txns,
    COUNT(*) FILTER (WHERE t.is_fraud)              AS fraud_txns,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE t.is_fraud) / COUNT(*), 2
    )                                               AS fraud_rate_pct,
    ROUND(AVG(t.amount), 2)                         AS avg_amount
FROM transaction t
JOIN card cd ON cd.card_id = t.card_id
JOIN customer c ON c.customer_id = cd.customer_id
JOIN merchant m ON m.merchant_id = t.merchant_id
WHERE c.state IS NOT NULL
  AND m.merchant_state IS NOT NULL
  AND c.state <> m.merchant_state
GROUP BY c.state, m.merchant_state
HAVING COUNT(*) >= 3
ORDER BY fraud_rate_pct DESC, total_txns DESC;