#!/usr/bin/env python3
"""Repeatable PostgreSQL performance benchmark for FraudGuard.

The script compares the original single-column index set with a workload-oriented
index set, optionally expands the seed data inside a transaction, captures
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON), and writes CSV/Markdown evidence.
Everything is rolled back when the script exits, so the database is unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

import psycopg2

DEFAULT_DSN = os.environ.get(
    "FRAUDGUARD_DSN",
    "host=127.0.0.1 dbname=fraudguard user=postgres password=postgres",
)

OPTIMIZED_INDEXES = {
    "idx_txn_card_time": """
        CREATE INDEX idx_txn_card_time
            ON transaction(card_id, txn_timestamp DESC)
    """,
    "idx_txn_merchant_time": """
        CREATE INDEX idx_txn_merchant_time
            ON transaction(merchant_id, txn_timestamp DESC)
    """,
    "idx_txn_fraud_time": """
        CREATE INDEX idx_txn_fraud_time
            ON transaction(txn_timestamp DESC) WHERE is_fraud
    """,
    "idx_alert_open_created": """
        CREATE INDEX idx_alert_open_created
            ON fraud_alert(created_at DESC) WHERE alert_status = 'OPEN'
    """,
    "idx_merch_mcc": """
        CREATE INDEX idx_merch_mcc ON merchant(mcc)
    """,
}


@dataclass(frozen=True)
class Benchmark:
    name: str
    sql: str
    params: tuple[Any, ...]
    mutating: bool = False


def parse_targets(raw: str) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value <= 0:
            raise argparse.ArgumentTypeError("target row counts must be positive")
        values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("provide at least one target row count")
    return sorted(set(values))


def require_schema(cur) -> None:
    required = ["customer", "card", "merchant_category", "merchant", "transaction", "fraud_alert"]
    cur.execute(
        """
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = ANY(%s)
        """,
        (required,),
    )
    found = {row[0] for row in cur.fetchall()}
    missing = sorted(set(required) - found)
    if missing:
        raise RuntimeError(f"missing FraudGuard tables: {', '.join(missing)}")

    cur.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'fraud_alert'
           AND column_name = 'reviewed_at'
        """
    )
    if cur.fetchone() is None:
        raise RuntimeError(
            "fraud_alert.reviewed_at is missing; reload sql/01_schema.sql from this repository first"
        )


def drop_optimized_indexes(cur) -> None:
    for name in OPTIMIZED_INDEXES:
        cur.execute(f"DROP INDEX IF EXISTS {name}")


def create_optimized_indexes(cur) -> None:
    for ddl in OPTIMIZED_INDEXES.values():
        cur.execute(ddl)


def row_count(cur) -> int:
    cur.execute("SELECT COUNT(*) FROM transaction")
    return int(cur.fetchone()[0])


def expand_to_target(cur, target: int) -> int:
    """Temporarily duplicate existing rows until transaction reaches target."""
    current = row_count(cur)
    if target <= current:
        return current
    if current == 0:
        raise RuntimeError("cannot scale an empty transaction table; load seed data first")

    needed = target - current
    copies = math.ceil(needed / current)
    cur.execute("SELECT COALESCE(MAX(transaction_id), 0) FROM transaction")
    max_id = int(cur.fetchone()[0])

    cur.execute(
        """
        WITH source_rows AS (
            SELECT t.*
              FROM transaction t
             ORDER BY t.transaction_id
        ), generated AS (
            SELECT s.*,
                   ROW_NUMBER() OVER () AS rn
              FROM source_rows s
              CROSS JOIN generate_series(1, %s) AS g(copy_no)
             LIMIT %s
        )
        INSERT INTO transaction
            (transaction_id, card_id, merchant_id, txn_timestamp, amount,
             use_chip, is_fraud, error_flag)
        SELECT %s + rn,
               card_id,
               merchant_id,
               txn_timestamp
                   + ((rn %% 365) * INTERVAL '1 day')
                   + ((rn %% 1440) * INTERVAL '1 minute'),
               amount,
               use_chip,
               is_fraud,
               error_flag
          FROM generated
        """,
        (copies, needed, max_id),
    )
    return row_count(cur)


def pick_parameters(cur) -> dict[str, Any]:
    cur.execute(
        """
        SELECT card_id
          FROM transaction
         GROUP BY card_id
         ORDER BY COUNT(*) DESC, card_id
         LIMIT 1
        """
    )
    card_id = cur.fetchone()[0]

    cur.execute(
        """
        SELECT merchant_id
          FROM transaction
         GROUP BY merchant_id
         ORDER BY COUNT(*) DESC, merchant_id
         LIMIT 1
        """
    )
    merchant_id = cur.fetchone()[0]

    cur.execute("SELECT MIN(txn_timestamp), MAX(txn_timestamp) FROM transaction")
    min_ts, max_ts = cur.fetchone()
    recent_start = max(min_ts, max_ts - timedelta(days=90))

    cur.execute("SELECT COALESCE(MAX(transaction_id), 0) + 900000000 FROM transaction")
    insert_id = int(cur.fetchone()[0])

    return {
        "card_id": card_id,
        "merchant_id": merchant_id,
        "recent_start": recent_start,
        "insert_id": insert_id,
    }


def build_benchmarks(params: dict[str, Any]) -> list[Benchmark]:
    return [
        Benchmark(
            "card_history",
            """
            SELECT t.transaction_id, t.txn_timestamp, t.amount, t.is_fraud,
                   m.merchant_name, m.merchant_state
              FROM transaction t
              JOIN merchant m ON m.merchant_id = t.merchant_id
             WHERE t.card_id = %s
             ORDER BY t.txn_timestamp DESC
             LIMIT 100
            """,
            (params["card_id"],),
        ),
        Benchmark(
            "merchant_history",
            """
            SELECT t.transaction_id, t.txn_timestamp, t.amount, t.is_fraud,
                   t.card_id
              FROM transaction t
             WHERE t.merchant_id = %s
             ORDER BY t.txn_timestamp DESC
             LIMIT 100
            """,
            (params["merchant_id"],),
        ),
        Benchmark(
            "recent_fraud_investigation",
            """
            SELECT t.transaction_id, t.txn_timestamp, t.amount,
                   c.customer_id, m.merchant_name, m.merchant_state
              FROM transaction t
              JOIN card c ON c.card_id = t.card_id
              JOIN merchant m ON m.merchant_id = t.merchant_id
             WHERE t.is_fraud
               AND t.txn_timestamp >= %s
             ORDER BY t.txn_timestamp DESC
             LIMIT 100
            """,
            (params["recent_start"],),
        ),
        Benchmark(
            "open_alert_queue",
            """
            SELECT a.alert_id, a.created_at, t.transaction_id, t.amount,
                   m.merchant_name, c.customer_id
              FROM fraud_alert a
              JOIN transaction t ON t.transaction_id = a.transaction_id
              JOIN merchant m ON m.merchant_id = t.merchant_id
              JOIN card c ON c.card_id = t.card_id
             WHERE a.alert_status = 'OPEN'
             ORDER BY a.created_at DESC
             LIMIT 100
            """,
            (),
        ),
        Benchmark(
            "monthly_state_report",
            """
            SELECT date_trunc('month', t.txn_timestamp)::date AS month,
                   m.merchant_state,
                   COUNT(*) AS txn_count,
                   SUM(t.amount) AS total_amount,
                   COUNT(*) FILTER (WHERE t.is_fraud) AS fraud_count
              FROM transaction t
              JOIN merchant m ON m.merchant_id = t.merchant_id
             WHERE t.txn_timestamp >= %s
             GROUP BY 1, 2
             ORDER BY 1, 2
            """,
            (params["recent_start"],),
        ),
        Benchmark(
            "insert_nonfraud",
            """
            INSERT INTO transaction
                (transaction_id, card_id, merchant_id, txn_timestamp, amount,
                 use_chip, is_fraud, error_flag)
            VALUES (%s, %s, %s, now(), 42.50, 'Chip Transaction', FALSE, FALSE)
            """,
            (params["insert_id"], params["card_id"], params["merchant_id"]),
            mutating=True,
        ),
        Benchmark(
            "insert_fraud_with_trigger",
            """
            INSERT INTO transaction
                (transaction_id, card_id, merchant_id, txn_timestamp, amount,
                 use_chip, is_fraud, error_flag)
            VALUES (%s, %s, %s, now(), 420.50, 'Online Transaction', TRUE, FALSE)
            """,
            (params["insert_id"] + 1, params["card_id"], params["merchant_id"]),
            mutating=True,
        ),
    ]


def explain_once(cur, benchmark: Benchmark) -> dict[str, Any]:
    explain_sql = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + benchmark.sql
    if benchmark.mutating:
        cur.execute("SAVEPOINT fraudguard_benchmark_insert")
    try:
        cur.execute(explain_sql, benchmark.params)
        payload = cur.fetchone()[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        result = payload[0]
    finally:
        if benchmark.mutating:
            cur.execute("ROLLBACK TO SAVEPOINT fraudguard_benchmark_insert")
            cur.execute("RELEASE SAVEPOINT fraudguard_benchmark_insert")
    return result


def benchmark_query(cur, benchmark: Benchmark, warmups: int, repeats: int) -> tuple[dict[str, Any], dict[str, Any]]:
    for _ in range(warmups):
        explain_once(cur, benchmark)

    plans = [explain_once(cur, benchmark) for _ in range(repeats)]
    execution = [float(p.get("Execution Time", 0.0)) for p in plans]
    planning = [float(p.get("Planning Time", 0.0)) for p in plans]
    median_execution = statistics.median(execution)
    representative_index = min(
        range(len(execution)), key=lambda i: abs(execution[i] - median_execution)
    )
    representative = plans[representative_index]
    top = representative.get("Plan", {})

    summary = {
        "benchmark": benchmark.name,
        "median_execution_ms": round(median_execution, 4),
        "min_execution_ms": round(min(execution), 4),
        "max_execution_ms": round(max(execution), 4),
        "median_planning_ms": round(statistics.median(planning), 4),
        "plan_node": top.get("Node Type", ""),
        "actual_rows": top.get("Actual Rows", ""),
        "shared_hit_blocks": top.get("Shared Hit Blocks", 0),
        "shared_read_blocks": top.get("Shared Read Blocks", 0),
    }
    return summary, representative


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "target_rows", "actual_rows_in_table", "index_mode", "benchmark",
        "median_execution_ms", "min_execution_ms", "max_execution_ms",
        "median_planning_ms", "plan_node", "actual_rows",
        "shared_hit_blocks", "shared_read_blocks",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def improvement_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (int(row["target_rows"]), str(row["benchmark"]))
        grouped.setdefault(key, {})[str(row["index_mode"])] = row

    output = []
    for (target, benchmark), modes in sorted(grouped.items()):
        if "baseline" not in modes or "optimized" not in modes:
            continue
        baseline = float(modes["baseline"]["median_execution_ms"])
        optimized = float(modes["optimized"]["median_execution_ms"])
        improvement = ((baseline - optimized) / baseline * 100.0) if baseline else 0.0
        output.append({
            "target_rows": target,
            "benchmark": benchmark,
            "baseline_ms": baseline,
            "optimized_ms": optimized,
            "improvement_pct": round(improvement, 2),
        })
    return output


def write_markdown(path: Path, rows: list[dict[str, Any]], server_version: str) -> None:
    improvements = improvement_rows(rows)
    lines = [
        "# FraudGuard PostgreSQL Performance Results",
        "",
        f"PostgreSQL server: `{server_version}`",
        "",
        "The benchmark used `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`. Each value is the median execution time across repeated runs. Synthetic scale rows and index changes were performed inside one transaction and rolled back after the test.",
        "",
        "| Rows | Benchmark | Baseline ms | Optimized ms | Change |",
        "|---:|---|---:|---:|---:|",
    ]
    for row in improvements:
        lines.append(
            f"| {row['target_rows']:,} | {row['benchmark']} | "
            f"{row['baseline_ms']:.4f} | {row['optimized_ms']:.4f} | "
            f"{row['improvement_pct']:+.2f}% |"
        )

    lines.extend([
        "",
        "## Interpretation guide",
        "",
        "- Positive change means the optimized index set was faster.",
        "- Small or negative changes at 2,000 rows are normal because sequential scans can be cheaper on tiny tables.",
        "- The most important evidence is whether card history, merchant history, recent fraud lookup, and the open-alert queue improve as row counts grow.",
        "- Insert tests show the write cost of maintaining indexes and the additional trigger cost for fraudulent transactions.",
        "",
        "Full measurements are in `performance_results.csv`; representative JSON plans are in `performance_plans.json`.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark FraudGuard PostgreSQL performance")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="libpq DSN")
    parser.add_argument(
        "--target-rows",
        type=parse_targets,
        default=parse_targets("2000,20000,100000,200000"),
        help="comma-separated temporary transaction row counts",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output-dir", default="performance_results")
    args = parser.parse_args()

    if args.repeats < 1 or args.warmups < 0:
        parser.error("repeats must be >= 1 and warmups must be >= 0")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    all_rows: list[dict[str, Any]] = []
    all_plans: dict[str, Any] = {}

    try:
        with conn.cursor() as cur:
            require_schema(cur)
            cur.execute("SHOW server_version")
            server_version = cur.fetchone()[0]
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("SET LOCAL lock_timeout = '15s'")

            initial = row_count(cur)
            targets = sorted(set([initial] + [t for t in args.target_rows if t >= initial]))
            print(f"PostgreSQL {server_version}; starting with {initial:,} transactions")

            for target in targets:
                drop_optimized_indexes(cur)
                actual = expand_to_target(cur, target)
                cur.execute("ANALYZE transaction")
                cur.execute("ANALYZE fraud_alert")
                params = pick_parameters(cur)
                benchmarks = build_benchmarks(params)
                print(f"\nBenchmarking {actual:,} transaction rows")

                for mode in ("baseline", "optimized"):
                    if mode == "optimized":
                        create_optimized_indexes(cur)
                        cur.execute("ANALYZE transaction")
                        cur.execute("ANALYZE fraud_alert")

                    print(f"  {mode} indexes")
                    for bench in benchmarks:
                        summary, plan = benchmark_query(cur, bench, args.warmups, args.repeats)
                        summary.update({
                            "target_rows": target,
                            "actual_rows_in_table": actual,
                            "index_mode": mode,
                        })
                        all_rows.append(summary)
                        all_plans[f"{target}:{mode}:{bench.name}"] = plan
                        print(f"    {bench.name:<30} {summary['median_execution_ms']:>10.4f} ms")

            write_csv(output_dir / "performance_results.csv", all_rows)
            write_markdown(output_dir / "performance_report.md", all_rows, server_version)
            (output_dir / "performance_plans.json").write_text(
                json.dumps(all_plans, indent=2, default=str), encoding="utf-8"
            )

        print(f"\nResults written to {output_dir.resolve()}")
        print("Database changes were temporary and will now be rolled back.")
        return 0
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
