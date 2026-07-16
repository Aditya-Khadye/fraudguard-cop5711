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

-- Q2 TODO: top 5 merchants by fraud dollar amount per month (window: RANK/PARTITION BY month)
-- Q3 TODO: customers whose monthly spend exceeds 2x their average (nested/correlated subquery)
-- Q4 TODO: refund/return analysis by merchant category (negative amounts = returns, join + HAVING)
-- Q5 TODO: alert review workflow report: open vs confirmed vs dismissed, avg time-to-review
-- Q6 TODO: card-type risk profile: fraud rate by card_type x use_chip (multi-dim aggregate)
-- Q7 TODO: spend vs income: top decile customers by spend/income ratio (join customer, NTILE)
-- Q8 TODO: state pairs: customer state vs merchant state mismatch as fraud signal (self-ish join)
