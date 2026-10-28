# Contract §7 for Genie: dynamic views over the UC replicas (rows + sections by group), metric views on top.
# Pure function so the setup notebook and the tests run the exact same statements.

# airport columns per gold grain (same as the Lakebase RLS policies)
SCOPE = {
    "flight_state": ["origin_iata", "destination_iata"],
    "airport_hour_kpis": ["airport_iata"],
    "gate_hour_occupancy": ["airport_iata"],
    "hub_connections": ["hub_iata"],
}
TECH_COLS = "batch_id, last_emitted_ts, written_at"

VIEW_COMMENTS = {
    "flight_state": "One row per flight: schedule, A-CDM milestones (UTC), phase, delays, gates, taxi and turnaround. Filtered to the reader's airports.",
    "airport_hour_kpis": "Per airport and UTC hour: scheduled vs completed movements, OTP D0/D15 (share of completed movements), taxi times, delay minutes, cancellations, diversions.",
    "gate_hour_occupancy": "Per gate and UTC hour: minutes the gate was occupied and flights that used it.",
    "hub_connections": "Per hub and outbound UTC hour: passenger connections, at risk (inbound ready + min connection time after outbound departure) and missed.",
}

COLUMN_COMMENTS = {
    "flight_state": {
        "flight_id": "Unique flight key (flight number + date + origin)",
        "origin_iata": "Departure airport IATA code",
        "destination_iata": "Arrival airport IATA code",
        "tail_number": "Aircraft registration; all aircraft are B737-800",
        "std_utc": "Scheduled time of departure (UTC)",
        "sta_utc": "Scheduled time of arrival (UTC)",
        "gate_dep": "Departure gate (hub gates only)",
        "gate_arr": "Arrival gate (hub gates only)",
        "aobt": "Actual off-block time (UTC); NULL = not yet departed",
        "atot": "Actual take-off time (UTC)",
        "aldt": "Actual landing time (UTC)",
        "aibt": "Actual in-block time (UTC); NULL = not yet arrived",
        "phase": "SCHEDULED, BOARDING, TAXI_OUT, AIRBORNE, TAXI_IN, AT_GATE, CANCELLED or DIVERTED",
        "dep_delay_min": "Departure delay in minutes (aobt - std_utc)",
        "arr_delay_min": "Arrival delay in minutes (aibt - sta_utc)",
        "delay_cause": "Reported delay cause category: AIRCRAFT, CREW, PASSENGER, EXTERNAL, GROUND_OPS",
        "taxi_out_min": "atot - aobt in minutes",
        "taxi_in_min": "aibt - aldt in minutes",
        "turnaround_min": "Ground time on the same tail: this aobt - previous aibt",
        "turnaround_ok": "TRUE when turnaround_min >= minimum ground time",
        "last_event_ts": "Latest milestone event time for the flight (UTC)",
    },
    "airport_hour_kpis": {
        "hour_start_utc": "Start of the UTC hour",
        "dep_otp_d0": "Share (0-1) of completed departures at or before schedule",
        "dep_otp_d15": "Share (0-1) of completed departures within 15 min of schedule",
        "arr_otp_d0": "Share (0-1) of completed arrivals at or before schedule",
        "arr_otp_d15": "Share (0-1) of completed arrivals within 15 min of schedule",
        "delay_minutes": "Reported departure delay minutes",
    },
    "gate_hour_occupancy": {
        "occupied_minutes": "Minutes in the hour the gate was occupied (0-60)",
        "flights": "Distinct flights that used the gate in the hour",
    },
    "hub_connections": {
        "hour_start_utc": "Outbound departure UTC hour",
        "connections_at_risk": "Connections not yet departed where inbound ready + min connection time is after the outbound departure",
        "connections_missed": "Connections whose outbound left before inbound ready + min connection time",
    },
}

# measures are re-aggregated from hourly rows; OTP is weighted by completed movements
METRIC_VIEWS = {
    "genie_airport_metrics": ("airport_hour_kpis", """
version: 1.1
comment: Airport operations KPIs by airport and UTC hour
dimensions:
  - name: airport_iata
    expr: airport_iata
  - name: hour_start_utc
    expr: hour_start_utc
  - name: ops_date
    expr: DATE(hour_start_utc)
measures:
  - name: departures
    expr: SUM(departed)
  - name: arrivals
    expr: SUM(arrived)
  - name: scheduled_departures
    expr: SUM(dep_scheduled)
  - name: dep_otp_d15
    expr: SUM(dep_otp_d15 * departed) / NULLIF(SUM(CASE WHEN dep_otp_d15 IS NOT NULL THEN departed END), 0)
  - name: dep_otp_d0
    expr: SUM(dep_otp_d0 * departed) / NULLIF(SUM(CASE WHEN dep_otp_d0 IS NOT NULL THEN departed END), 0)
  - name: arr_otp_d15
    expr: SUM(arr_otp_d15 * arrived) / NULLIF(SUM(CASE WHEN arr_otp_d15 IS NOT NULL THEN arrived END), 0)
  - name: avg_taxi_out_min
    expr: SUM(avg_taxi_out_min * departed) / NULLIF(SUM(CASE WHEN avg_taxi_out_min IS NOT NULL THEN departed END), 0)
  - name: delay_minutes
    expr: SUM(delay_minutes)
  - name: cancellations
    expr: SUM(cancellations)
  - name: diversions
    expr: SUM(diversions)
"""),
    "genie_connection_metrics": ("hub_connections", """
version: 1.1
comment: Hub passenger connections by outbound UTC hour
dimensions:
  - name: hub_iata
    expr: hub_iata
  - name: hour_start_utc
    expr: hour_start_utc
  - name: ops_date
    expr: DATE(hour_start_utc)
measures:
  - name: connections
    expr: SUM(connections)
  - name: connections_at_risk
    expr: SUM(connections_at_risk)
  - name: connections_missed
    expr: SUM(connections_missed)
  - name: pax_connecting
    expr: SUM(pax_connecting)
  - name: pax_at_risk
    expr: SUM(pax_at_risk)
  - name: connection_success_rate
    expr: 1 - SUM(connections_missed) / NULLIF(SUM(connections), 0)
"""),
}


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def row_predicate(access, table, member="is_member({})"):
    # a row is visible if some group the reader belongs to has a section on this table and the airport
    cols = SCOPE[table]
    terms = []
    for r in access["roles"]:
        if not any(table in access["section_tables"][s] for s in r["sections"]):
            continue
        m = member.format(_q(r["group"]))
        if "*" in r["airports"]:
            terms.append(m)
        else:
            lits = ", ".join(_q(a) for a in r["airports"])
            terms.append(f"({m} AND (" + " OR ".join(f"{c} IN ({lits})" for c in cols) + "))")
    return " OR ".join(terms) or "FALSE"


def view_statements(access, uc, member="is_member({})", source=None):
    # member: group test template (tests swap in a fixed group list); source: schema of the gold_* replicas
    src = source or uc
    out = []
    for table in SCOPE:
        v = f"{uc}.genie_{table}"
        out.append(f"CREATE OR REPLACE VIEW {v} COMMENT {_q(VIEW_COMMENTS[table])} AS "
                   f"SELECT * EXCEPT ({TECH_COLS}) FROM {src}.gold_{table} WHERE {row_predicate(access, table, member)}")
        out += [f"COMMENT ON COLUMN {v}.{c} IS {_q(t)}" for c, t in COLUMN_COMMENTS[table].items()]
    for name, (table, spec) in METRIC_VIEWS.items():
        out.append(f"CREATE OR REPLACE VIEW {uc}.{name} WITH METRICS LANGUAGE YAML AS $$\n"
                   f"source: {uc}.genie_{table}\n{spec.strip()}\n$$")
    return out


def genie_objects(uc):
    return [f"{uc}.genie_{t}" for t in SCOPE] + [f"{uc}.{n}" for n in METRIC_VIEWS]


def grant_statements(uc, reader):
    # workspace-local groups cannot hold UC grants: readers get SELECT, the views filter by is_member()
    cat = uc.split(".")[0]
    return [f"GRANT USE CATALOG ON CATALOG {cat} TO `{reader}`", f"GRANT USE SCHEMA ON SCHEMA {uc} TO `{reader}`"] + \
        [f"GRANT SELECT ON TABLE {o} TO `{reader}`" for o in genie_objects(uc)]
