# Genie space

A Genie space lets operations users ask questions in plain language ("Which flights are more than 15 minutes late and still on the ground?"). It reads the same gold as the app, through UC objects that apply the access model of `config/demo.yaml` (contract §7).

## What gets deployed

| Object | Purpose | Source |
|---|---|---|
| `genie_flight_state`, `genie_airport_hour_kpis`, `genie_gate_hour_occupancy`, `genie_hub_connections` | Dynamic views over the `gold_*` Delta replicas: rows limited to the reader's airports, tables limited to the reader's sections, technical columns hidden, column comments for Genie | `src/setup/genie_access.py` |
| `genie_airport_metrics` | Metric view: movements, OTP D0/D15 (weighted by completed movements), taxi-out, delay minutes, cancellations, diversions by airport / hour / date | same |
| `genie_connection_metrics` | Metric view: connections, at risk, missed, success rate by hub / hour / date | same |
| Genie space `aeroLatinos Airport Operations` | Six data sources, instructions, 4 sample questions, 5 example SQLs; `CAN_RUN` for the four `aops-*` groups | `resources/genie.yml` |

## Access model

The app enforces access in Lakebase (RLS per group role). Genie reads Unity Catalog, so the same rules are re-expressed in the views:

- A row is visible when the reader belongs to a group whose sections include that table and whose airports match the row (`origin_iata`/`destination_iata`, `airport_iata` or `hub_iata`); `*` means every airport.
- Station BOG sees BOG flights and KPIs, no hub connections. Hub Connections sees PTY flights and connections, no gates. Operations Control sees everything. A user in no group sees nothing.
- Metric views read the filtered views, so KPIs only aggregate visible rows.
- `CAN_RUN` on the space is not a data grant: anyone given `SELECT` on the views (`genie_reader`, default `account users`) still only gets their group's rows.

Why views and not UC row filters on the replicas: the demo groups are workspace-local (created with `databricks groups create`), and UC grants or `is_account_group_member()` only work with account groups. The views use `is_member()`, which evaluates workspace groups for the querying user. With account groups in production, swap the template to `is_account_group_member({})` in `view_statements` and grant `SELECT` to the groups directly. Row filters on the replicas would also filter the pipeline SP's MERGE reads unless the filter function exempts it; views keep the writer path untouched.

Change the access model in `config/demo.yaml`, then re-run `python scripts/deploy.py genie-objects ...` (or the `gold_setup` job).

## Deploy

Set the warehouse: `--var genie_warehouse_id=<id>` (or set it in your `<name>.local.yml` target). `deploy.py all` runs `genie-objects` before `deploy`, because the Genie API rejects a space whose tables do not exist yet. Genie only costs warehouse time when someone asks a question.

## Validation

| Check | Where | Result (dev workspace, 2026-10-19) |
|---|---|---|
| Rows + sections per role (Ops Control, Hub, PTY, BOG, BOG+Hub, no group) match a Python oracle from `config/demo.yaml`, membership simulated | `tests/it_genie.py` (scratch schema, live replicas) | pass |
| Real `is_member()` in the views (deployer is in every group) returns all rows | same | pass |
| Metric view departures and OTP D15 per airport equal the flight-level computation | same | pass |
| The 5 example SQLs in `resources/genie.yml` run and return rows; ids and identifiers sorted | same | pass |
| Generated SQL per role, quoting, every object covered | `tests/test_genie_access.py` | pass |
| Genie API accepts the `serialized_space`; a conversation answered the OTP question with SQL on `genie_airport_metrics` | scratch space, deleted afterwards | pass |

Not yet tested: a real user who belongs to one group only (the deployer is in all four). Do it with a test user before sharing.

## In the app: Ask Genie panel

The **Ask Genie** button in the top bar opens a side panel that pushes the dashboard to the left and stays open across pages. It shows sample questions, the answer text, the result table (first 50 rows), the SQL behind it, and Genie's follow-up suggestions. **History** lists your previous conversations; opening one continues it in Genie with the same `conversation_id`.

| Piece | Where | Notes |
|---|---|---|
| API `/api/genie/{config, ask, messages/{cid}/{mid}, conversations, conversations/{cid}}` | `src/app/server/genie.py` | Calls Genie with the user's forwarded token (scope `genie`), so the `genie_*` views filter by the user's groups. The app service principal would see nothing |
| History store | `src/app/server/chat_store.py`, Lakebase schema `chat` (`conversations`, `messages`) | Owned by the app service principal (its role from the app's `postgres` resource). FORCE RLS: every row is visible only when `app.user_email` matches; the app sets it per transaction from `x-forwarded-email`. Created on first use |
| Panel | `src/app/frontend/src/components/GeniePanel.tsx` | Polls every 2 s up to 3 min; markdown limited to bold and bullets, rendered without HTML injection |

Unity Catalog enforces access on every Genie query by the user's real group memberships (the role picker in the app is a UI filter only). Users need `CAN_USE` on the Genie warehouse (the deployer has it; grant it to the `aops-*` groups for other users). Adding the `genie` scope re-prompts each user for OAuth consent once.
