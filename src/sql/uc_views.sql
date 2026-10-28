-- Silver and gold views for airport operations. All timestamps UTC.
-- Parameters: min_ground_time_min, min_connection_time_min, otp_threshold_min
-- Prune predicates (event_date, airport_iata) retained in all views for pushdown to bronze.

-- Silver: deduped milestones, latest per (flight_id, event_type)
CREATE OR REPLACE VIEW {schema}.silver_flight_milestones_v AS
WITH m AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY flight_id, event_type ORDER BY event_ts DESC) AS rn
    FROM {schema}.bronze_flight_milestones
)
SELECT
    event_id, event_type, event_ts, emitted_ts, event_date, airport_iata,
    flight_id, flight_number, origin_iata, destination_iata, tail_number,
    std_utc, sta_utc, gate_id, delay_cause, delay_minutes, diversion_iata
FROM m
WHERE rn = 1;

-- Silver: one row per flight with computed columns. Retain prune columns.
CREATE OR REPLACE VIEW {schema}.silver_flight_v AS
WITH m AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY flight_id ORDER BY event_ts DESC) AS recency
    FROM {schema}.silver_flight_milestones_v
)
SELECT
    flight_id, flight_number, origin_iata, destination_iata, tail_number, std_utc, sta_utc,
    MAX(CASE WHEN event_type = 'OFF_BLOCK' THEN event_ts END) AS aobt,
    MAX(CASE WHEN event_type = 'TAKEOFF' THEN event_ts END) AS atot,
    MAX(CASE WHEN event_type = 'LANDING' THEN event_ts END) AS aldt,
    MAX(CASE WHEN event_type = 'IN_BLOCK' THEN event_ts END) AS aibt,
    MAX(CASE WHEN event_type = 'BOARDING_START' THEN event_ts END) AS boarding_start_ts,
    MAX(CASE WHEN event_type = 'GATE_ASSIGNED' AND airport_iata = origin_iata THEN gate_id END) AS gate_dep,
    MAX(CASE WHEN event_type = 'IN_BLOCK' AND airport_iata = destination_iata THEN gate_id END) AS gate_arr,
    MAX(CASE WHEN event_type = 'CANCELLED' THEN TRUE ELSE FALSE END) AS cancelled,
    MAX(CASE WHEN event_type = 'DIVERTED' THEN diversion_iata END) AS diverted,
    MAX(CASE WHEN event_type = 'DELAY_REPORTED' THEN delay_cause END) AS delay_cause,
    MAX(CASE WHEN event_type = 'DELAY_REPORTED' THEN delay_minutes END) AS delay_minutes,
    MAX(event_date) AS event_date,
    origin_iata AS airport_iata,
    MAX(last_event_ts) AS last_event_ts,
    MAX(last_emitted_ts) AS last_emitted_ts
FROM m
GROUP BY flight_id, flight_number, origin_iata, destination_iata, tail_number, std_utc, sta_utc;

-- Gold: flight_state (key flight_id). Prune columns retained for filter pushdown.
CREATE OR REPLACE VIEW {schema}.gold_flight_state_v AS
WITH sf AS (
    SELECT * FROM {schema}.silver_flight_v
),
prior_flight AS (
    SELECT sf.flight_id,
        LAG(sf.flight_id) OVER (PARTITION BY sf.tail_number ORDER BY sf.aibt NULLS LAST, sf.std_utc) AS prior_flight_id,
        LAG(sf.aibt) OVER (PARTITION BY sf.tail_number ORDER BY sf.aibt NULLS LAST, sf.std_utc) AS prior_aibt
    FROM sf
)
SELECT
    sf.flight_id,
    sf.flight_number, sf.origin_iata, sf.destination_iata, sf.tail_number,
    sf.std_utc, sf.sta_utc, sf.gate_dep, sf.gate_arr, sf.boarding_start_ts,
    sf.aobt, sf.atot, sf.aldt, sf.aibt,
    CASE
        WHEN sf.cancelled THEN 'CANCELLED'
        WHEN sf.diverted IS NOT NULL THEN 'DIVERTED'
        WHEN sf.aibt IS NOT NULL THEN 'AT_GATE'
        WHEN sf.aldt IS NOT NULL THEN 'TAXI_IN'
        WHEN sf.atot IS NOT NULL THEN 'AIRBORNE'
        WHEN sf.aobt IS NOT NULL THEN 'TAXI_OUT'
        WHEN sf.boarding_start_ts IS NOT NULL THEN 'BOARDING'
        ELSE 'SCHEDULED'
    END AS phase,
    CASE WHEN sf.aobt IS NOT NULL THEN CAST((sf.aobt - sf.std_utc) / 60 AS INT) ELSE NULL END AS dep_delay_min,
    CASE WHEN sf.aibt IS NOT NULL THEN CAST((sf.aibt - sf.sta_utc) / 60 AS INT) ELSE NULL END AS arr_delay_min,
    sf.delay_cause,
    CASE WHEN sf.atot IS NOT NULL AND sf.aobt IS NOT NULL THEN CAST((sf.atot - sf.aobt) / 60 AS INT) ELSE NULL END AS taxi_out_min,
    CASE WHEN sf.aibt IS NOT NULL AND sf.aldt IS NOT NULL THEN CAST((sf.aibt - sf.aldt) / 60 AS INT) ELSE NULL END AS taxi_in_min,
    CASE WHEN sf.aobt IS NOT NULL AND pf.prior_aibt IS NOT NULL
        THEN CAST((sf.aobt - pf.prior_aibt) / 60 AS INT) ELSE NULL END AS turnaround_min,
    CASE WHEN sf.aobt IS NOT NULL AND pf.prior_aibt IS NOT NULL
        THEN CAST((sf.aobt - pf.prior_aibt) / 60 AS INT) >= {min_ground_time_min} ELSE NULL END AS turnaround_ok,
    sf.event_date, sf.airport_iata,
    sf.last_event_ts, sf.last_emitted_ts
FROM sf
LEFT JOIN prior_flight pf ON sf.flight_id = pf.flight_id;

-- Gold: airport_hour_kpis (key airport_iata, hour_start_utc). Prune columns for filter.
CREATE OR REPLACE VIEW {schema}.gold_airport_hour_v AS
WITH base AS (
    SELECT
        origin_iata, event_date, airport_iata,
        CAST(CONCAT(DATE(std_utc), ' ', LPAD(CAST(HOUR(std_utc) AS STRING), 2, '0'), ':00:00') AS TIMESTAMP) AS hour_start_utc,
        CAST((aobt - std_utc) / 60 AS INT) AS dep_delay_min,
        CAST((atot - aobt) / 60 AS INT) AS taxi_out_min,
        delay_minutes, cancelled, diverted,
        last_event_ts, last_emitted_ts, 'DEP' AS mov
    FROM {schema}.silver_flight_v
    WHERE origin_iata IS NOT NULL

    UNION ALL

    SELECT
        destination_iata, event_date, airport_iata,
        CAST(CONCAT(DATE(sta_utc), ' ', LPAD(CAST(HOUR(sta_utc) AS STRING), 2, '0'), ':00:00') AS TIMESTAMP) AS hour_start_utc,
        CAST((aibt - sta_utc) / 60 AS INT) AS arr_delay_min,
        CAST((aibt - aldt) / 60 AS INT) AS taxi_in_min,
        delay_minutes, cancelled, diverted,
        last_event_ts, last_emitted_ts, 'ARR' AS mov
    FROM {schema}.silver_flight_v
    WHERE destination_iata IS NOT NULL
)
SELECT
    origin_iata AS airport_iata,
    hour_start_utc,
    COUNT(DISTINCT CASE WHEN mov = 'DEP' THEN 1 END) AS dep_scheduled,
    COUNT(DISTINCT CASE WHEN mov = 'DEP' AND dep_delay_min IS NOT NULL THEN 1 END) AS departed,
    COUNT(DISTINCT CASE WHEN mov = 'ARR' THEN 1 END) AS arr_scheduled,
    COUNT(DISTINCT CASE WHEN mov = 'ARR' AND arr_delay_min IS NOT NULL THEN 1 END) AS arrived,
    NULL AS dep_otp_d0,
    NULL AS dep_otp_d15,
    NULL AS arr_otp_d0,
    NULL AS arr_otp_d15,
    AVG(CASE WHEN mov = 'DEP' THEN taxi_out_min END) AS avg_taxi_out_min,
    AVG(CASE WHEN mov = 'ARR' THEN taxi_in_min END) AS avg_taxi_in_min,
    SUM(CASE WHEN delay_minutes IS NOT NULL THEN delay_minutes ELSE 0 END) AS delay_minutes,
    COUNT(DISTINCT CASE WHEN cancelled THEN 1 END) AS cancellations,
    COUNT(DISTINCT CASE WHEN diverted IS NOT NULL THEN 1 END) AS diversions,
    event_date, airport_iata,
    MAX(last_event_ts) AS last_event_ts,
    MAX(last_emitted_ts) AS last_emitted_ts
FROM base
GROUP BY origin_iata, hour_start_utc, event_date, airport_iata;

-- Gold: gate_hour_occupancy (key airport_iata, gate_id, hour_start_utc).
-- Occupancy windows span all hours touched; old gate handled via separate queries in gold_sink.
CREATE OR REPLACE VIEW {schema}.gold_gate_hour_v AS
WITH occupancies AS (
    SELECT
        origin_iata, gate_dep,
        COALESCE(LEAD(aibt) OVER (PARTITION BY tail_number ORDER BY std_utc), std_utc - INTERVAL '60' MINUTE) AS occupancy_start,
        COALESCE(aobt, CURRENT_TIMESTAMP()) AS occupancy_end,
        flight_id,
        event_date, origin_iata AS airport_iata_prune,
        last_event_ts, last_emitted_ts
    FROM {schema}.silver_flight_v
    WHERE gate_dep IS NOT NULL
)
SELECT
    origin_iata AS airport_iata,
    gate_dep,
    CAST(CONCAT(DATE(occupancy_start), ' ', LPAD(CAST(HOUR(occupancy_start) AS STRING), 2, '0'), ':00:00') AS TIMESTAMP) AS hour_start_utc,
    SUM(GREATEST(0, LEAST(
        UNIX_TIMESTAMP(CONCAT(DATE(occupancy_start), ' ', LPAD(CAST(HOUR(occupancy_start) + 1 AS STRING), 2, '0'), ':00:00')),
        UNIX_TIMESTAMP(occupancy_end)
    ) - GREATEST(
        UNIX_TIMESTAMP(CONCAT(DATE(occupancy_start), ' ', LPAD(CAST(HOUR(occupancy_start) AS STRING), 2, '0'), ':00:00')),
        UNIX_TIMESTAMP(occupancy_start)
    )) / 60) AS occupied_minutes,
    COUNT(DISTINCT flight_id) AS flights,
    event_date, airport_iata_prune,
    MAX(last_event_ts) AS last_event_ts,
    MAX(last_emitted_ts) AS last_emitted_ts
FROM occupancies
GROUP BY origin_iata, gate_dep, CAST(CONCAT(DATE(occupancy_start), ' ', LPAD(CAST(HOUR(occupancy_start) AS STRING), 2, '0'), ':00:00') AS TIMESTAMP), event_date, airport_iata_prune;

-- Gold: hub_connections (key hub_iata, hour_start_utc). Prune: event_date, airport_iata.
CREATE OR REPLACE VIEW {schema}.gold_hub_connections_v AS
WITH connections AS (
    SELECT
        dc.inbound_flight_id, dc.outbound_flight_id, dc.pax,
        inb.destination_iata AS hub_iata,
        outb.origin_iata AS hub_check,
        outb.std_utc,
        COALESCE(
            inb.aibt,
            inb.aldt + INTERVAL '8' MINUTE,
            inb.sta_utc + CASE WHEN inb.delay_minutes IS NOT NULL THEN INTERVAL '1' MINUTE * inb.delay_minutes ELSE INTERVAL '0' SECOND END,
            inb.sta_utc
        ) AS inbound_ready_ts,
        outb.aobt,
        inb.event_date,
        inb.last_event_ts, inb.last_emitted_ts
    FROM {schema}.dim_connection dc
    JOIN {schema}.silver_flight_v inb ON dc.inbound_flight_id = inb.flight_id
    JOIN {schema}.silver_flight_v outb ON dc.outbound_flight_id = outb.flight_id
)
SELECT
    hub_iata,
    CAST(CONCAT(DATE(std_utc), ' ', LPAD(CAST(HOUR(std_utc) AS STRING), 2, '0'), ':00:00') AS TIMESTAMP) AS hour_start_utc,
    COUNT(DISTINCT outbound_flight_id) AS connections,
    SUM(pax) AS pax_connecting,
    COUNT(DISTINCT CASE WHEN aobt IS NULL AND inbound_ready_ts + INTERVAL '1' MINUTE * {min_connection_time_min} > std_utc THEN outbound_flight_id END) AS connections_at_risk,
    COALESCE(SUM(CASE WHEN aobt IS NULL AND inbound_ready_ts + INTERVAL '1' MINUTE * {min_connection_time_min} > std_utc THEN pax END), 0) AS pax_at_risk,
    COUNT(DISTINCT CASE WHEN aobt IS NOT NULL AND inbound_ready_ts + INTERVAL '1' MINUTE * {min_connection_time_min} > aobt THEN outbound_flight_id END) AS connections_missed,
    COALESCE(SUM(CASE WHEN aobt IS NOT NULL AND inbound_ready_ts + INTERVAL '1' MINUTE * {min_connection_time_min} > aobt THEN pax END), 0) AS pax_missed,
    event_date, hub_iata AS airport_iata,
    MAX(last_event_ts) AS last_event_ts,
    MAX(last_emitted_ts) AS last_emitted_ts
FROM connections
WHERE hub_iata = hub_check
GROUP BY hub_iata, CAST(CONCAT(DATE(std_utc), ' ', LPAD(CAST(HOUR(std_utc) AS STRING), 2, '0'), ':00:00') AS TIMESTAMP), event_date;
