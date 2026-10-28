#!/usr/bin/env python3
"""
Ops simulator: generates realistic A-CDM milestone events for airport operations.
Deterministic with seed; matches the data contract in docs/data-model.md exactly.
"""

import random
import uuid
import yaml
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Any, Optional

UTC = timezone.utc


class OpsSimulator:
    """Generate dimensions and milestone events for aeroLatinos hub-and-spoke network."""

    def __init__(self, config_path: str, backfill_start_utc: Optional[datetime] = None):
        """Load config and initialize the simulator.

        Args:
            config_path: Path to demo.yaml
            backfill_start_utc: Explicit backfill window start (UTC). If None, uses today - backfill_days.
        """
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.seed = self.cfg["simulation"]["seed"]
        self.rng = random.Random(self.seed)

        # Set backfill window anchor
        if backfill_start_utc is None:
            now_utc = datetime.now(UTC)
            backfill_days = self.cfg["simulation"]["backfill_days"]
            self.backfill_start_utc = (now_utc - timedelta(days=backfill_days)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            self.backfill_start_utc = backfill_start_utc

        self._event_counter = 0  # For deterministic event_id generation

    def _next_event_id(self) -> str:
        """Generate deterministic event_id from seeded RNG."""
        bits = self.rng.getrandbits(128)
        return str(uuid.UUID(int=bits, version=4))

    def build_airports(self) -> List[Dict[str, Any]]:
        """Build dim_airport from config network topology."""
        hub = self.cfg["network"]["hub"]
        spokes = self.cfg["network"]["spokes"]
        airports = []

        # Hub
        airports.append({
            "airport_iata": hub,
            "name": {"PTY": "Tocumen Internacional"}[hub],
            "city": {"PTY": "Panama City"}[hub],
            "country": {"PTY": "PA"}[hub],
            "tz": {"PTY": "America/Panama"}[hub],
            "is_hub": True,
            "lat": {"PTY": 8.9824}[hub],
            "lon": {"PTY": -79.5547}[hub],
        })

        # Spokes with realistic coordinates
        spoke_data = {
            "BOG": ("El Dorado", "Bogotá", "CO", "America/Bogota", 4.7213, -74.1497),
            "LIM": ("Jorge Chávez", "Lima", "PE", "America/Lima", -12.0219, -77.1090),
            "SJO": ("Juan Santamaría", "San José", "CR", "America/Costa_Rica", 9.9865, -84.2086),
            "GUA": ("La Aurora", "Guatemala City", "GT", "America/Guatemala", 14.5835, -90.5269),
            "SAL": ("Óscar Arnulfo Romero", "San Salvador", "SV", "America/El_Salvador", 13.4429, -89.1176),
            "MEX": ("Benito Juárez", "Mexico City", "MX", "America/Mexico_City", 19.4361, -99.0720),
            "MIA": ("Miami International", "Miami", "US", "America/New_York", 25.7959, -80.2870),
            "SDQ": ("Las Américas", "Santo Domingo", "DO", "America/Santo_Domingo", 18.5591, -69.9297),
            "UIO": ("Mariscal Sucre", "Quito", "EC", "America/Guayaquil", -0.2176, -78.4891),
            "CUN": ("Cancún", "Cancún", "MX", "America/Cancun", 20.6364, -87.0731),
            "MDE": ("José María Córdova", "Medellín", "CO", "America/Bogota", 6.1643, -75.4234),
            "SCL": ("Arturo Merino Benítez", "Santiago", "CL", "America/Santiago", -33.3930, -70.7858),
        }

        for code in spokes:
            if code in spoke_data:
                name, city, country, tz, lat, lon = spoke_data[code]
                airports.append({
                    "airport_iata": code,
                    "name": name,
                    "city": city,
                    "country": country,
                    "tz": tz,
                    "is_hub": False,
                    "lat": lat,
                    "lon": lon,
                })

        return airports

    def build_gates(self, airports: List[Dict]) -> List[Dict[str, Any]]:
        """Build dim_gate from airports."""
        gates = []
        for airport in airports:
            if not airport["is_hub"]:
                continue  # Gates only at hub
            iata = airport["airport_iata"]
            # 2 terminals, 15 gates each
            for term_num in [1, 2]:
                for gate_num in range(1, 16):
                    gate_id = f"{chr(64 + gate_num)}{gate_num:02d}"
                    gates.append({
                        "airport_iata": iata,
                        "gate_id": gate_id,
                        "terminal": f"T{term_num}",
                    })
        return gates

    def build_aircraft(self) -> List[Dict[str, Any]]:
        """Build dim_aircraft: all B737-800."""
        aircraft = []
        fleet_size = self.cfg["fleet"]["size"]
        seats = 160  # B737-800 capacity
        for i in range(1, fleet_size + 1):
            tail = f"HP-{i:04d}AL"
            aircraft.append({
                "tail_number": tail,
                "aircraft_type": self.cfg["fleet"]["aircraft_type"],
                "seats": seats,
            })
        return aircraft

    def build_routes(self, airports: List[Dict]) -> List[Dict[str, Any]]:
        """Build dim_route: realistic block times between hub and spokes."""
        routes = []
        hub = self.cfg["network"]["hub"]
        spokes = self.cfg["network"]["spokes"]

        # Block times in minutes (realistic for regional flights)
        block_times = {
            ("PTY", "BOG"): 180, ("BOG", "PTY"): 180,
            ("PTY", "LIM"): 240, ("LIM", "PTY"): 240,
            ("PTY", "SJO"): 90, ("SJO", "PTY"): 90,
            ("PTY", "GUA"): 120, ("GUA", "PTY"): 120,
            ("PTY", "SAL"): 110, ("SAL", "PTY"): 110,
            ("PTY", "MEX"): 240, ("MEX", "PTY"): 240,
            ("PTY", "MIA"): 180, ("MIA", "PTY"): 180,
            ("PTY", "SDQ"): 120, ("SDQ", "PTY"): 120,
            ("PTY", "UIO"): 210, ("UIO", "PTY"): 210,
            ("PTY", "CUN"): 200, ("CUN", "PTY"): 200,
            ("PTY", "MDE"): 150, ("MDE", "PTY"): 150,
            ("PTY", "SCL"): 420, ("SCL", "PTY"): 420,
        }

        for (orig, dest), block_min in block_times.items():
            distance_km = int(block_min * 8)  # Rough approximation: ~8 km/min cruise
            routes.append({
                "origin_iata": orig,
                "destination_iata": dest,
                "block_minutes": block_min,
                "distance_km": distance_km,
            })

        return routes

    def build_schedule(self, backfill_days: int, aircraft: List[Dict],
                      airports: List[Dict], flight_num_start: int = 1000,
                      stop_before: Optional[datetime] = None) -> Tuple[List[Dict], Dict]:
        """
        Build dim_schedule with hub-and-spoke banks.
        Returns (schedule rows, flight_id->schedule dict for event generation).
        """
        schedule = []
        flight_map = {}
        hub = self.cfg["network"]["hub"]
        spokes = self.cfg["network"]["spokes"]
        banks_local = self.cfg["network"]["banks_local"]  # ["06:30", "10:30", "15:00", "20:30"]
        aircraft_list = [a["tail_number"] for a in aircraft]
        min_ground_time = self.cfg["fleet"]["min_ground_time_min"]

        flight_num = flight_num_start

        # each tail is back at the hub and ready at ready_at; rotations never overlap
        ready_at = {t: self.backfill_start_utc for t in aircraft_list}

        for day_offset in range(backfill_days):
            day_utc = self.backfill_start_utc + timedelta(days=day_offset)

            for tail in aircraft_list:
                # Each tail does 3-4 rotations per day, banks in time order
                n_rotations = self.rng.randint(3, 4)
                banks = sorted(self.rng.sample(banks_local, min(n_rotations, len(banks_local))))

                for bank_str in banks:
                    bank_h, bank_m = map(int, bank_str.split(":"))

                    # Outbound: hub -> spoke at the bank (+ jitter), not before the tail is ready
                    std_time = max(day_utc.replace(hour=bank_h, minute=bank_m) + timedelta(
                        minutes=self.rng.randint(-30, 30)), ready_at[tail])
                    # live: only rotations that depart inside the emit window (whole rotation kept)
                    if stop_before is not None and std_time >= stop_before:
                        continue
                    dest = self.rng.choice(spokes)

                    # Inbound: spoke -> hub (after min ground time at destination)
                    block_time_out = self._get_block_time(hub, dest)
                    sta_out = std_time + timedelta(minutes=block_time_out)
                    # std_back >= sta_out + min_ground_time
                    std_back = sta_out + timedelta(
                        minutes=min_ground_time + self.rng.randint(0, 15)
                    )

                    # Generate outbound flight
                    flight_num_str = f"{flight_num:04d}"
                    flight_num += 1

                    flight_id_out = f"AL{flight_num_str}-{std_time.strftime('%Y%m%d')}-{hub}"
                    schedule.append({
                        "flight_id": flight_id_out,
                        "flight_number": flight_num_str,
                        "origin_iata": hub,
                        "destination_iata": dest,
                        "tail_number": tail,
                        "std_utc": std_time,
                        "sta_utc": sta_out,
                    })
                    flight_map[flight_id_out] = {
                        "flight_number": flight_num_str,
                        "origin": hub,
                        "destination": dest,
                        "tail": tail,
                        "std_utc": std_time,
                        "sta_utc": sta_out,
                    }

                    # Inbound flight
                    flight_num_str = f"{flight_num:04d}"
                    flight_num += 1
                    flight_id_back = f"AL{flight_num_str}-{std_back.strftime('%Y%m%d')}-{dest}"
                    block_time_back = self._get_block_time(dest, hub)
                    sta_back = std_back + timedelta(minutes=block_time_back)
                    schedule.append({
                        "flight_id": flight_id_back,
                        "flight_number": flight_num_str,
                        "origin_iata": dest,
                        "destination_iata": hub,
                        "tail_number": tail,
                        "std_utc": std_back,
                        "sta_utc": sta_back,
                    })
                    flight_map[flight_id_back] = {
                        "flight_number": flight_num_str,
                        "origin": dest,
                        "destination": hub,
                        "tail": tail,
                        "std_utc": std_back,
                        "sta_utc": sta_back,
                    }
                    ready_at[tail] = sta_back + timedelta(minutes=min_ground_time)

        return schedule, flight_map

    def build_connections(self, schedule: List[Dict]) -> List[Dict[str, Any]]:
        """Build dim_connection: inbound->outbound at hub with passenger counts."""
        connections = []
        hub = self.cfg["network"]["hub"]

        # Group flights by destination hour
        by_dest_hour = {}
        for sched in schedule:
            if sched["destination_iata"] != hub:
                continue
            sta_hour = sched["sta_utc"].replace(minute=0, second=0, microsecond=0)
            key = sta_hour
            by_dest_hour.setdefault(key, []).append(sched)

        # For each inbound arrival, find compatible outbounds
        min_conn_time = self.cfg["kpis"]["min_connection_time_min"]
        for inbound in schedule:
            if inbound["destination_iata"] != hub:
                continue

            inbound_arrival = inbound["sta_utc"]
            outbound_window_start = inbound_arrival + timedelta(minutes=min_conn_time)
            outbound_window_end = inbound_arrival + timedelta(minutes=180)

            for outbound in schedule:
                if outbound["origin_iata"] != hub:
                    continue
                if outbound["flight_id"] == inbound["flight_id"]:
                    continue

                if outbound_window_start <= outbound["std_utc"] <= outbound_window_end:
                    pax = self.rng.randint(10, 80)
                    connections.append({
                        "inbound_flight_id": inbound["flight_id"],
                        "outbound_flight_id": outbound["flight_id"],
                        "pax": pax,
                    })

        return connections

    def generate_events(self, backfill_days: int, flight_map: Dict) -> List[Dict[str, Any]]:
        """
        Generate milestone events matching the data-model contract exactly.
        Includes realistic delays, cancellations, diversions, and some late/dup/OOO events.
        """
        events = []
        hub = self.cfg["network"]["hub"]
        now_utc = datetime.now(UTC)
        backfill_start = (now_utc - timedelta(days=backfill_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        min_ground = self.cfg["fleet"]["min_ground_time_min"]
        otp_threshold = self.cfg["kpis"]["otp_threshold_min"]

        for flight_id, sched in flight_map.items():
            std = sched["std_utc"]
            sta = sched["sta_utc"]

            # Decide on outcome: on-time (70%), minor delay (15%), major delay (10%), cancel (3%), divert (2%)
            outcome = self.rng.choices(
                ["ontime", "minor_delay", "major_delay", "cancelled", "diverted"],
                weights=[0.70, 0.15, 0.10, 0.03, 0.02]
            )[0]

            tail = sched["tail"]
            origin = sched["origin"]
            dest = sched["destination"]
            flight_num = sched["flight_number"]

            # SCHEDULED event
            events.append(self._make_event(
                flight_id=flight_id,
                flight_number=flight_num,
                origin=origin,
                dest=dest,
                tail=tail,
                std=std,
                sta=sta,
                event_type="SCHEDULED",
                event_ts=std - timedelta(hours=24),
                airport=origin,
            ))

            if outcome == "cancelled":
                # CANCELLED event
                events.append(self._make_event(
                    flight_id=flight_id,
                    flight_number=flight_num,
                    origin=origin,
                    dest=dest,
                    tail=tail,
                    std=std,
                    sta=sta,
                    event_type="CANCELLED",
                    event_ts=std - timedelta(minutes=120),
                    airport=origin,
                ))
                continue

            if outcome == "diverted":
                # Divert to random nearby airport
                divert_airport = self.rng.choice(["MIA", "SJO", "BOG"])

                # Go through normal events up to LANDING
                gate_dep = f"A{self.rng.randint(1, 15):02d}"
                events.append(self._make_event(
                    flight_id=flight_id,
                    flight_number=flight_num,
                    origin=origin,
                    dest=dest,
                    tail=tail,
                    std=std,
                    sta=sta,
                    event_type="GATE_ASSIGNED",
                    event_ts=std - timedelta(minutes=90),
                    airport=origin,
                    gate=gate_dep,
                ))

                events.append(self._make_event(
                    flight_id=flight_id,
                    flight_number=flight_num,
                    origin=origin,
                    dest=dest,
                    tail=tail,
                    std=std,
                    sta=sta,
                    event_type="BOARDING_START",
                    event_ts=std - timedelta(minutes=60),
                    airport=origin,
                ))

                events.append(self._make_event(
                    flight_id=flight_id,
                    flight_number=flight_num,
                    origin=origin,
                    dest=dest,
                    tail=tail,
                    std=std,
                    sta=sta,
                    event_type="OFF_BLOCK",
                    event_ts=std + timedelta(minutes=self.rng.randint(-5, 30)),
                    airport=origin,
                ))

                events.append(self._make_event(
                    flight_id=flight_id,
                    flight_number=flight_num,
                    origin=origin,
                    dest=dest,
                    tail=tail,
                    std=std,
                    sta=sta,
                    event_type="TAKEOFF",
                    event_ts=std + timedelta(minutes=self.rng.randint(20, 60)),
                    airport=origin,
                ))

                # DIVERTED event instead of LANDING
                events.append(self._make_event(
                    flight_id=flight_id,
                    flight_number=flight_num,
                    origin=origin,
                    dest=dest,
                    tail=tail,
                    std=std,
                    sta=sta,
                    event_type="DIVERTED",
                    event_ts=sta + timedelta(minutes=self.rng.randint(-30, 60)),
                    airport=dest,
                    diversion=divert_airport,
                ))
                continue

            # Normal flight progression
            delay_minutes = 0
            if outcome == "major_delay":
                delay_minutes = self.rng.randint(60, 240)
            elif outcome == "minor_delay":
                delay_minutes = self.rng.randint(15, 59)

            # GATE_ASSIGNED
            gate_dep = f"A{self.rng.randint(1, 15):02d}"
            events.append(self._make_event(
                flight_id=flight_id,
                flight_number=flight_num,
                origin=origin,
                dest=dest,
                tail=tail,
                std=std,
                sta=sta,
                event_type="GATE_ASSIGNED",
                event_ts=std - timedelta(minutes=90),
                airport=origin,
                gate=gate_dep,
            ))

            # BOARDING_START
            events.append(self._make_event(
                flight_id=flight_id,
                flight_number=flight_num,
                origin=origin,
                dest=dest,
                tail=tail,
                std=std,
                sta=sta,
                event_type="BOARDING_START",
                event_ts=std - timedelta(minutes=60),
                airport=origin,
            ))

            # If delayed, DELAY_REPORTED event
            if delay_minutes > 0:
                cause = self.rng.choice(["AIRCRAFT", "CREW", "PASSENGER", "EXTERNAL", "GROUND_OPS"])
                events.append(self._make_event(
                    flight_id=flight_id,
                    flight_number=flight_num,
                    origin=origin,
                    dest=dest,
                    tail=tail,
                    std=std,
                    sta=sta,
                    event_type="DELAY_REPORTED",
                    event_ts=std - timedelta(minutes=30),
                    airport=origin,
                    delay_minutes=delay_minutes,
                    delay_cause=cause,
                ))

            # OFF_BLOCK (AOBT)
            aobt = std + timedelta(minutes=delay_minutes) + timedelta(minutes=self.rng.randint(-5, 15))
            events.append(self._make_event(
                flight_id=flight_id,
                flight_number=flight_num,
                origin=origin,
                dest=dest,
                tail=tail,
                std=std,
                sta=sta,
                event_type="OFF_BLOCK",
                event_ts=aobt,
                airport=origin,
            ))

            # TAKEOFF (ATOT)
            atot = aobt + timedelta(minutes=self.rng.randint(10, 30))
            events.append(self._make_event(
                flight_id=flight_id,
                flight_number=flight_num,
                origin=origin,
                dest=dest,
                tail=tail,
                std=std,
                sta=sta,
                event_type="TAKEOFF",
                event_ts=atot,
                airport=origin,
            ))

            # LANDING (ALDT)
            # scheduled block is gate to gate: airborne = block minus mean taxi (20 out + 10 in), small schedule buffer
            block_time = self._get_block_time(origin, dest)
            aldt = atot + timedelta(minutes=max(20, block_time - 30 + self.rng.randint(-10, 5)))
            events.append(self._make_event(
                flight_id=flight_id,
                flight_number=flight_num,
                origin=origin,
                dest=dest,
                tail=tail,
                std=std,
                sta=sta,
                event_type="LANDING",
                event_ts=aldt,
                airport=dest,
            ))

            # IN_BLOCK (AIBT)
            gate_arr = f"B{self.rng.randint(1, 15):02d}"
            aibt = aldt + timedelta(minutes=self.rng.randint(5, 15))
            events.append(self._make_event(
                flight_id=flight_id,
                flight_number=flight_num,
                origin=origin,
                dest=dest,
                tail=tail,
                std=std,
                sta=sta,
                event_type="IN_BLOCK",
                event_ts=aibt,
                airport=dest,
                gate=gate_arr,
            ))

        # Add duplicates (same event_id, same event_ts, re-emitted)
        dup_pct = int(len(events) * 0.01)  # 1% duplicates
        for _ in range(dup_pct):
            e = self.rng.choice(events)
            events.append(dict(e))

        # Add out-of-order events (same as originals, will be shuffled in landing files)
        # This is handled by landing file order, not by modifying event_ts/event_id

        # Add late events: same event_id and event_ts, but emitted_ts set later
        # These will be written to a later landing file in the backfill/live stage
        late_pct = int(len(events) * 0.02)  # 2% of events arrive late
        late_events = []
        for _ in range(late_pct):
            orig_event = self.rng.choice(events)
            late_event = dict(orig_event)
            # Set emitted_ts to 10-120 minutes after the original
            late_event["emitted_ts"] = late_event["emitted_ts"] + timedelta(
                minutes=self.rng.randint(10, 120)
            )
            late_events.append(late_event)

        events.extend(late_events)
        return events

    def _make_event(self, flight_id: str, flight_number: str, origin: str, dest: str,
                   tail: str, std: datetime, sta: datetime, event_type: str,
                   event_ts: datetime, airport: str, gate: str = None,
                   delay_minutes: int = None, delay_cause: str = None,
                   diversion: str = None) -> Dict[str, Any]:
        """Create a single event record. event_date derived from event_ts per contract."""
        event_date = event_ts.date()
        return {
            "event_id": self._next_event_id(),
            "event_type": event_type,
            "event_ts": event_ts,
            "emitted_ts": datetime.now(UTC),
            "event_date": event_date,
            "airport_iata": airport,
            "flight_id": flight_id,
            "flight_number": flight_number,
            "origin_iata": origin,
            "destination_iata": dest,
            "tail_number": tail,
            "std_utc": std,
            "sta_utc": sta,
            "gate_id": gate,
            "delay_cause": delay_cause,
            "delay_minutes": delay_minutes,
            "diversion_iata": diversion,
        }

    def _get_block_time(self, origin: str, dest: str) -> int:
        """Return block time in minutes for a route."""
        times = {
            ("PTY", "BOG"): 180, ("BOG", "PTY"): 180,
            ("PTY", "LIM"): 240, ("LIM", "PTY"): 240,
            ("PTY", "SJO"): 90, ("SJO", "PTY"): 90,
            ("PTY", "GUA"): 120, ("GUA", "PTY"): 120,
            ("PTY", "SAL"): 110, ("SAL", "PTY"): 110,
            ("PTY", "MEX"): 240, ("MEX", "PTY"): 240,
            ("PTY", "MIA"): 180, ("MIA", "PTY"): 180,
            ("PTY", "SDQ"): 120, ("SDQ", "PTY"): 120,
            ("PTY", "UIO"): 210, ("UIO", "PTY"): 210,
            ("PTY", "CUN"): 200, ("CUN", "PTY"): 200,
            ("PTY", "MDE"): 150, ("MDE", "PTY"): 150,
            ("PTY", "SCL"): 420, ("SCL", "PTY"): 420,
        }
        return times.get((origin, dest), 180)


# ---------- live mode ----------

def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def parse_hhmm(hhmm: str) -> Tuple[int, int]:
    try:
        h, m = (int(x) for x in hhmm.strip().split(":"))
    except (AttributeError, ValueError):
        raise ValueError(f"live_start_hhmm must be HH:MM, got {hhmm!r}")
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"live_start_hhmm out of range: {hhmm!r}")
    return h, m


def live_window_start(max_event_ts: datetime, live_start_hhmm: str = "05:30",
                      tails_ready_utc: Optional[datetime] = None) -> datetime:
    # HH:MM on the last event's UTC day: live follows the backfill with no empty day, first bank minutes away
    h, m = parse_hhmm(live_start_hhmm)
    last = _utc(max_event_ts)
    floor = max(last, _utc(tails_ready_utc)) if tails_ready_utc else last
    start = last.replace(hour=h, minute=m, second=0, microsecond=0)
    # already past (or tails still flying): earliest whole minute after the data and the last arrival
    if start < floor:
        start = floor.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return start


def live_simulator(config_path: str, start: datetime) -> OpsSimulator:
    sim = OpsSimulator(config_path, backfill_start_utc=start)
    sim.rng = random.Random(sim.seed + start.toordinal())  # distinct event_ids per live window
    return sim


def plan_live(events: List[Dict], gen_now: datetime) -> List[Tuple[datetime, Dict]]:
    # (sim time to emit, event) in emit order; late copies keep their extra emitted_ts lag
    plan = []
    for e in events:
        lag = e["emitted_ts"] - gen_now
        plan.append((e["event_ts"] + (lag if lag > timedelta(minutes=1) else timedelta(0)), e))
    plan.sort(key=lambda p: (p[0], p[1]["event_id"]))
    return plan


def live_file_name(run_id: str, seq: int) -> str:
    # one immutable file per flush: Auto Loader never re-reads an overwritten path
    return f"live-{run_id}-{seq:06d}.jsonl"


def to_json_line(e: Dict, emitted_ts: datetime) -> str:
    import json
    out = dict(e, emitted_ts=emitted_ts)
    return json.dumps({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in out.items()})
