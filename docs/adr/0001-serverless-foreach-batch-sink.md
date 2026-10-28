# ADR 0001 — Serverless SDP foreach_batch_sink writes gold to Lakebase

Status: accepted (2026-10-15, validated in F0)

Context: silver is a regular view without CDF; serverless only; 30 s freshness; the bronze pipeline cannot be modified.

Decision: a second continuous serverless SDP pipeline reads bronze as a stream; a `foreach_batch_sink` recomputes touched keys from UC views and writes Lakebase (psycopg, OAuth) plus a Delta replica.

Evidence: F0 probe — p95 event→Lakebase 9.4 s over 39 batches; upsert + delete in one transaction; batch replay skipped; UC replica equal to Lakebase; ran as SP.

Consequences: we own connection/retry code; writes go through the batch driver (fine for operational volumes).
