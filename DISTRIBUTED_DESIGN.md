# FraudGuard Distributed and Parallel Database Design

## Recommended architecture

FraudGuard should scale in two stages. First, PostgreSQL should range-partition the rapidly growing `transaction` table by `txn_timestamp`, preferably one partition per month. Second, if the workload exceeds a single server, Citus can hash-distribute transaction data across worker nodes while small lookup tables are replicated as reference tables.

This hybrid design addresses two different problems:

- **Time partitioning** reduces the amount of transaction data scanned by monthly and recent-period reports and makes old-data retention easier.
- **Hash distribution** spreads storage and processing across nodes while keeping a card's transaction history together.

## Table placement

| Table | Recommended placement | Reason |
|---|---|---|
| `transaction` | Monthly range partitions; hash-distributed by `card_id` in Citus | It is the largest and fastest-growing table. Card-history queries become single-shard operations. |
| `fraud_alert` | Co-located with `transaction` using `card_id` | An alert is always accessed with its transaction. Co-location avoids a distributed join during review. The distributed version should add `card_id` to this table. |
| `card` | Hash-distributed by `card_id` | Co-locates card details with card transactions. |
| `customer` | Reference table for the course dataset; customer-distributed in a larger production system | It is small enough to replicate in the project dataset. A much larger customer table would require a customer-centric shard design. |
| `merchant` | Reference table for the course dataset | It is repeatedly joined to transactions for names, locations, and categories. Replication avoids network movement during those joins. |
| `merchant_category` | Reference table | It is a very small lookup table and should be available on every worker. |
| Monthly fraud summaries | Materialized summary or reporting table | Prevents dashboards from repeatedly scanning raw transactions. |

## Distribution-key decision

### Selected key: `card_id`

`card_id` is the best fit for the current normalized schema because it already exists in `transaction` and directly supports one of the required outputs: per-card transaction history. Hashing also spreads cards across many shards more evenly than a small geographic list such as state.

With `card_id` as the shard key:

- Card-history lookups can be routed to one shard.
- New transactions for different cards can be written in parallel.
- Fraud alerts can be co-located with their source transactions if `card_id` is copied into `fraud_alert`.
- Customer history may touch several shards because one customer can own several cards, but the number of cards per customer is normally small.

### Why `merchant_state` should not be the primary shard key

State-based sharding makes regional reports convenient, but it is a poor primary key for the overall workload:

- The number of states is small, limiting distribution flexibility.
- Transaction volume can be highly uneven between states.
- Online or missing-location transactions can concentrate in one shard.
- A customer's transactions would be scattered across many states and nodes.
- Adding or rebalancing geographic regions is more disruptive than hash rebalancing.

`merchant_state` remains useful as a report dimension or an optional list subpartition, but not as the main Citus distribution column.

## Alternative customer-centric design

If per-customer history becomes more important than per-card history, add `customer_id` directly to `transaction` and `fraud_alert`, then distribute `customer`, `card`, `transaction`, and `fraud_alert` by `customer_id`. This denormalizes one key into the fact table but allows every customer's cards, transactions, and alerts to remain on the same shard.

The team should present `card_id` as the selected design for the current schema and mention `customer_id` as the production alternative.

## PostgreSQL partitioning plan

Use monthly range partitions on `txn_timestamp`:

```sql
CREATE TABLE transaction_partitioned (
    transaction_id BIGINT NOT NULL,
    card_id INTEGER NOT NULL,
    merchant_id INTEGER NOT NULL,
    txn_timestamp TIMESTAMP NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    use_chip TEXT,
    is_fraud BOOLEAN NOT NULL,
    error_flag BOOLEAN NOT NULL,
    PRIMARY KEY (transaction_id, txn_timestamp)
) PARTITION BY RANGE (txn_timestamp);

CREATE TABLE transaction_2026_07
PARTITION OF transaction_partitioned
FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
```

The partitioned primary key includes `txn_timestamp` because PostgreSQL requires a partitioned table's unique or primary-key constraint to include the partition key.

`sql/05_partitioning_demo.sql` creates a rollback-only partitioned clone from the currently loaded data and displays comparable `EXPLAIN (ANALYZE, BUFFERS)` plans.

## Conceptual Citus configuration

The following is a design example rather than a required project installation:

```sql
CREATE EXTENSION IF NOT EXISTS citus;

SELECT create_reference_table('merchant_category');
SELECT create_reference_table('merchant');
SELECT create_reference_table('customer');

SELECT create_distributed_table('card', 'card_id');
SELECT create_distributed_table('transaction', 'card_id', colocate_with => 'card');

-- Distributed version: add card_id to fraud_alert before distribution.
SELECT create_distributed_table('fraud_alert', 'card_id',
                                colocate_with => 'transaction');
```

Co-located tables must use compatible distribution columns so related rows can be placed on the same workers.

## Query behavior

### Single-shard or local operations

- Card transaction history by `card_id`
- Insert of one card transaction
- Fraud alert creation and review when the alert is co-located
- Card-level fraud investigation

### Parallel scatter-gather operations

- Fraud totals by merchant state
- Fraud totals by merchant category
- Global top merchants
- Overall monthly trend reports

These analytical queries can run in parallel across workers, after which partial aggregates are merged by the coordinator. Frequently requested dashboard results should be stored in a materialized monthly summary so the application does not rescan all raw transactions on every refresh.

## Replication and availability

Reference tables should be copied to every worker because they are small and frequently joined to the transaction table. Read replicas can serve dashboards and reporting workloads, while the primary nodes continue accepting inserts and alert reviews. Replicas improve read capacity but may show a short replication delay, so the live alert queue should read from the primary or from a synchronously updated source when immediate visibility is required.

## Distributed transactions and consistency

Most FraudGuard writes affect one card and can remain on one shard. A single-shard transaction can atomically insert the transaction and its fraud alert. Two-phase commit is only necessary when one business operation changes data on multiple shards or nodes. Cross-shard transactions provide stronger atomicity but add coordination cost and should therefore be avoided when the data model can keep related rows together.

## Tradeoffs and risks

- **Shard-key skew:** a few extremely active cards could create hot shards, although hash distribution greatly reduces this risk compared with state-based sharding.
- **Cross-shard customer queries:** customers with multiple cards may require results from several shards under the selected `card_id` design.
- **Reference-table growth:** if `merchant` or `customer` becomes too large to replicate, it should be distributed and the data model reconsidered.
- **Partition maintenance:** a new time partition must be created before each new month unless a default partition or automated maintenance job is used.
- **Global uniqueness:** distributed systems may require identifiers generated in a way that avoids collisions across nodes.
- **Operational complexity:** sharding and replication add monitoring, failure recovery, and schema-migration work that is unnecessary for the current 2,000-row demonstration.

## Final recommendation

For the submitted project, keep the working single-node PostgreSQL implementation, demonstrate monthly range partitioning using `sql/05_partitioning_demo.sql`, and describe Citus hash distribution by `card_id` as the next scaling step. Replicate `merchant`, `merchant_category`, and the small project `customer` table, and co-locate `card`, `transaction`, and `fraud_alert`. This design is consistent with the current schema, the project's card-history workload, and the requirement to discuss distributed and parallel database scaling without claiming that a full cluster was deployed.
