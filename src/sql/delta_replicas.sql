-- Delta replicas of gold tables in Unity Catalog. Clustered on keys for efficient prune.
-- Replicated via MERGE inside foreach_batch_sink, idempotent per batch_id.

CREATE TABLE IF NOT EXISTS {schema}.gold_flight_state (
    flight_id STRING,
    flight_number STRING, origin_iata STRING, destination_iata STRING, tail_number STRING,
    std_utc TIMESTAMP, sta_utc TIMESTAMP,
    gate_dep STRING, gate_arr STRING, boarding_start_ts TIMESTAMP,
    aobt TIMESTAMP, atot TIMESTAMP, aldt TIMESTAMP, aibt TIMESTAMP,
    phase STRING,
    dep_delay_min INT, arr_delay_min INT, delay_cause STRING,
    taxi_out_min INT, taxi_in_min INT,
    turnaround_min INT, turnaround_ok BOOLEAN,
    batch_id BIGINT, last_event_ts TIMESTAMP, last_emitted_ts TIMESTAMP,
    written_at TIMESTAMP,
    PRIMARY KEY (flight_id)
)
CLUSTER BY (flight_id);

CREATE TABLE IF NOT EXISTS {schema}.gold_airport_hour_kpis (
    airport_iata STRING, hour_start_utc TIMESTAMP,
    dep_scheduled INT, departed INT, arr_scheduled INT, arrived INT,
    dep_otp_d0 DOUBLE, dep_otp_d15 DOUBLE,
    arr_otp_d0 DOUBLE, arr_otp_d15 DOUBLE,
    avg_taxi_out_min DOUBLE, avg_taxi_in_min DOUBLE,
    delay_minutes INT, cancellations INT, diversions INT,
    batch_id BIGINT, last_event_ts TIMESTAMP, last_emitted_ts TIMESTAMP,
    written_at TIMESTAMP,
    PRIMARY KEY (airport_iata, hour_start_utc)
)
CLUSTER BY (airport_iata, hour_start_utc);

CREATE TABLE IF NOT EXISTS {schema}.gold_gate_hour_occupancy (
    airport_iata STRING, gate_id STRING, hour_start_utc TIMESTAMP,
    occupied_minutes INT, flights INT,
    batch_id BIGINT, last_event_ts TIMESTAMP, last_emitted_ts TIMESTAMP,
    written_at TIMESTAMP,
    PRIMARY KEY (airport_iata, gate_id, hour_start_utc)
)
CLUSTER BY (airport_iata, gate_id, hour_start_utc);

CREATE TABLE IF NOT EXISTS {schema}.gold_hub_connections (
    hub_iata STRING, hour_start_utc TIMESTAMP,
    connections INT, pax_connecting INT,
    connections_at_risk INT, pax_at_risk INT,
    connections_missed INT, pax_missed INT,
    batch_id BIGINT, last_event_ts TIMESTAMP, last_emitted_ts TIMESTAMP,
    written_at TIMESTAMP,
    PRIMARY KEY (hub_iata, hour_start_utc)
)
CLUSTER BY (hub_iata, hour_start_utc);
