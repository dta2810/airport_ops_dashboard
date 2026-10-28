# Recipe: streaming gold served from Lakebase

How this repo was built, as steps you can repeat with your own data. Each step lists what to change for your context and how you know it is done. Steps 5–7 can run in parallel once step 2 is frozen.

## 1. Pin the constraints and pick the mechanism

- Write down what you cannot change: source owner, whether silver has CDF, compute policy (serverless only?), freshness target, key shape.
- Probe the candidate mechanisms before building (this repo: `foreach_batch_sink` vs synced tables vs native sink; see [architecture](architecture.md) and [ADRs](adr/)).
- Record compute alternatives you parked and why (e.g. classic single-node job cluster: restart gap and operations).
- **Your context:** replace the constraint list and re-run the probes that your constraints touch.
- **Done when:** an ADR states the mechanism with a measured freshness number.

## 2. Freeze the data contract

- One file ([data-model.md](data-model.md)) defines source event, dimensions, silver, gold grains with keys, KPI formulas, touched-key rules and prune predicates.
- Every gold row carries a source wall-clock timestamp so freshness is measurable end to end.
- Prune columns (`event_date`, location) live on the fact and are its clustering keys; recompute filters use literal `IN` lists.
- **Your context:** swap the event schema and gold grains for yours; keep the touched-key and prune sections.
- **Done when:** simulator, gold SQL, Lakebase DDL and app can each be written from the file alone.

## 3. Parametrize the bundle

- Deployment identifiers in `databricks.yml` variables (catalog, schema, warehouse, Lakebase project/branch/database, app name, SP name/app id); demo behaviour in `config/demo.yaml`.
- Targets: `new` (creates Lakebase project), `reuse` (existing), one dev target.
- **Your context:** set the target variables; nothing else in the repo names your workspace.
- **Done when:** `databricks bundle validate -t <target>` resolves every variable.

## 4. Create the pipeline identity

- `python scripts/deploy.py create-sp -t <target> -p <profile>` creates the run-as SP with `workspace-access` and gives you the User role on it (or an admin does it; see deployment.md section 4).
- Wait 3–5 minutes for the entitlement to propagate before the first pipeline run.
- **Done when:** the script prints an app id and the SP shows Workspace access.

## 5. Source or simulator

- Simulator (`src/simulator/`) writes contract-shaped JSON into the landing volume: backfill plus a live emitter on an accelerated clock.
- **Your context:** point bronze at your real source and skip the simulator, or adapt its generators.
- Late events keep their true `event_ts` and only arrive later (`emitted_ts`, later file); shifting `event_ts` fabricates delays once "latest event wins" applies.
- Seed a dedicated RNG (event ids included) and pass an explicit start date, so a run is reproducible.
- Notebooks get `config_path` as a job parameter (`${workspace.file_path}/config/demo.yaml`); `__file__` does not exist in notebooks.
- **Done when:** bronze has rows for every event type, late/duplicate events exist, and resolving "latest event per milestone" never breaks OFF_BLOCK < TAKEOFF < LANDING < IN_BLOCK.

## 6. Gold logic

- UC silver and gold views implement the contract formulas; the `foreach_batch_sink` computes touched keys, recomputes them from the views, MERGEs the Delta replica and upserts/deletes Lakebase in one transaction with `batch_id` idempotency. A batch with the stored `batch_id` is a replay and is skipped. A lower id means the checkpoint was reset (full refresh, new checkpoint path), so it is written: the writes are recomputed state, safe to apply again.
- **Your context:** rewrite the views; keep the sink and `lakebase_writer.py` as they are.
- One module renders every gold query; views use prune `TRUE`, the sink injects a literal `event_date IN (...)` on the bronze scan. Same text, so views and sink cannot drift.
- Prune on dates only unless every KPI input lives at the key's location (here cancellations sit at the origin and diversions at the destination, so an airport prune would undercount).
- Expansions must follow the contract literally: "next flight of the same tail" comes from the schedule, not from a fixed time window (long ground times break windows).
- Test against an independent implementation (plain-Python oracle over the same events), plus pruned == full and incremental batches + replay == full. Ties in ordering keys make results engine-dependent: make the source data valid (no overlapping rotations) rather than adding tie-breakers.
- No local JDK? Databricks Connect on serverless runs the same SQL from a laptop against a scratch schema.
- **Done when:** replica and Lakebase match row for row and a replayed batch is skipped.

## 7. App

- Pre-app gate: check prior art, align `user_api_scopes` with the bundle, verify scopes survive a deploy.
- The app reads Lakebase only, refreshes on an interval and shows how fresh the data is.
- **Done when:** the app renders from Lakebase and passes the security review.

## 8. Deploy and verify end to end

- `python scripts/deploy.py all -t <target> -p <profile>`, then `deploy.py start` (bronze + gold + live emitter) and open the app. The script is the bundle plus the steps a bundle cannot do; each step has a CLI equivalent in deployment.md.
- Order matters: UC grants for the run-as SP before the pipeline exists; bronze's first run before the gold views; stop continuous pipelines right after deploy (they start on create).
- Measure event → Lakebase freshness from `ops.batch_log`.
- **Done when:** p95 freshness meets the target over a 30-minute run.

## 9. Tear down

- `python scripts/deploy.py destroy -t <target> -p <profile>`; continuous pipelines run until stopped.
