# Deployment

You deploy from your own laptop (Windows, macOS or Linux) with one Python script that drives the Databricks CLI. Nothing runs from a CI server, and nothing needs bash or jq.

The bundle (`databricks.yml` + `resources/`) creates the Databricks resources. The script adds the steps a bundle cannot do: create the pipeline service principal, grant it Unity Catalog access before the pipeline exists, build the frontend, hold the continuous pipelines, run setup in the right order, and verify the app kept its OBO scope.

## 1. Prerequisites

### Tools on your laptop

| Tool | Version | Windows (PowerShell) | macOS |
|---|---|---|---|
| Databricks CLI | validated with v1.20.0 (needs Lakebase bundle resources) | `winget install Databricks.DatabricksCLI` | `brew tap databricks/tap && brew install databricks` |
| Python | ≥ 3.10, with `pyyaml` (`pip install pyyaml`) | `winget install Python.Python.3.12` | `brew install python@3.12` |
| Node.js + npm | ≥ 20 | `winget install OpenJS.NodeJS.LTS` | `brew install node@20` |

Sign in once per workspace: `databricks auth login --host https://<workspace-url> --profile <PROFILE>`.

### Workspace features

Serverless compute (jobs and Lakeflow pipelines), Lakebase Autoscaling, Databricks Apps, a SQL warehouse for Genie (serverless recommended), metric views, and Apps user authorization with the `postgres` scope allowed (workspace setting `allowedAppsUserApiScopes`, default all).

### Permissions of the person deploying

| Needed for | Permission | If you don't have it |
|---|---|---|
| `create-sp` | Workspace admin (create service principals) | An admin creates the SP (section 4) and you pass `--var pipeline_sp_app_id=<app id>` |
| `create-sp` | *Service principal: User* on that SP (the creator gets *Manager* only) | Ask the SP manager to add you as User; keep Manager if you have it (changing your own role in the UI replaces it) |
| `pre-grants`, `setup` | `USE CATALOG` + `CREATE SCHEMA` on the target catalog, and the right to grant `USE CATALOG` on it (catalog owner or `MANAGE`) | Ask the catalog owner, or use your personal catalog |
| `deploy` (target `new`) | Create Lakebase projects | Use target `reuse` with an existing project |
| `deploy` (target `reuse`) | `CAN_MANAGE` on the Lakebase project, and Postgres owner/`databricks_superuser` on its branch (setup runs DDL as you) | Ask the project owner |
| `deploy` (Lakebase UC catalog) | `CREATE CATALOG` on the metastore | Use target `new_no_catalog` (fresh workspace) or `reuse`: `postgres_catalogs` is declared only in target `new`. It only adds UC browsing of the Postgres tables; the app does not need it |
| Access groups | Workspace admin, for step `groups` to create them; existing groups with the names in `config/demo.yaml` are reused | A workspace admin creates them (section 4), then resume with `--from groups` |
| `genie-objects`, Genie space | `CAN_USE` on the Genie warehouse; right to grant `USE CATALOG`/`USE SCHEMA`/`SELECT` to `genie_reader` (default `account users`) | Pass another `--var genie_reader=<principal>` |

## 2. Configure

1. Pick a target in `databricks.yml`:
   - `new` creates a Lakebase project named `lakebase_project_id`.
   - `new_no_catalog` is `new` without the UC catalog over Postgres, for metastores where you cannot `CREATE CATALOG`.
   - `reuse` uses an existing project/branch.
   - Or copy `targets.example.yml` to `<name>.local.yml` (gitignored, picked up by the bundle) and define your own target there.
2. Set at least the catalog and the Genie warehouse: `--var catalog=<catalog> --var genie_warehouse_id=<warehouse id>`. Every other variable has a default: schema, app name, Lakebase ids, SP name, access groups. List them with `databricks bundle validate -t <target> -p <PROFILE> -o json`.
3. Adjust demo behaviour and the access model in `config/demo.yaml`: hub/spokes, KPI thresholds, and the `access:` roles. Group names in `access.roles` must match the bundle variable `access_groups` and the workspace groups.

## 3. Deploy

```powershell
# Windows PowerShell (same command on macOS/Linux with python3)
python scripts/deploy.py all --target new --profile MYWS --var catalog=my_catalog
```

Preview first: `--dry-run` prints every command that would change something, and only runs read-only calls.

### Gold identity

The gold pipeline and the classic gold job run as one identity, chosen per target with `--identity` (sticky: later runs on the same target keep it):

| `--identity` | Gold runs as | What it needs | Use it for |
|---|---|---|---|
| `service_principal` (default) | Service principal `pipeline_sp_name` | `create-sp` (or an admin, section 4), *Service principal: User* for you, `allow-cluster-create` on the SP for classic | Production: the pipeline does not depend on a person |
| `deployer` | You (the signed-in user) | Nothing extra; for classic, you need permission to create clusters | Demos and POCs without an SP. The pipeline and job stop working if your account loses access |

`--identity deployer` skips `create-sp` and the SP grants in `pre-grants`, and writes the derived bundle variables (`gold_principal`, `gold_run_as`, `bundle_permissions`) to `.databricks/bundle/<target>/variable-overrides.json`, so plain `databricks bundle` commands use the same identity. Equivalent target snippet, if you prefer it in `databricks.yml`:

```yaml
  my_target:
    variables:
      gold_identity: deployer
      gold_principal: ${workspace.current_user.userName}
      gold_run_as: {user_name: "${workspace.current_user.userName}"}
      bundle_permissions:
        - {level: CAN_MANAGE, user_name: "${workspace.current_user.userName}"}
```

The Lakebase database is owned by the gold identity's Postgres role. Step `lakebase-role` finds that role on the branch (by `postgres_role` = SP application id or your email) or creates it, and records its path as `lakebase_owner_role`; on target `new` it first creates the Lakebase project when it does not exist (`lakebase_create_project: "true"`). The role and the project are not bundle resources, so `destroy` never deletes them. Plain `databricks bundle deploy` needs `--var lakebase_owner_role=<role path>` unless `deploy.py` has written it.

Lakebase sizing: when `deploy.py` creates the project (target `new`) it sets the gold endpoint to `lakebase_min_cu`–`lakebase_max_cu` (default 0.5–4 CU) and auto-suspend after `lakebase_suspend` idle (default `300s`); without this the platform default is 1–1 CU and suspend after 24 h. Projects you reuse are not touched; apply the same settings to them with `python scripts/deploy.py lakebase-size -t <target> -p <profile> [--var lakebase_max_cu=8]`.

**Upgrading an existing deployment** (bundle versions that declared `postgres_roles.pipeline_sp`, and `postgres_projects` on target `new`): the next deploy would delete those resources from the workspace. Before deploying this version, run from the previous version's checkout: `databricks bundle deployment unbind pipeline_sp -t <target> -p <profile>` (and `unbind airport_ops_project` on target `new`). The role keeps working and `lakebase-role` picks it up by principal.

| Step | What it does | Manual equivalent |
|---|---|---|
| `preflight` | Checks tools, sign-in, required variables | `databricks current-user me -p MYWS` |
| `create-sp` | Creates `pipeline_sp_name` with *Workspace access* (or reuses it by name) and gives you *Service principal: User*; skipped with `--identity deployer` | Section 4 |
| `groups` | Creates the access groups from `config/demo.yaml` that are missing (workspace-local) You are not added by default (membership in every group means the union of all roles, also in Genie); `--add-me` adds you to each one so you can switch roles in a demo. Needs workspace admin; otherwise it stops with the command for an admin | Section 4 |
| `pre-grants` | Creates the schema; grants the SP `USE CATALOG` and `USE SCHEMA, CREATE TABLE, SELECT` (skipped with `--identity deployer`) | `databricks grants update catalog <cat> --json '{"changes":[{"principal":"<sp>","add":["USE_CATALOG"]}]}' -p MYWS` |
| `lakebase-role` | Finds or creates the gold identity's Lakebase role (and the project on target `new`); sets `lakebase_owner_role` | `databricks postgres list-roles projects/<p>/branches/<b> -p MYWS`; `databricks postgres create-role ...` |
| `genie-objects` | Creates the Delta replicas, the Genie views (`genie_*`, filtered by group) and metric views, and grants `genie_reader` read access. Runs before `deploy` because the Genie space is rejected if its tables do not exist | SQL from `src/setup/genie_access.py` on the warehouse |
| `build` | `npm ci && npm run build` in `src/app/frontend` (the bundle ships `dist/`) | same commands |
| `deploy` | `bundle validate` + `bundle deploy` | `databricks bundle deploy -t new -p MYWS --var catalog=...` |
| `hold` | Stops both continuous pipelines (they start on create) | `databricks pipelines stop <id> -p MYWS` |
| `setup` | Job `setup`: UC schema/volume/grants, Lakebase DDL, group roles, access policy, RLS | `databricks bundle run setup ...` |
| `backfill` | Job `simulator_backfill`: dimensions + 7 days of events | `databricks bundle run simulator_backfill ...` |
| `bronze-init` | Starts bronze and waits for `bronze_flight_milestones` to exist | `databricks bundle run bronze --no-wait ...` |
| `gold-setup` | Job `gold_setup`: views, Delta replicas, `gold_params`, Genie views (re-applied from `config/demo.yaml`); stops the pipelines again | `databricks bundle run gold_setup ...` |
| `app` | Starts the app | `databricks bundle run dashboard ...` |
| `verify` | Fails unless the app is RUNNING and `postgres` is in `effective_user_api_scopes` | `databricks apps get <app> -o json -p MYWS` |

A failed run resumes from the failing step: `python scripts/deploy.py all ... --from setup`. Every step is idempotent.

## 4. One-time identity setup (admin)

With `--identity deployer` no service principal is needed; only the groups below.

Step `groups` does this for you when you are a workspace admin. Run these when you are not, or to prepare groups in advance.

`scripts/identity/` holds the request bodies, so the commands are the same in PowerShell, bash and zsh:

```powershell
# pipeline service principal with Workspace access
databricks service-principals create --json @scripts/identity/pipeline_sp.json -p MYWS
# access groups (names must match config/demo.yaml access.roles)
databricks groups create --display-name aops-ops-control -p MYWS
databricks groups create --display-name aops-hub-connections -p MYWS
databricks groups create --display-name aops-station-PTY -p MYWS
databricks groups create --display-name aops-station-BOG -p MYWS
```

Then add users to the groups (UI: Settings → Identity and access → Groups). Give the deploying user *Service principal: User* on the SP (UI: the SP → Permissions). Workspace access takes 3–5 minutes to propagate before the pipeline can run as the SP.

## 5. Run the demo

```powershell
python scripts/deploy.py start --target new --profile MYWS   # bronze + gold continuous, live emitter
python scripts/deploy.py stop  --target new --profile MYWS   # always stop when done: stops continuous pipelines, cancels simulator_live runs
```

The live emitter (job `simulator_live`) opens its window at `live_start_hhmm` (default `05:30`, simulated UTC) on the UTC day of the last landed event, so there is no empty day after the backfill and the first bank departs within about a wall minute (`time_scale: 60`). If that time is already past, or a tail is still flying, it starts one minute after the last event or the last arrival plus minimum ground time. Live flights take flight numbers above the highest one in bronze, so flight_ids never collide. Change the start per run with `databricks bundle run simulator_live --notebook-params live_start_hhmm=HH:MM`, or permanently in `resources/jobs.yml`.

Open the app URL printed by `verify`. On the first visit each user accepts the OAuth consent; the app then lists only the roles the user's groups allow.

## Classic gold consumer (optional)

An optional second gold consumer runs on classic compute, for workspaces that allow classic job clusters, on the same bronze as the serverless pipeline. It is a continuous job (`gold_classic`) running `src/pipelines/gold/gold_stream_classic.py`: Structured Streaming `foreachBatch` calling the same `gold_core.process_batch` and `LakebaseWriter` as `gold_sink.py`. It writes separate targets, so the app and the serverless gold are untouched:

| | Serverless (`gold`) | Classic (`gold_classic`) |
|---|---|---|
| Delta replicas | `{catalog}.{schema}.gold_*` | `{catalog}.airport_ops_classic.gold_*` (`gold_classic_schema`) |
| Lakebase | schema `ops` | schema `ops_classic` (`gold_classic_lakebase_schema`), same database |
| Idempotency | `ops.batch_control` | `ops_classic.batch_control` |
| Checkpoint | managed by the pipeline | `/Volumes/{catalog}/airport_ops_classic/chk/gold_classic` |

Both jobs are declared for every target and deploy PAUSED, so a deploy creates them but nothing runs or bills until `start-classic`. Nothing is workspace-specific: catalog, schemas, Lakebase endpoint and SP come from the target's variables. `deploy` picks the node type from the workspace's own node list (preferred `Standard_D4ds_v5` on Azure, `m5d.xlarge` on AWS, `n2-standard-4` on GCP, else the smallest 4-core node with ≥ 14 GB); override with `--var gold_classic_node_type=<node type>`. Photon is on by default (`gold_classic_runtime_engine: PHOTON`; `--var gold_classic_runtime_engine=STANDARD` to turn it off). Runtime defaults to **DBR 18 LTS** (`gold_classic_spark_version: 18.x-scala2.13`, Spark 4.1). Recommendation: keep the classic cluster on the latest LTS with Photon. The 2026-10 measurements ran on 17.3 LTS; to pin it back use `--var gold_classic_spark_version=17.3.x-scala2.13`. Check the key with `databricks clusters spark-versions` in the target workspace. Photon shortens each micro-batch; more cores do not. The workspace must allow classic job clusters in standard access mode; serverless-only workspaces can deploy the job but not run it.

The job runs as the gold identity on a classic job cluster. With the service principal identity, the SP needs the `allow-cluster-create` entitlement ("Allow unrestricted cluster creation"); the serverless pipeline never needs it. Step `classic-setup` adds it (only then) and `destroy` removes it; without it every run fails with `PERMISSION_DENIED: You are not authorized to create clusters`.

Run it after the main deploy (`setup`, `gold-setup` done):

```powershell
python scripts/deploy.py classic-setup --target <target> --profile <profile>   # job gold_classic_setup: UC schema, gold_* replicas, chk volume, Lakebase ops_classic DDL, SP grants
python scripts/deploy.py start-classic --target <target> --profile <profile>   # unpause the continuous job (single-node cluster starts, bills while running)
python scripts/deploy.py stop-classic  --target <target> --profile <profile>   # pause it and cancel the active run
```

`deploy.py stop` also pauses `gold_classic` and cancels active `simulator_live` runs. `bundle deploy` re-applies `pause_status: PAUSED`, so a redeploy stops a running classic job. Check its freshness in `ops_classic.batch_log` (`written_at - max_event_ts`).

The cluster uses standard access mode (`data_security_mode: USER_ISOLATION`), not dedicated: bronze is a streaming table owned by the bronze pipeline, and dedicated access mode supports only batch reads of streaming tables. On standard access mode Python `foreachBatch` runs in a separate process, so the batch function imports the gold modules and builds the `WorkspaceClient` lazily inside itself and never touches `dbutils` or notebook state. The job runs as the gold identity; `classic-setup` grants it `USE SCHEMA, CREATE TABLE, SELECT, MODIFY` on the replica schema, `READ/WRITE VOLUME` on `chk`, and `USAGE, CREATE` + DML on `ops_classic`. Reset the classic consumer by pausing the job, then dropping the checkpoint directory together with the `ops_classic.batch_control` rows (they must restart together).

## 6. Tear down

`python scripts/deploy.py destroy --target new --profile MYWS` stops the pipelines and runs `bundle destroy` after you type the target name. It leaves in place on purpose: the pipeline SP, the workspace groups, the catalog grant, and the Lakebase project and roles (not bundle resources).

## Troubleshooting (errors seen on real deploys)

| Error | Cause | Fix |
|---|---|---|
| `Field database_id must match pattern ^[a-z]([a-z0-9-]...` | Lakebase database ids allow hyphens, not underscores | Keep `lakebase_database_id` hyphenated; the Postgres name `lakebase_database` may use `_` |
| `Principal '<sp>' does not have USE CATALOG` when creating the gold pipeline | The pipeline runs as the SP, checked at create time | Step `pre-grants` (run it before `deploy`) |
| `Database ... does not exist` on the app or catalog | Created before the database | Resources reference `${resources.postgres_databases...}`; redeploy |
| `TABLE_OR_VIEW_NOT_FOUND ... bronze_flight_milestones` in `gold_setup` | Bronze has not run yet | Step `bronze-init` |
| App `CRASHED`, log `no pq wrapper available ... libpq library not found` | The Apps runtime has no system libpq | `psycopg[binary]` in `src/app/requirements.txt` (plain `psycopg` is for serverless notebooks) |
| App stuck on "Checking your access…" (~2 min) then empty roles; log `generate_database_credential() got an unexpected keyword argument 'group_name'` | `group_name` exists in `databricks-sdk==0.120.0` but was removed in later SDKs; a `>=` floor pulls a newer one | Pin `databricks-sdk==0.120.0` in `src/app/requirements.txt`; `tests/test_sdk_contract.py` checks the real signature |
| Pipelines `RUNNING` right after deploy | Continuous pipelines start on create | Step `hold`; `deploy.py stop` |
| App 403 or empty role list in the browser | Consent not given, or the OAuth integration lacks `postgres` | Re-open in a private window; check `effective_user_api_scopes`; an account admin can add the scope to the app's custom app integration |
| `Failed to fetch tables for the agent ... does not exist` on the Genie space | The space was deployed before its views | Step `genie-objects`, then `deploy` |
| `Invalid id for text_instruction.id: '1'` | YAML read an unquoted id such as `0b…01` as a binary number | Quote every `id` in `resources/genie.yml` |
| `gold_classic` runs fail: `PERMISSION_DENIED: You are not authorized to create clusters` | The run-as SP lacks `allow-cluster-create` (classic job cluster) | `deploy.py classic-setup` (or Settings → Service principals → Allow unrestricted cluster creation); allow a few minutes to propagate |
| A user sees every row | They connect as a `databricks_superuser`/`rolbypassrls` identity | The app refuses such connections (503); use group roles, never superuser |
