import os
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, jsonify

app = Flask(__name__)

DSN = os.environ.get("FRAUDGUARD_DSN", "host=127.0.0.1 dbname=fraudguard user=postgres password=postgres")


def get_conn():
    return psycopg2.connect(DSN)


def query(sql, params=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()


@app.route("/")
def dashboard():
    stats = query("""
        SELECT COUNT(*) AS txns,
               COUNT(*) FILTER (WHERE is_fraud) AS fraud,
               ROUND(100.0 * COUNT(*) FILTER (WHERE is_fraud) / COUNT(*), 1) AS fraud_pct,
               SUM(amount) AS volume,
               (SELECT COUNT(*) FROM fraud_alert WHERE alert_status = 'OPEN') AS open_alerts
        FROM transaction
    """)[0]
    monthly = query("""
        SELECT month, merchant_state, txn_count, total_amount, fraud_count, fraud_pct
        FROM v_monthly_fraud_summary
        ORDER BY month DESC, txn_count DESC
        LIMIT 15
    """)
    alerts = query("""
        SELECT a.alert_id, a.alert_status, t.txn_timestamp, t.amount,
               m.merchant_name, m.merchant_state, c.customer_id
        FROM fraud_alert a
        JOIN transaction t ON t.transaction_id = a.transaction_id
        JOIN merchant m ON m.merchant_id = t.merchant_id
        JOIN card cd ON cd.card_id = t.card_id
        JOIN customer c ON c.customer_id = cd.customer_id
        ORDER BY (a.alert_status = 'OPEN') DESC, a.created_at DESC
    """)
    return render_template("index.html", stats=stats, monthly=monthly, alerts=alerts)


# backend api endpoint (json), same data the page uses
@app.route("/api/monthly_summary")
def api_monthly():
    rows = query("SELECT * FROM v_monthly_fraud_summary ORDER BY month, merchant_state")
    return jsonify([dict(r) for r in rows])


@app.route("/alerts/<int:alert_id>/review", methods=["POST"])
def review(alert_id):
    decision = request.form.get("decision")
    reviewer = request.form.get("reviewer") or "analyst"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CALL review_alert(%s, %s, %s)", (alert_id, reviewer, decision))
    except psycopg2.Error as e:
        return f"error: {e.pgerror or e}", 400
    return redirect(request.referrer or "/")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
