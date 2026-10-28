# Security

## Identities

| Identity | Used for | Access |
|---|---|---|
| Gold identity: pipeline SP (`pipeline_sp_name`), or the deployer with `--identity deployer` | Runs the gold pipeline (and the classic gold job), writes replicas + Lakebase; owns the gold Lakebase database | UC: USE CATALOG, USE SCHEMA, CREATE TABLE, SELECT on the project schema, MODIFY on the gold replicas. Lakebase: CONNECT, USAGE + DML on schema `ops`, explicit `pipeline_all` RLS policy |
| Signed-in user (OBO) | Every dashboard and Genie request | Lakebase: credentials minted with `group_name` for one `aops-*` group role; RLS + section grants apply. Genie: the user's forwarded token (scope `genie`); the `genie_*` views filter by the user's groups |
| App SP | Genie chat history only | Lakebase: `CAN_CONNECT_AND_CREATE` (app `postgres` resource); owns schema `chat`, FORCE RLS on `app.user_email`. No access to schema `ops` |

No PATs or passwords. Lakebase credentials are OAuth tokens minted with `POST /api/2.0/postgres/credentials`.

The narrow grants above are what the setup adds. With `--identity deployer` the gold pipeline and classic job run with the deployer's full rights instead: everything the person can do in UC, and on Lakebase the deployer's own role, which as project owner or `databricks_superuser` usually bypasses RLS (so `pipeline_all` changes nothing). Readers are unaffected: the app never connects as the gold identity. Use deployer mode for demos and POCs; use the SP for anything long-lived.

## Controls

- Dashboard data: Postgres RLS per group role (`ops.access_policy`), section → table grants, connections refused when the identity bypasses RLS (superuser / `rolbypassrls`).
- Genie: UC dynamic views with `is_member()` (workspace-local groups) and metric views on top; `genie_reader` gets SELECT, rows are filtered in the view. With account groups, switch to `is_account_group_member()` and grant the groups directly.
- Chat history: only the app SP connects to schema `chat`; `app.user_email` is set per transaction from the signed-in user. This session variable is a boundary only because no user-controlled role can connect to `chat` (see data-model §7 for why `app.*` context is not used on `ops`).
- Static files: the SPA route serves only files inside `frontend/dist` (resolved path containment, regression test `tests/test_static_paths.py`).
- Inputs: conversation/message ids `^[0-9a-f]{32}$`, airports `^[A-Z]{3}$`, roles against the allow-list, questions ≤ 1000 characters; all app SQL is parameterized.
- CSV export neutralises spreadsheet formulas.

## Known limits

- `is_member()` matches workspace-local group names in whichever workspace runs the query: bind the catalog to this workspace, or use account groups.
- The `overview` section grants `flight_state` and `airport_hour_kpis`, so a role with `overview` can query hourly KPIs and delays through Genie even if the app hides those sections.
- No per-user rate limit on Genie questions.

## Review log

- 2026-10-19: review of master `ecd1c64`. Critical SPA path traversal fixed in `7dad614`; remaining findings tracked in the task notes.
- Final gate before sharing: re-run the security and best-practices review.
