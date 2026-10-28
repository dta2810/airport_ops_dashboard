# Architecture

## Flow

1. Simulator writes milestone events (JSON) to a UC volume.
2. Bronze pipeline (continuous, serverless) ingests them with Auto Loader into `bronze_flight_milestones`, clustered by `event_date, airport_iata`.
3. Gold pipeline (continuous, serverless, run-as SP) reads bronze as a stream. Each micro-batch: collect touched keys per gold grain → recompute only those keys from the UC silver/gold views using literal `IN` lists → MERGE into the Delta replica → upsert + delete into Lakebase in one transaction, skipping replayed `batch_id`s.
4. The app reads Lakebase every 30 s.

## Why this design

| Option | Serverless | Works with a no-CDF silver view | Freshness | Outcome |
|---|---|---|---|---|
| Synced table (Triggered/Continuous) from gold view | Yes | No (views only sync in Snapshot) | Minutes | Rejected for gold |
| Native Lakebase sink `format("postgresql")` | Only `AvailableNow` | Only for DataFrame-expressible logic | Unmeasured cycle | Not chosen |
| Lakebase Sink on SDP | — | — | — | Private Preview; optional comparison only |
| SDP `foreach_batch_sink` + psycopg (chosen) | Yes, continuous | Yes (batch reads inside the batch) | p95 9.4 s (F0) | Chosen |
| Gold Delta with CDF + synced Continuous | Yes | Yes | +~4 s vs direct (F0 sample) | Fallback |

Decisions are recorded in [adr/](adr/).
