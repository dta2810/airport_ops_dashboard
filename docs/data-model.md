# Data model (contract v1.1)

Status: FROZEN for F1 (2026-10-16; v1.1 adds §7 access model). Simulator, gold logic, Lakebase DDL and app all build against this file; change it first, then the code. Column names in English, all timestamps UTC. Standards: A-CDM milestones, OOOI.

## 1. Source event (landing JSON → `bronze_flight_milestones`)

One JSON object per line, one milestone per event. Bronze adds `source_file`, `ingested_at`.

| Column | Type | Notes |
|---|---|---|
| `event_id` | STRING | UUID; unique per event (silver dedupes on it) |
| `event_type` | STRING | `SCHEDULED`, `GATE_ASSIGNED`, `BOARDING_START`, `OFF_BLOCK` (AOBT), `TAKEOFF` (ATOT), `LANDING` (ALDT), `IN_BLOCK` (AIBT), `DELAY_REPORTED`, `CANCELLED`, `DIVERTED` |
| `event_ts` | TIMESTAMP | When the milestone happened (simulated ops clock) |
| `emitted_ts` | TIMESTAMP | Wall clock when the emitter wrote it (freshness = Lakebase `written_at` − `emitted_ts`) |
| `event_date` | DATE | `to_date(event_ts)`; clustering + prune column |
| `airport_iata` | STRING | Airport where the milestone happens (origin for SCHEDULED…TAKEOFF, destination for LANDING/IN_BLOCK); clustering + prune column |
| `flight_id` | STRING | `AL<flight_number>-<yyyyMMdd of std_utc>-<origin_iata>`; deterministic |
| `flight_number` | STRING | e.g. `0123` |
| `origin_iata`, `destination_iata` | STRING | |
| `tail_number` | STRING | Aircraft registration |
| `std_utc`, `sta_utc` | TIMESTAMP | Scheduled off-block / in-block; repeated on every event |
| `gate_id` | STRING | Set on `GATE_ASSIGNED` (origin gate) and `IN_BLOCK` (destination gate); else null |
| `delay_cause` | STRING | On `DELAY_REPORTED`: `AIRCRAFT`, `CREW`, `PASSENGER`, `EXTERNAL`, `GROUND_OPS`; else null |
| `delay_minutes` | INT | On `DELAY_REPORTED`; else null |
| `diversion_iata` | STRING | On `DIVERTED`; else null |

Late, duplicate and out-of-order events are allowed; the latest `event_ts` per (`flight_id`, `event_type`) wins.

## 2. Dimensions (UC Delta, written once by the backfill)

| Table | Key | Columns |
|---|---|---|
| `dim_airport` | `airport_iata` | `name, city, country, tz, is_hub, lat, lon` |
| `dim_gate` | `airport_iata, gate_id` | `terminal` |
| `dim_aircraft` | `tail_number` | `aircraft_type, seats` |
| `dim_route` | `origin_iata, destination_iata` | `block_minutes, distance_km` |
| `dim_schedule` | `flight_id` | `flight_number, origin_iata, destination_iata, tail_number, std_utc, sta_utc` |
| `dim_connection` | `inbound_flight_id, outbound_flight_id` | `pax` — passengers connecting at the hub |

## 3. Silver (UC views, no CDF — mirrors the customer pattern)

- `silver_flight_milestones_v` — `SELECT DISTINCT` on bronze, latest event per (`flight_id`, `event_type`).
- `silver_flight_v` — one row per flight: `aobt, atot, aldt, aibt, boarding_start_ts, gate_dep, gate_arr, cancelled, diverted, delay_cause, delay_minutes, last_event_ts, last_emitted_ts`.

## 4. Gold (UC view `gold_<name>_v` → Delta replica `gold_<name>` + Lakebase `ops.<name>`)

Every gold row also carries `last_event_ts`, `last_emitted_ts` (max over contributing events), `batch_id` and `written_at`.

### `flight_state` — key `flight_id`

`flight_number, origin_iata, destination_iata, tail_number, std_utc, sta_utc, gate_dep, gate_arr, boarding_start_ts, aobt, atot, aldt, aibt, phase, dep_delay_min, arr_delay_min, delay_cause, taxi_out_min, taxi_in_min, turnaround_min, turnaround_ok`

- `phase`: first match of `CANCELLED`, `DIVERTED`, `AT_GATE` (aibt), `TAXI_IN` (aldt), `AIRBORNE` (atot), `TAXI_OUT` (aobt), `BOARDING` (boarding_start_ts), `SCHEDULED`.
- `dep_delay_min = aobt − std_utc`, `arr_delay_min = aibt − sta_utc` (minutes, null until actual).
- `taxi_out_min = atot − aobt`, `taxi_in_min = aibt − aldt`.
- `turnaround_min = aobt − aibt` of the previous flight of the same tail; `turnaround_ok = turnaround_min >= min_ground_time_min`.

### `airport_hour_kpis` — key `airport_iata, hour_start_utc`

Bucketed by scheduled hour (`std_utc` for departures at origin, `sta_utc` for arrivals at destination).

`dep_scheduled, departed, arr_scheduled, arrived, dep_otp_d0, dep_otp_d15, arr_otp_d0, arr_otp_d15, avg_taxi_out_min, avg_taxi_in_min, delay_minutes, cancellations, diversions`

- OTP D0 = share of completed movements with delay ≤ 0; D15 = delay ≤ `otp_threshold_min`. Range 0–1, null when no completed movements.

### `gate_hour_occupancy` — key `airport_iata, gate_id, hour_start_utc`

`occupied_minutes, flights`

- Occupancy interval per departing flight at `gate_dep`: from the inbound `aibt` of the same tail (or `std_utc − 60 min` if none) to `aobt` (or the current ops clock if not yet off-block), clipped to each hour.

### `hub_connections` — key `hub_iata, hour_start_utc` (outbound `std_utc` hour)

`connections, pax_connecting, connections_at_risk, pax_at_risk, connections_missed, pax_missed`

- Inbound ready time = `aibt`, else `aldt + 8 min`, else `sta_utc + dep_delay_min`, else `sta_utc`.
- Outbound departure = `aobt`, else `std_utc`.
- Missed: outbound departed before ready + `min_connection_time_min`. At risk: outbound not departed and ready + MCT > outbound departure.

## 5. Touched keys and prune predicates (per micro-batch)

Recompute only touched keys, always with **literal** `IN` lists (ADR 0002) on both the key and the prune columns `event_date`, `airport_iata`.

| Grain | Touched keys from the batch | Expansion |
|---|---|---|
| `flight_state` | `flight_id` of every event | + next flight of the same tail when `IN_BLOCK` arrives (turnaround) |
| `airport_hour_kpis` | (origin, hour(std)) and (destination, hour(sta)) of touched flights | — |
| `gate_hour_occupancy` | (origin, gate_dep, each hour of the occupancy interval) | old gate too if the gate changed |
| `hub_connections` | outbound std hour of every connection where a touched flight is inbound or outbound | — |

Prune: `event_date IN (std date, std date + 1)` and `airport_iata IN (origin, destination)` of the touched flights. A key that returns no row from the gold view is deleted from Lakebase and the replica.

## 6. Parameters (`config/demo.yaml`)

`min_ground_time_min`, `min_connection_time_min`, `otp_threshold_min`, hub and spokes, fleet size, simulator speed.

## 7. Access model (v1.1) — OBO, enforced in Lakebase

The app never reads as its own service principal. Each request runs as the signed-in user acting as one Databricks group: the app mints a group-scoped Lakebase credential with the user's forwarded token (`generate_database_credential(endpoint, group_name=<group>)`, scope `postgres`) and connects with `user=<group>`, so `current_user` is the group. The platform refuses the mint if the user is not a member.

### Roles (Databricks groups → Postgres group roles)

| Group | Airports | Sections | Priority |
|---|---|---|---|
| `aops-ops-control` | all (`*`) | overview, flights, airport_kpis, gates, delays, connections | 1 |
| `aops-hub-connections` | `PTY` | overview, flights, connections | 2 |
| `aops-station-PTY` | `PTY` | overview, flights, airport_kpis, gates, delays | 3 |
| `aops-station-BOG` | `BOG` | overview, flights, airport_kpis, gates, delays | 3 |

Group names, airports and sections are configuration (bundle variable + `ops.access_policy`), not code.

### Lakebase objects

- `ops.access_policy (pg_role TEXT PK, label TEXT, airports TEXT[], sections TEXT[], priority INT)` — one row per group; RLS: a role sees only its own row.
- Section → table privileges (`GRANT SELECT` per group): overview → `airport_hour_kpis`, `flight_state`; flights, delays → `flight_state`; airport_kpis → `airport_hour_kpis`; gates → `gate_hour_occupancy`; connections → `hub_connections`. `batch_log` (freshness) readable by every group. No grant = the section is unavailable even if the UI asked.
- Row-level security, `ENABLE` + `FORCE`, on every gold table:
  - `flight_state`: `origin_iata` or `destination_iata` in the role's airports (or `*`).
  - `airport_hour_kpis`, `gate_hour_occupancy`: `airport_iata` in the role's airports.
  - `hub_connections`: `hub_iata` in the role's airports.
- Pipeline SP: explicit `FOR ALL ... USING (true) WITH CHECK (true)` policy per table (it writes everything).
- Identities with `rolbypassrls` (e.g. `databricks_superuser`, workspace admins connecting as themselves) bypass RLS even under `FORCE`; the app refuses to serve data on such a connection.

### App behaviour

- `/api/roles`: tries the mint for each configured group in priority order and returns only the roles the platform accepted (label, airports, sections). One role → open it directly; several → role picker.
- Every data request carries the chosen role; the server checks it against the configured allow-list and connects as that group. The UI shows only the role's sections; the database enforces them at table level (section → table grants above), so sections sharing a table (`overview` and `delays` both read `flight_state`) are separated by the app only.
- Connections: small pool per (user, role), short idle timeout (membership is validated only at mint time), credential re-minted before expiry.
- Never `SET app.acting_user`-style context on `ops`: any group role could set it, so it is not a boundary there. The Genie chat schema `chat` uses `app.user_email` safely only because the app SP is the sole role that connects to it.

