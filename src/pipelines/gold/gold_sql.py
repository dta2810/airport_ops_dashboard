# Single source of the silver/gold SQL. Views render with prune "TRUE"; the sink renders the same
# text with a literal event_date list injected on the bronze scan (below every window/aggregate).

GRAINS = {
    "flight_state": ["flight_id"],
    "airport_hour_kpis": ["airport_iata", "hour_start_utc"],
    "gate_hour_occupancy": ["airport_iata", "gate_id", "hour_start_utc"],
    "hub_connections": ["hub_iata", "hour_start_utc"],
}

VIEWS = {
    "flight_state": "gold_flight_state_v",
    "airport_hour_kpis": "gold_airport_hour_v",
    "gate_hour_occupancy": "gold_gate_hour_v",
    "hub_connections": "gold_hub_connections_v",
}


def _silver(s, prune):
    return f"""
bronze_p AS (
  SELECT * FROM {s}.bronze_flight_milestones WHERE {prune}
),
ms AS (
  SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY flight_id, event_type
                                 ORDER BY event_ts DESC, emitted_ts DESC, event_id) AS rn
    FROM bronze_p
  ) WHERE rn = 1
),
sf AS (
  SELECT flight_id,
    ANY_VALUE(flight_number) AS flight_number, ANY_VALUE(origin_iata) AS origin_iata,
    ANY_VALUE(destination_iata) AS destination_iata, ANY_VALUE(tail_number) AS tail_number,
    ANY_VALUE(std_utc) AS std_utc, ANY_VALUE(sta_utc) AS sta_utc,
    MAX(CASE WHEN event_type = 'GATE_ASSIGNED' THEN gate_id END) AS gate_dep,
    MAX(CASE WHEN event_type = 'IN_BLOCK' THEN gate_id END) AS gate_arr,
    MAX(CASE WHEN event_type = 'BOARDING_START' THEN event_ts END) AS boarding_start_ts,
    MAX(CASE WHEN event_type = 'OFF_BLOCK' THEN event_ts END) AS aobt,
    MAX(CASE WHEN event_type = 'TAKEOFF' THEN event_ts END) AS atot,
    MAX(CASE WHEN event_type = 'LANDING' THEN event_ts END) AS aldt,
    MAX(CASE WHEN event_type = 'IN_BLOCK' THEN event_ts END) AS aibt,
    BOOL_OR(event_type = 'CANCELLED') AS cancelled,
    MAX(CASE WHEN event_type = 'DIVERTED' THEN diversion_iata END) AS diversion_iata,
    MAX(CASE WHEN event_type = 'DELAY_REPORTED' THEN delay_cause END) AS delay_cause,
    MAX(CASE WHEN event_type = 'DELAY_REPORTED' THEN delay_minutes END) AS delay_minutes,
    MAX(event_ts) AS last_event_ts, MAX(emitted_ts) AS last_emitted_ts
  FROM ms GROUP BY flight_id
),
fs AS (
  SELECT sf.*,
    LAG(aibt) OVER (PARTITION BY tail_number ORDER BY std_utc) AS prior_aibt,
    LAG(destination_iata) OVER (PARTITION BY tail_number ORDER BY std_utc) AS prior_destination
  FROM sf
),
flight_state AS (
  SELECT flight_id, flight_number, origin_iata, destination_iata, tail_number, std_utc, sta_utc,
    gate_dep, gate_arr, boarding_start_ts, aobt, atot, aldt, aibt,
    CASE WHEN cancelled THEN 'CANCELLED'
         WHEN diversion_iata IS NOT NULL THEN 'DIVERTED'
         WHEN aibt IS NOT NULL THEN 'AT_GATE'
         WHEN aldt IS NOT NULL THEN 'TAXI_IN'
         WHEN atot IS NOT NULL THEN 'AIRBORNE'
         WHEN aobt IS NOT NULL THEN 'TAXI_OUT'
         WHEN boarding_start_ts IS NOT NULL THEN 'BOARDING'
         ELSE 'SCHEDULED' END AS phase,
    CAST(timestampdiff(MINUTE, std_utc, aobt) AS INT) AS dep_delay_min,
    CAST(timestampdiff(MINUTE, sta_utc, aibt) AS INT) AS arr_delay_min,
    delay_cause,
    CAST(timestampdiff(MINUTE, aobt, atot) AS INT) AS taxi_out_min,
    CAST(timestampdiff(MINUTE, aldt, aibt) AS INT) AS taxi_in_min,
    CAST(CASE WHEN prior_destination = origin_iata THEN timestampdiff(MINUTE, prior_aibt, aobt) END AS INT) AS turnaround_min,
    CASE WHEN prior_destination = origin_iata AND aobt IS NOT NULL AND prior_aibt IS NOT NULL
         THEN timestampdiff(MINUTE, prior_aibt, aobt) >= {{min_ground_time_min}} END AS turnaround_ok,
    last_event_ts, last_emitted_ts,
    cancelled, diversion_iata, delay_minutes, prior_aibt, prior_destination
  FROM fs
)"""


_FINAL = {
    "flight_state": """
SELECT flight_id, flight_number, origin_iata, destination_iata, tail_number, std_utc, sta_utc,
  gate_dep, gate_arr, boarding_start_ts, aobt, atot, aldt, aibt, phase, dep_delay_min, arr_delay_min,
  delay_cause, taxi_out_min, taxi_in_min, turnaround_min, turnaround_ok, last_event_ts, last_emitted_ts
FROM flight_state""",

    "airport_hour_kpis": """
, mv AS (
  SELECT origin_iata AS airport_iata, date_trunc('HOUR', std_utc) AS hour_start_utc, 'DEP' AS mov,
    (NOT cancelled AND aobt IS NOT NULL) AS completed, dep_delay_min AS delay_min, taxi_out_min AS taxi_min,
    cancelled, FALSE AS diverted, delay_minutes AS reported_min, last_event_ts, last_emitted_ts
  FROM flight_state
  UNION ALL
  SELECT destination_iata, date_trunc('HOUR', sta_utc), 'ARR',
    aibt IS NOT NULL, arr_delay_min, taxi_in_min,
    FALSE, diversion_iata IS NOT NULL, NULL, last_event_ts, last_emitted_ts
  FROM flight_state
)
SELECT airport_iata, hour_start_utc,
  CAST(COUNT_IF(mov = 'DEP') AS INT) AS dep_scheduled,
  CAST(COUNT_IF(mov = 'DEP' AND completed) AS INT) AS departed,
  CAST(COUNT_IF(mov = 'ARR') AS INT) AS arr_scheduled,
  CAST(COUNT_IF(mov = 'ARR' AND completed) AS INT) AS arrived,
  AVG(CASE WHEN mov = 'DEP' AND completed THEN CAST(delay_min <= 0 AS INT) END) AS dep_otp_d0,
  AVG(CASE WHEN mov = 'DEP' AND completed THEN CAST(delay_min <= {otp_threshold_min} AS INT) END) AS dep_otp_d15,
  AVG(CASE WHEN mov = 'ARR' AND completed THEN CAST(delay_min <= 0 AS INT) END) AS arr_otp_d0,
  AVG(CASE WHEN mov = 'ARR' AND completed THEN CAST(delay_min <= {otp_threshold_min} AS INT) END) AS arr_otp_d15,
  AVG(CASE WHEN mov = 'DEP' AND completed THEN CAST(taxi_min AS DOUBLE) END) AS avg_taxi_out_min,
  AVG(CASE WHEN mov = 'ARR' AND completed THEN CAST(taxi_min AS DOUBLE) END) AS avg_taxi_in_min,
  CAST(COALESCE(SUM(CASE WHEN mov = 'DEP' THEN reported_min END), 0) AS INT) AS delay_minutes,
  CAST(COUNT_IF(mov = 'DEP' AND cancelled) AS INT) AS cancellations,
  CAST(COUNT_IF(mov = 'ARR' AND diverted) AS INT) AS diversions,
  MAX(last_event_ts) AS last_event_ts, MAX(last_emitted_ts) AS last_emitted_ts
FROM mv GROUP BY airport_iata, hour_start_utc""",

    "gate_hour_occupancy": """
, ops_clock AS (SELECT MAX(event_ts) AS now_ts FROM bronze_p)
, occ AS (
  SELECT f.origin_iata AS airport_iata, f.gate_dep AS gate_id, f.flight_id,
    COALESCE(CASE WHEN f.prior_destination = f.origin_iata
                   AND f.prior_aibt >= f.std_utc - INTERVAL 6 HOURS
                   AND f.prior_aibt < COALESCE(f.aobt, f.std_utc) THEN f.prior_aibt END,
             f.std_utc - INTERVAL 60 MINUTES) AS occ_start,
    COALESCE(f.aobt, c.now_ts) AS occ_end,
    f.last_event_ts, f.last_emitted_ts
  FROM flight_state f CROSS JOIN ops_clock c
  WHERE f.gate_dep IS NOT NULL AND NOT f.cancelled
)
, hrs AS (
  SELECT *, explode(sequence(date_trunc('HOUR', occ_start),
                             date_trunc('HOUR', occ_end - INTERVAL 1 SECOND), INTERVAL 1 HOUR)) AS hour_start_utc
  FROM occ WHERE occ_end > occ_start
)
SELECT airport_iata, gate_id, hour_start_utc,
  CAST(ROUND(SUM(timestampdiff(SECOND, GREATEST(occ_start, hour_start_utc),
                               LEAST(occ_end, hour_start_utc + INTERVAL 1 HOUR)) / 60.0)) AS INT) AS occupied_minutes,
  CAST(COUNT(DISTINCT flight_id) AS INT) AS flights,
  MAX(last_event_ts) AS last_event_ts, MAX(last_emitted_ts) AS last_emitted_ts
FROM hrs GROUP BY airport_iata, gate_id, hour_start_utc""",

    "hub_connections": """
, cx AS (
  SELECT c.pax, o.origin_iata AS hub_iata, date_trunc('HOUR', o.std_utc) AS hour_start_utc,
    COALESCE(i.aibt, i.aldt + INTERVAL 8 MINUTES, timestampadd(MINUTE, i.dep_delay_min, i.sta_utc), i.sta_utc) AS ready_ts,
    o.aobt, o.std_utc,
    GREATEST(i.last_event_ts, o.last_event_ts) AS last_event_ts,
    GREATEST(i.last_emitted_ts, o.last_emitted_ts) AS last_emitted_ts
  FROM {s}.dim_connection c
  JOIN flight_state i ON c.inbound_flight_id = i.flight_id
  JOIN flight_state o ON c.outbound_flight_id = o.flight_id
  WHERE o.origin_iata = '{hub}' AND i.destination_iata = '{hub}'
)
SELECT hub_iata, hour_start_utc,
  CAST(COUNT(*) AS INT) AS connections,
  CAST(SUM(pax) AS INT) AS pax_connecting,
  CAST(COUNT_IF(aobt IS NULL AND timestampadd(MINUTE, {min_connection_time_min}, ready_ts) > std_utc) AS INT) AS connections_at_risk,
  CAST(COALESCE(SUM(CASE WHEN aobt IS NULL AND timestampadd(MINUTE, {min_connection_time_min}, ready_ts) > std_utc THEN pax END), 0) AS INT) AS pax_at_risk,
  CAST(COUNT_IF(aobt IS NOT NULL AND aobt < timestampadd(MINUTE, {min_connection_time_min}, ready_ts)) AS INT) AS connections_missed,
  CAST(COALESCE(SUM(CASE WHEN aobt IS NOT NULL AND aobt < timestampadd(MINUTE, {min_connection_time_min}, ready_ts) THEN pax END), 0) AS INT) AS pax_missed,
  MAX(last_event_ts) AS last_event_ts, MAX(last_emitted_ts) AS last_emitted_ts
FROM cx GROUP BY hub_iata, hour_start_utc""",
}


def grain_sql(grain, schema, params, prune="TRUE"):
    # params: min_ground_time_min, min_connection_time_min, otp_threshold_min, hub
    body = _silver(schema, prune).replace("{min_ground_time_min}", str(int(params["min_ground_time_min"])))
    final = _FINAL[grain].format(s=schema, hub=params["hub"],
                                 otp_threshold_min=int(params["otp_threshold_min"]),
                                 min_connection_time_min=int(params["min_connection_time_min"]))
    if final.lstrip().startswith(","):
        return f"WITH {body}{final}"
    return f"WITH {body}\n{final}"


def view_ddl(grain, schema, params):
    return f"CREATE OR REPLACE VIEW {schema}.{VIEWS[grain]} AS\n{grain_sql(grain, schema, params)}"
