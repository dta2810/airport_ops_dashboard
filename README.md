# Airport Ops Dashboard

Live airport operations dashboard for a fictitious hub-and-spoke airline (aeroLatinos, hub PTY, all-B737 fleet, synthetic data). Flight milestone events stream into a bronze streaming table; a serverless Lakeflow pipeline builds operational gold per micro-batch and serves it from Lakebase Postgres, with a governed Delta replica in Unity Catalog. A Databricks App refreshes every 30 seconds, and an embedded Genie panel answers questions over the same gold with the user's own permissions. Measured on the dev workspace: event → Lakebase p95 14–18 s for flight state, airport KPIs and gate occupancy.

```
simulator ─► landing volume ─► [bronze pipeline] bronze_flight_milestones (ST)
                                      │ readStream
                                      ▼
                     [gold pipeline, serverless, run-as SP]  foreach_batch_sink
                      touched keys → silver/gold UC views → ├─► Lakebase ops.* (serving)
                                                            └─► Delta gold replica (UC)
                                                                    │
                                              Databricks App ◄──────┤ (reads Lakebase every 30s, as the user)
                                               └ Ask Genie ─► Genie space ─► genie_* views + metric views (UC, filtered by group)
```

## Before you start

| Area | You need | Details |
|---|---|---|
| Laptop | Databricks CLI (≥ v1.20), Python ≥ 3.10 with `pyyaml`, Node.js ≥ 20 | [deployment.md §1](docs/deployment.md#1-prerequisites) |
| Workspace features | Serverless jobs and Lakeflow pipelines, Lakebase Autoscaling, Databricks Apps with user authorization (scopes `postgres`, `genie`), metric views, a SQL warehouse for Genie | Ask a workspace admin if a feature is disabled |
| Your permissions | `USE CATALOG` + `CREATE SCHEMA` on a catalog you can grant on; create a Lakebase project (targets `new`, `new_no_catalog`) or manage an existing one (target `reuse`); `CAN_USE` on the Genie warehouse | [Permissions table](docs/deployment.md#permissions-of-the-person-deploying) |
| Gold identity | Service principal (default, for production) or `--identity deployer` (no SP; gold runs as you — fine for demos and POCs) | [Gold identity](docs/deployment.md#gold-identity) |
| One-time identity setup (admin) | With the SP identity: a service principal with Workspace access for the gold pipeline (`deploy.py` can create it). Always: the four access groups from `config/demo.yaml` (`deploy.py` creates both when you are a workspace admin) | [deployment.md §4](docs/deployment.md#4-one-time-identity-setup-admin) |
| End users | Membership in one of the access groups and `CAN_USE` on the Genie warehouse; each user accepts the app's OAuth consent once | — |

Continuous pipelines and the app run until you stop them: `deploy.py start` begins a live demo, `deploy.py stop` ends it; stop the app with `databricks apps stop <name>` when idle ([runbook](docs/runbook.md#always-on-components)).

## Quickstart

From your laptop (Windows, macOS or Linux):

```bash
databricks auth login --host https://<workspace-url> --profile MYWS
python scripts/deploy.py all --target new --profile MYWS --var catalog=<catalog> --var genie_warehouse_id=<warehouse-id> --dry-run   # preview
python scripts/deploy.py all --target new --profile MYWS --var catalog=<catalog> --var genie_warehouse_id=<warehouse-id>   # add --add-me to test every role yourself
python scripts/deploy.py start --target new --profile MYWS    # live demo; `stop` when done
# no service principal (demo/POC): add --identity deployer to the `all` command

# optional classic gold consumer (workspaces that allow classic job clusters)
python scripts/deploy.py classic-setup --target new --profile MYWS
python scripts/deploy.py start-classic --target new --profile MYWS   # `stop-classic` (or `stop`) when done
```

`--var` values are saved per target (`.databricks/bundle/<target>/variable-overrides.json`), so `start`, `stop` and the classic commands after `all` need only `--target` and `--profile`.

### Azure and AWS

The bundle and the commands are the same on both clouds; nothing in the code is cloud-specific. Pick the target by what your metastore allows:

| Your workspace | Target | Notes |
|---|---|---|
| You can `CREATE CATALOG` on the metastore | `new` | Also creates a UC catalog over the Postgres database (browse Lakebase tables from UC; the app does not need it) |
| You cannot `CREATE CATALOG` (common in shared metastores) | `new_no_catalog` | Same as `new` without that catalog |
| You already have a Lakebase project | `reuse` | `--var lakebase_project_id=<project>` |

| | Azure | AWS |
|---|---|---|
| Verified from zero | Yes (2026-10-22, target `new`) | Yes (2026-10-27, us-east-1, target `new_no_catalog`) |
| Classic gold node (picked by `deploy`) | `Standard_D4ds_v5` | `m5d.xlarge` |
| App URL | `https://<app>-<workspace-id>.<n>.azure.databricksapps.com` | `https://<app>-<workspace-id>.aws.databricksapps.com` |
| Classic needs | Classic job clusters in standard access mode; `classic-setup` grants the SP `allow-cluster-create` | same |

Serverless gold, Lakebase, Apps and Genie need no cloud-specific settings. Prices differ by cloud and region: estimate with your workspace's `system.billing.list_prices`, and for classic add the VM at your cloud's list price.

See [docs/deployment.md](docs/deployment.md) for prerequisites, permissions, the step-by-step manual equivalent and troubleshooting.

## The app

Each user signs in with their own identity (on-behalf-of) and picks a role; Lakebase row-level security limits the data, and the navigation shows only the pages the role can use.

| Page | What it shows | Roles |
|---|---|---|
| Overview | Network KPI tiles, OTP and gate gauges, movements per hour, delay causes, flights needing attention, per-airport table. Click a delay cause or an hour to open Flights filtered by it; click an airport to focus the app on it | all |
| Flights | Live board by phase (cards highlight when a flight changes phase) or sortable list, with filters (status, cause, route, delay threshold, phase, hour, search) shown as removable chips and a summary line; "Copy link to this view" shares the filters in the URL; click a flight for its milestones, punctuality, aircraft/turnaround and gates | all |
| Rotations | One row per aircraft over the window: flights coloured by delay, short turnarounds in red, propagated delays (late departure right after a late arrival on the same tail) ringed; click a bar for the flight | all |
| Gates | Utilisation tiles, gate × hour heatmap, gate side panel (next departures, recent movements), all-gates status table | roles with gate data (hub) |
| Connections | Protected / at-risk / missed passengers by outbound hour, late inbound flights to the hub | hub, operations control |
| Ask Genie (side panel) | Questions in plain language; answer, chart (expand / PNG), result table (CSV export), SQL and follow-ups; history per user | all — Unity Catalog filters rows by the user's groups |

A time-window selector (last 6 / 12 / 24 h) and an airport selector in the top bar apply to every page and are kept in the URL. A shared link carries filters only; whoever opens it still sees only their own role's rows. Gate views appear only where gate data exists, so spoke stations never see an empty gate panel. Two apps can share one Lakebase database by giving each its own Genie history schema (`CHAT_SCHEMA`, default `chat`). Local development: `MOCK_MODE=true python app.py` in `src/app` serves fixture data with the same role filtering.

## Budget policy and tags

Set a budget (usage) policy per target in `databricks.yml` (`budget_policy_id` on the pipelines, the live job and the app; apps accept no tags). The gold pipeline runs as a service principal, which needs the **User** role on the policy, otherwise its effective policy stays empty. Resource tags (`project`, `cost_eval`, `component`) are set on the pipelines and the live simulator job.

## Docs

- [Requirements](docs/requirements.md)
- [Architecture and decisions](docs/architecture.md) · [ADRs](docs/adr/)
- [Recipe: reuse with your data](docs/recipe.md)
- [Data model and KPIs (contract v1)](docs/data-model.md)
- [Deployment](docs/deployment.md) · [Security](docs/security.md) · [Runbook](docs/runbook.md) · [Genie](docs/genie.md)

## Configuration

- `databricks.yml` — deployment identifiers (catalog, schema, Lakebase project/branch/database, pipeline SP, app name) per target: `new` (creates a Lakebase project), `new_no_catalog` (same, without the UC catalog over Postgres), `reuse` (existing project). Put your own workspace targets in a gitignored `<name>.local.yml` (template: `targets.example.yml`).
- `config/demo.yaml` — demo behaviour (network, fleet, simulator speed, refresh).
