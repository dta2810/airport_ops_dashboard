-- Serving gold in Lakebase. Composite PKs = gold grain.
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.batch_control (sink TEXT PRIMARY KEY, last_batch_id BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), token_minted_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS ops.batch_log (sink TEXT, batch_id BIGINT, n_keys INT, n_rows INT, n_deleted INT, batch_started_at TIMESTAMPTZ, max_event_ts TIMESTAMPTZ, written_at TIMESTAMPTZ NOT NULL DEFAULT now(), writer TEXT DEFAULT current_user, PRIMARY KEY (sink, batch_id));
CREATE TABLE IF NOT EXISTS ops.token_log (minted_at TIMESTAMPTZ PRIMARY KEY DEFAULT now(), batch_id BIGINT, writer TEXT DEFAULT current_user);

CREATE TABLE IF NOT EXISTS ops.flight_state (
    flight_id TEXT PRIMARY KEY,
    flight_number TEXT, origin_iata TEXT, destination_iata TEXT, tail_number TEXT,
    std_utc TIMESTAMPTZ, sta_utc TIMESTAMPTZ,
    gate_dep TEXT, gate_arr TEXT, boarding_start_ts TIMESTAMPTZ,
    aobt TIMESTAMPTZ, atot TIMESTAMPTZ, aldt TIMESTAMPTZ, aibt TIMESTAMPTZ,
    phase TEXT,
    dep_delay_min INT, arr_delay_min INT, delay_cause TEXT,
    taxi_out_min INT, taxi_in_min INT,
    turnaround_min INT, turnaround_ok BOOLEAN,
    batch_id BIGINT, last_event_ts TIMESTAMPTZ, last_emitted_ts TIMESTAMPTZ,
    written_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.airport_hour_kpis (
    airport_iata TEXT, hour_start_utc TIMESTAMPTZ,
    PRIMARY KEY (airport_iata, hour_start_utc),
    dep_scheduled INT, departed INT, arr_scheduled INT, arrived INT,
    dep_otp_d0 DOUBLE PRECISION, dep_otp_d15 DOUBLE PRECISION,
    arr_otp_d0 DOUBLE PRECISION, arr_otp_d15 DOUBLE PRECISION,
    avg_taxi_out_min DOUBLE PRECISION, avg_taxi_in_min DOUBLE PRECISION,
    delay_minutes INT, cancellations INT, diversions INT,
    batch_id BIGINT, last_event_ts TIMESTAMPTZ, last_emitted_ts TIMESTAMPTZ,
    written_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.gate_hour_occupancy (
    airport_iata TEXT, gate_id TEXT, hour_start_utc TIMESTAMPTZ,
    PRIMARY KEY (airport_iata, gate_id, hour_start_utc),
    occupied_minutes INT, flights INT,
    batch_id BIGINT, last_event_ts TIMESTAMPTZ, last_emitted_ts TIMESTAMPTZ,
    written_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.hub_connections (
    hub_iata TEXT, hour_start_utc TIMESTAMPTZ,
    PRIMARY KEY (hub_iata, hour_start_utc),
    connections INT, pax_connecting INT,
    connections_at_risk INT, pax_at_risk INT,
    connections_missed INT, pax_missed INT,
    batch_id BIGINT, last_event_ts TIMESTAMPTZ, last_emitted_ts TIMESTAMPTZ,
    written_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
