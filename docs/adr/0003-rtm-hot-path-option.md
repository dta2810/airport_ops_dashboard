# ADR 0003 — Optional sub-second hot path with Real-Time Mode (RTM)

Status: proposed (2026-10-20, docs review only — not probed)

Context: today event → Lakebase is p95 14–18 s (ADR 0001). If the customer's bronze pipeline ingests from Kafka/MSK, the same topic could feed a sub-second path. Constraint unchanged: the bronze pipeline cannot be modified.

What the docs allow (checked 2026-10-20):
- RTM in Lakeflow pipelines (GA): sources Kafka / Event Hubs / Kinesis / MSK; sinks Kafka / Event Hubs / MSK only. Delta as source or sink, `foreach` and `foreachBatch` are not supported. RTM is set per flow (`@dp.update_flow`, `pipelines.trigger: "RealTime"`); multiple flows allowed but slot contention raises latency. https://docs.databricks.com/aws/en/ldp/real-time
- Structured Streaming Lakebase sink (GA for Lakebase): upsert via `upsertkey` (`INSERT … ON CONFLICT DO UPDATE`), creates the table if missing. RTM trigger only on classic compute (DBR 18 LTS+, dedicated or standard access); serverless only `AvailableNow`/`Once`; Lakeflow pipelines not supported. https://docs.databricks.com/aws/en/structured-streaming/lakebase — example "Kafka source to Lakebase sink": https://docs.databricks.com/aws/en/structured-streaming/real-time/examples
- RTM compute: no autoscaling, Photon or spot; task slots for all stages at once; update output mode only. https://docs.databricks.com/aws/en/structured-streaming/real-time/reference

- A pipeline-native Lakebase sink for RTM flows was in preview (not GA) at the time of writing. If it reaches GA, the hot path could run as a serverless pipeline reading Kafka instead of a classic job — re-check before deciding.
- Delta as an RTM source or sink: not supported and, per the streaming team (2026-09), not planned; micro-batch is the recommended path for Delta.
- Known RTM gaps seen in the field: no outer stream-stream joins; the native Lakebase sink resolves the running identity to a user (a cluster running as a group identity cannot mint the credential — use a user or service principal identity).

Answers to the two design questions:
1. Extend the existing Kafka → bronze pipeline to write Lakebase in real time? No. An RTM flow inside a pipeline cannot write to Lakebase (Kafka-family sinks only), and the bronze pipeline is not ours to change.
2. A new pipeline doing RTM from bronze? No. RTM cannot read Delta (bronze is a streaming table). Reading bronze stays micro-batch (ADR 0001).

Decision (proposed): add a parallel hot path that reads the same Kafka topic with its own consumer group: a standalone Structured Streaming job, `trigger(realTime=…)`, on classic dedicated compute, writing `ops.flight_state` with the Lakebase sink (upsert on `flight_id`). Bronze and the gold pipeline stay as they are; gold keeps the aggregate tables (`airport_hour_kpis`, `gate_hour_occupancy`, `hub_connections`) and the UC replicas for Genie.

Rules if adopted:
- One writer per Lakebase table: the RTM job owns `flight_state`; the gold pipeline stops writing it to Lakebase (it still writes the Delta replica).
- Out-of-order events: the sink's upsert is unconditional as documented; verify whether a guard on `last_event_ts` is possible, otherwise use a custom `ForeachWriter` with a conditional upsert.
- Only per-flight state fits row-at-a-time logic; joins and hourly aggregates stay in the gold pipeline (stream-stream joins unsupported in pipeline RTM; `transformWithState` in RTM has no event-time timers).

Consequences: breaks the serverless-only rule for one job (classic dedicated, always on — size it with Lakemeter); adds a Kafka consumer group and a second writer to operate; app and RLS unchanged (same Lakebase tables). Needs from the customer: confirmation that events pass through Kafka/MSK before bronze, topic/partition counts, and acceptance of classic compute for this path.

Next step to accept: probe in a dev workspace — synthetic Kafka topic → RTM job → Lakebase sink; measure p50/p95 event → Lakebase and $/h; test out-of-order handling.
