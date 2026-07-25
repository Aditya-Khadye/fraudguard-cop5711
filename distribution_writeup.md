# Scaling FraudGuard out: sharding and distribution plan

This is the conceptual plan for how FraudGuard would run across multiple PostgreSQL nodes. We didn't implement a cluster for the project (single node is fine at our seed size), but the design below is what we'd do for the real workload: the full IBM dataset is ~24M transactions and a production card network would keep growing past that.

## What grows and what doesn't

The tables split cleanly into two groups. `transaction` and `fraud_alert` grow without bound, every swipe adds a row. Everything else is basically fixed size: `customer` and `card` grow with the customer base (slowly), and `merchant_category` (~300 MCC codes max) and `merchant` are small reference data. So the distribution problem is really about one big table and its satellites.

## Sharding the transaction table

We'd shard `transaction` by **hash(card_id)**. Reasons:

- Writes spread evenly. Card activity is roughly uniform across hash buckets, so no node becomes the hot one. If we sharded by merchant_state instead, big states like CA and TX would overload their nodes while small states idle (data skew).
- The most common operational query, "show me this card's / this customer's history", hits exactly one shard, because all of a card's transactions hash to the same place.
- The fraud trigger stays local: a transaction insert and the fraud_alert row it opens happen on the same node, so the write path never crosses shards.

The trade-off we're accepting: regional analytics (fraud rate by merchant_state, our Q1/Q8 style queries) now touch every shard. That's fine, those are reporting queries, and they parallelize well, each shard computes its partial aggregate and a coordinator merges them (scatter-gather). We'd rather pay a parallel scan on monthly reports than a hot spot on every write.

We also considered range-sharding by txn_timestamp (nice for time-window queries and archiving old months), but it makes the newest shard take 100% of the writes, which is the exact hot spot problem again.

## Keeping joins local: co-location

`card` and `customer` get distributed by the same key family: card by hash(card_id), and customer placed with their cards. That way the join chain transaction -> card -> customer resolves on one node. `fraud_alert` is co-located with `transaction` (its parent row), so the analyst review workflow (trigger insert + review_alert() update) is always single-node.

## Small tables: replicate everywhere

`merchant` and `merchant_category` are tiny (hundreds of rows, low churn) and joined by almost every query. Instead of sharding them, we'd keep a full copy on every node (reference/broadcast tables). Then a shard computing its slice of the monthly fraud summary joins merchant locally instead of pulling rows over the network.

## Replication for availability

Each shard would have one or two replicas via Postgres streaming replication. For this workload we'd use async replication for the replicas (a few ms of replication lag is acceptable for a monitoring dashboard) but the primary of each shard is the single source of truth for writes. Replicas also absorb read traffic from the dashboard, which refreshes every few seconds in live mode and shouldn't compete with the insert path.

## Cross-shard writes and 2PC

Single-card operations never cross shards, which is most of the traffic. The case that does is a multi-card operation, e.g. moving a dispute/chargeback between two accounts whose cards live on different shards. That's where two-phase commit comes in: both shards prepare, and the transaction only commits if every participant votes yes, otherwise everyone rolls back. Our 2PC demo shows exactly this pattern with PREPARE TRANSACTION / COMMIT PREPARED across two databases standing in for two shards.

## Summary table

| Table | Strategy | Why |
|---|---|---|
| transaction | shard by hash(card_id) | balanced writes, card history stays single-shard |
| fraud_alert | co-located with transaction | trigger + review stay local |
| card, customer | distributed with their transactions | joins stay local |
| merchant, merchant_category | replicated to all nodes | small, read-heavy, joined everywhere |
