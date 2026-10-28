#!/usr/bin/env python3
"""
Tests for ops_sim: verify schema conformance, flight topology, and realism.
Run with: pytest tests/test_ops_sim.py -v
"""

import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "src/simulator")
from ops_sim import OpsSimulator

UTC = timezone.utc
CONFIG_PATH = "config/demo.yaml"


def test_schema_scheduled_event():
    """Verify SCHEDULED event has required fields."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(1, aircraft, airports)
    events = sim.generate_events(1, flight_map)

    scheduled = [e for e in events if e["event_type"] == "SCHEDULED"]
    assert len(scheduled) > 0, "No SCHEDULED events generated"

    event = scheduled[0]
    required = [
        "event_id", "event_type", "event_ts", "emitted_ts", "event_date",
        "airport_iata", "flight_id", "flight_number", "origin_iata",
        "destination_iata", "tail_number", "std_utc", "sta_utc"
    ]
    for field in required:
        assert field in event, f"Missing field {field}"

    # SCHEDULED should not have gate_id, delay fields, or diversion
    assert event.get("gate_id") is None
    assert event.get("delay_minutes") is None
    assert event.get("delay_cause") is None
    assert event.get("diversion_iata") is None


def test_schema_gate_assigned_event():
    """Verify GATE_ASSIGNED event has gate_id."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(1, aircraft, airports)
    events = sim.generate_events(1, flight_map)

    gate_assigned = [e for e in events if e["event_type"] == "GATE_ASSIGNED"]
    assert len(gate_assigned) > 0, "No GATE_ASSIGNED events"

    event = gate_assigned[0]
    assert event.get("gate_id") is not None, "GATE_ASSIGNED missing gate_id"


def test_schema_delay_reported_event():
    """Verify DELAY_REPORTED has delay_minutes and delay_cause."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(2, aircraft, airports)
    events = sim.generate_events(2, flight_map)

    delay_events = [e for e in events if e["event_type"] == "DELAY_REPORTED"]
    if len(delay_events) > 0:
        event = delay_events[0]
        assert event.get("delay_minutes") is not None
        assert event.get("delay_cause") is not None
        assert event["delay_cause"] in ["AIRCRAFT", "CREW", "PASSENGER", "EXTERNAL", "GROUND_OPS"]


def test_flight_id_format():
    """Verify flight_id matches AL<flight_number>-<yyyyMMdd of std_utc>-<origin_iata>."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(1, aircraft, airports)

    for flight_id, sched in flight_map.items():
        flight_num = sched["flight_number"]
        std_date = sched["std_utc"].strftime("%Y%m%d")
        origin = sched["origin"]

        expected = f"AL{flight_num}-{std_date}-{origin}"
        assert flight_id == expected, f"Flight ID {flight_id} != {expected}"


def test_every_flight_touches_hub():
    """Verify hub-and-spoke: every flight is hub->spoke or spoke->hub."""
    sim = OpsSimulator(CONFIG_PATH)
    hub = sim.cfg["network"]["hub"]
    spokes = set(sim.cfg["network"]["spokes"])

    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(3, aircraft, airports)

    for flight_id, sched in flight_map.items():
        origin = sched["origin"]
        dest = sched["destination"]

        # Every flight must have hub as either origin or destination
        has_hub = (origin == hub or dest == hub)
        assert has_hub, f"Flight {flight_id} {origin}->{dest} doesn't touch hub"

        # Other endpoint must be a spoke
        other = dest if origin == hub else origin
        assert other in spokes, f"Flight {flight_id}: {other} not a spoke"


def test_tail_rotations_respect_min_ground_time():
    """Verify that paired outbound/inbound flights respect min_ground_time."""
    sim = OpsSimulator(CONFIG_PATH)
    aircraft = sim.build_aircraft()
    airports = sim.build_airports()
    min_ground = sim.cfg["fleet"]["min_ground_time_min"]

    schedule, flight_map = sim.build_schedule(3, aircraft, airports)

    # For each tail, check that paired flights (PTY->X->PTY) respect min ground time
    by_tail = {}
    for sched in schedule:
        tail = sched["tail_number"]
        if tail not in by_tail:
            by_tail[tail] = []
        by_tail[tail].append(sched)

    hub = sim.cfg["network"]["hub"]
    for tail, flights in by_tail.items():
        # Group into potential pairs based on flight order
        for i in range(len(flights) - 1):
            curr = flights[i]
            # Look for return flight: outbound from hub, inbound to hub
            if curr["origin_iata"] == hub and curr["destination_iata"] != hub:
                # Find matching return flight
                for j in range(i + 1, len(flights)):
                    nxt = flights[j]
                    if nxt["origin_iata"] == curr["destination_iata"] and \
                       nxt["destination_iata"] == hub:
                        # This is the paired return flight
                        arrival = curr["sta_utc"]
                        departure = nxt["std_utc"]
                        turnaround = (departure - arrival).total_seconds() / 60

                        assert turnaround >= min_ground - 1, \
                            f"Tail {tail}: turnaround at {curr['destination_iata']} = {turnaround}min < {min_ground}min"
                        break


def test_tail_rotations_no_overlap():
    """Same tail: next departure is from where it landed, after the previous arrival + min ground time."""
    sim = OpsSimulator(CONFIG_PATH)
    min_ground = sim.cfg["fleet"]["min_ground_time_min"]
    schedule, _ = sim.build_schedule(7, sim.build_aircraft(), sim.build_airports())
    by_tail = {}
    for f in schedule:
        by_tail.setdefault(f["tail_number"], []).append(f)
    bad = []
    for flights in by_tail.values():
        flights.sort(key=lambda f: f["std_utc"])
        for a, b in zip(flights, flights[1:]):
            if b["std_utc"] < a["sta_utc"] + timedelta(minutes=min_ground) or b["origin_iata"] != a["destination_iata"]:
                bad.append((a["flight_id"], b["flight_id"]))
    assert not bad, f"{len(bad)} overlapping/teleporting rotations, e.g. {bad[:3]}"

def test_connections_at_hub_only():
    """Verify connections are only at the hub airport."""
    sim = OpsSimulator(CONFIG_PATH)
    hub = sim.cfg["network"]["hub"]

    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(2, aircraft, airports)
    connections = sim.build_connections(schedule)

    for conn in connections:
        inbound_id = conn["inbound_flight_id"]
        outbound_id = conn["outbound_flight_id"]

        inbound = flight_map[inbound_id]
        outbound = flight_map[outbound_id]

        # Inbound must arrive at hub
        assert inbound["destination"] == hub, \
            f"Connection inbound {inbound_id} doesn't arrive at hub"

        # Outbound must depart from hub
        assert outbound["origin"] == hub, \
            f"Connection outbound {outbound_id} doesn't depart from hub"


def test_connections_timing():
    """Verify inbound arrives before outbound departs (with MCT buffer)."""
    sim = OpsSimulator(CONFIG_PATH)
    hub = sim.cfg["network"]["hub"]
    min_conn_time = sim.cfg["kpis"]["min_connection_time_min"]

    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(2, aircraft, airports)
    connections = sim.build_connections(schedule)

    for conn in connections:
        inbound_id = conn["inbound_flight_id"]
        outbound_id = conn["outbound_flight_id"]

        inbound = flight_map[inbound_id]
        outbound = flight_map[outbound_id]

        inbound_arrival = inbound["sta_utc"]
        outbound_departure = outbound["std_utc"]

        # Outbound must depart after inbound arrival + MCT
        assert outbound_departure >= inbound_arrival + \
            __import__("datetime").timedelta(minutes=min_conn_time), \
            f"Connection {inbound_id}->{outbound_id}: MCT not respected"


def test_late_and_duplicate_events_exist():
    """Verify late and duplicate events are generated."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(5, aircraft, airports)
    events = sim.generate_events(5, flight_map)

    # Check for duplicate event_ids
    event_ids = [e["event_id"] for e in events]
    duplicates = len(event_ids) - len(set(event_ids))
    assert duplicates > 0, "No duplicate events found"

    # Check for late events (same event_id, later event_ts)
    # Count events with >1 hour gap from their flight's STD
    late_events = 0
    for event in events:
        std = event["std_utc"]
        event_ts = event["event_ts"]
        if (event_ts - std).total_seconds() / 3600 > 1:  # >1 hour after STD
            late_events += 1

    assert late_events > 0, "No late events found"


def test_determinism_by_seed():
    """Verify same seed produces identical events."""
    sim1 = OpsSimulator(CONFIG_PATH)
    airports1 = sim1.build_airports()
    aircraft1 = sim1.build_aircraft()
    schedule1, flight_map1 = sim1.build_schedule(2, aircraft1, airports1)
    events1 = sim1.generate_events(2, flight_map1)

    sim2 = OpsSimulator(CONFIG_PATH)
    airports2 = sim2.build_airports()
    aircraft2 = sim2.build_aircraft()
    schedule2, flight_map2 = sim2.build_schedule(2, aircraft2, airports2)
    events2 = sim2.generate_events(2, flight_map2)

    # Compare key fields
    assert len(events1) == len(events2), "Different event counts"

    for e1, e2 in zip(events1, events2):
        assert e1["flight_id"] == e2["flight_id"]
        assert e1["event_type"] == e2["event_type"]
        assert e1["event_date"] == e2["event_date"]


def test_airports_dimensions():
    """Verify dim_airport contains hub and all spokes."""
    sim = OpsSimulator(CONFIG_PATH)
    hub = sim.cfg["network"]["hub"]
    spokes = set(sim.cfg["network"]["spokes"])

    airports = sim.build_airports()
    airport_codes = {a["airport_iata"] for a in airports}

    assert hub in airport_codes, f"Hub {hub} not in airports"
    assert spokes.issubset(airport_codes), "Not all spokes in airports"


def test_gates_at_hub_only():
    """Verify gates are only at the hub."""
    sim = OpsSimulator(CONFIG_PATH)
    hub = sim.cfg["network"]["hub"]

    airports = sim.build_airports()
    gates = sim.build_gates(airports)

    gate_airports = {g["airport_iata"] for g in gates}
    assert gate_airports == {hub}, f"Gates found at {gate_airports}, expected only {hub}"


def test_routes_hub_and_spoke_only():
    """Verify all routes connect hub to spokes (no spoke-spoke routes)."""
    sim = OpsSimulator(CONFIG_PATH)
    hub = sim.cfg["network"]["hub"]
    spokes = set(sim.cfg["network"]["spokes"])

    airports = sim.build_airports()
    routes = sim.build_routes(airports)

    for route in routes:
        origin = route["origin_iata"]
        dest = route["destination_iata"]

        has_hub = hub in (origin, dest)
        assert has_hub, f"Route {origin}->{dest} doesn't include hub"

        other = dest if origin == hub else origin
        assert other in spokes, f"Route {origin}->{dest}: non-spoke endpoint"


def test_aircraft_all_b737():
    """Verify all aircraft are B737-800."""
    sim = OpsSimulator(CONFIG_PATH)
    aircraft = sim.build_aircraft()

    assert len(aircraft) == sim.cfg["fleet"]["size"]
    for a in aircraft:
        assert a["aircraft_type"] == "B737-800"
        assert a["seats"] == 160


def test_event_date_equals_date_event_ts():
    """Verify event_date == to_date(event_ts) for every event (contract §1)."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(2, aircraft, airports)
    events = sim.generate_events(2, flight_map)

    for event in events:
        expected_date = event["event_ts"].date()
        assert event["event_date"] == expected_date, \
            f"event_date {event['event_date']} != date(event_ts) {expected_date}"


def test_milestone_order_after_latest_event_ts_resolution():
    """Verify no broken OFF_BLOCK<TAKEOFF<LANDING<IN_BLOCK after contract resolution."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(3, aircraft, airports)
    events = sim.generate_events(3, flight_map)

    # Apply contract rule: latest event_ts per (flight_id, event_type) wins
    latest_per_key = {}
    for event in events:
        key = (event["flight_id"], event["event_type"])
        if key not in latest_per_key or event["event_ts"] > latest_per_key[key]["event_ts"]:
            latest_per_key[key] = event

    resolved_events = list(latest_per_key.values())

    # Group by flight_id
    by_flight = {}
    for event in resolved_events:
        fid = event["flight_id"]
        if fid not in by_flight:
            by_flight[fid] = {}
        by_flight[fid][event["event_type"]] = event

    # Check milestone order for non-cancelled/diverted flights
    event_order = ["SCHEDULED", "GATE_ASSIGNED", "BOARDING_START", "OFF_BLOCK",
                   "TAKEOFF", "LANDING", "IN_BLOCK"]

    for fid, events_by_type in by_flight.items():
        if "CANCELLED" in events_by_type or "DIVERTED" in events_by_type:
            continue

        present_events = [et for et in event_order if et in events_by_type]
        for i in range(len(present_events) - 1):
            curr_type = present_events[i]
            next_type = present_events[i + 1]
            curr_ts = events_by_type[curr_type]["event_ts"]
            next_ts = events_by_type[next_type]["event_ts"]

            assert curr_ts <= next_ts, \
                f"Flight {fid}: {curr_type} after {next_type}"


def test_determinism_seed_and_start_identical():
    """Verify identical events with same seed+backfill_start."""
    start_time = datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC)

    sim1 = OpsSimulator(CONFIG_PATH, backfill_start_utc=start_time)
    airports1 = sim1.build_airports()
    aircraft1 = sim1.build_aircraft()
    schedule1, flight_map1 = sim1.build_schedule(2, aircraft1, airports1)
    events1 = sim1.generate_events(2, flight_map1)

    sim2 = OpsSimulator(CONFIG_PATH, backfill_start_utc=start_time)
    airports2 = sim2.build_airports()
    aircraft2 = sim2.build_aircraft()
    schedule2, flight_map2 = sim2.build_schedule(2, aircraft2, airports2)
    events2 = sim2.generate_events(2, flight_map2)

    # Compare key fields
    assert len(events1) == len(events2), "Event counts differ"

    for e1, e2 in zip(events1, events2):
        assert e1["event_id"] == e2["event_id"], "event_id differs"
        assert e1["event_ts"] == e2["event_ts"], "event_ts differs"


def test_late_events_structure():
    """Verify late events are properly created with same event_id but delayed emitted_ts."""
    sim = OpsSimulator(CONFIG_PATH)
    airports = sim.build_airports()
    aircraft = sim.build_aircraft()
    schedule, flight_map = sim.build_schedule(2, aircraft, airports)
    events = sim.generate_events(2, flight_map)

    # Count unique event_ids and total events
    unique_ids = len(set(e["event_id"] for e in events))
    total_events = len(events)

    # Should have some duplicates (same event_id appearing multiple times)
    assert total_events > unique_ids, "No duplicate event_ids found (late events)"

    # Verify all late event copies have same event_ts as original
    by_id = {}
    for event in events:
        eid = event["event_id"]
        if eid not in by_id:
            by_id[eid] = []
        by_id[eid].append(event)

    for eid, event_list in by_id.items():
        if len(event_list) > 1:
            # All events with same event_id should have same event_ts
            first_ts = event_list[0]["event_ts"]
            for event in event_list[1:]:
                assert event["event_ts"] == first_ts, \
                    f"Late event has different event_ts than original"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])


def test_on_time_departures_mostly_arrive_on_time():
    """Scheduled block is gate to gate: an on-time departure should usually arrive within 15 min."""
    sim = OpsSimulator(CONFIG_PATH)
    aircraft = sim.build_aircraft()
    airports = sim.build_airports()
    schedule, flight_map = sim.build_schedule(3, aircraft, airports)
    events = sim.generate_events(3, flight_map)
    ts = lambda v: v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    by = {}
    for e in events:
        if e["event_type"] in ("OFF_BLOCK", "IN_BLOCK"):
            by.setdefault(e["flight_id"], {})[e["event_type"]] = e
    dep_ok = arr_ok = 0
    for f in by.values():
        if len(f) < 2:
            continue
        ob, ib = f["OFF_BLOCK"], f["IN_BLOCK"]
        if ts(ob["event_ts"]) - ts(ob["std_utc"]) <= timedelta(minutes=0):
            dep_ok += 1
            arr_ok += ts(ib["event_ts"]) - ts(ib["sta_utc"]) <= timedelta(minutes=15)
    assert dep_ok > 50
    assert arr_ok / dep_ok >= 0.9
