"""Local-only fixture data (MOCK_MODE=true, no forwarded token). Filters exactly like the Lakebase RLS policies."""

import random
from datetime import datetime, timedelta, timezone

MOCK_ROLES = {
    "aops-ops-control": {"label": "Operations Control", "airports": ["*"], "sections": ["overview", "flights", "airport_kpis", "gates", "delays", "connections"], "priority": 1},
    "aops-hub-connections": {"label": "Hub Connections", "airports": ["PTY"], "sections": ["overview", "flights", "connections"], "priority": 2},
    "aops-station-PTY": {"label": "PTY Station", "airports": ["PTY"], "sections": ["overview", "flights", "airport_kpis", "gates", "delays"], "priority": 3},
    "aops-station-BOG": {"label": "BOG Station", "airports": ["BOG"], "sections": ["overview", "flights", "airport_kpis", "gates", "delays"], "priority": 3},
}

HUB = "PTY"
SPOKES = ["BOG", "LIM", "SJO", "MEX", "MIA"]
PHASES = ["SCHEDULED", "BOARDING", "TAXI_OUT", "AIRBORNE", "TAXI_IN", "AT_GATE"]
CAUSES = ["AIRCRAFT", "CREW", "PASSENGER", "EXTERNAL", "GROUND_OPS"]


def _build():
    rng = random.Random(42)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = [now - timedelta(hours=h) for h in range(23, -1, -1)]
    flights, kpis, gates, conns = [], [], [], []
    real_now = datetime.now(timezone.utc)
    iso = lambda t: t.isoformat() if t else None
    for i in range(160):
        spoke = SPOKES[i % len(SPOKES)]
        out = i % 2 == 0
        std = now - timedelta(hours=22) + timedelta(minutes=9 * i)
        delay = rng.choice([-3, 0, 2, 5, 12, 25, 48])
        block = timedelta(minutes=rng.randint(150, 240))
        taxi_out, taxi_in = rng.randint(10, 22), rng.randint(5, 12)
        boarding = std - timedelta(minutes=40)
        aobt = std + timedelta(minutes=delay)
        atot = aobt + timedelta(minutes=taxi_out)
        aibt = aobt + block
        aldt = aibt - timedelta(minutes=taxi_in)
        cancelled = i % 53 == 7
        # phase follows the clock, milestones only up to now
        if cancelled:
            phase = "CANCELLED"
        else:
            phase = "SCHEDULED"
            for name, t in (("BOARDING", boarding), ("TAXI_OUT", aobt), ("AIRBORNE", atot), ("TAXI_IN", aldt), ("AT_GATE", aibt)):
                if t <= real_now:
                    phase = name
        seen = lambda t: t if not cancelled and t <= real_now else None
        arr_delay = int((aibt - (std + block)).total_seconds() // 60) + rng.randint(-6, 4) if seen(aibt) else None
        turn = rng.randint(35, 70) if seen(aibt) and rng.random() < 0.7 else None
        flights.append({
            "flight_id": f"AL{1000 + i}-{std:%Y%m%d}-{HUB if out else spoke}", "flight_number": str(1000 + i),
            "origin_iata": HUB if out else spoke, "destination_iata": spoke if out else HUB,
            "tail_number": f"HP-{1820 + i % 24}CMP", "phase": phase,
            "std_utc": iso(std), "sta_utc": iso(std + block),
            "gate_dep": f"A{i % 14 + 1:02d}" if out else None, "gate_arr": None if out else f"A{(i * 5) % 14 + 1:02d}",
            "boarding_start_ts": iso(seen(boarding)), "aobt": iso(seen(aobt)), "atot": iso(seen(atot)),
            "aldt": iso(seen(aldt)), "aibt": iso(seen(aibt)),
            "dep_delay_min": delay if seen(aobt) else None, "arr_delay_min": arr_delay,
            "delay_cause": rng.choice(CAUSES) if delay > 10 and seen(aobt) else None,
            "taxi_out_min": taxi_out if seen(atot) else None, "taxi_in_min": taxi_in if seen(aibt) else None,
            "turnaround_min": turn, "turnaround_ok": None if turn is None else turn <= 60,
            "last_event_ts": iso(max([t for t in (boarding, aobt, atot, aldt, aibt) if seen(t)], default=None)),
        })
    for ap in [HUB] + SPOKES:
        for h in hours:
            dep = rng.randint(6, 14) if ap == HUB else rng.randint(1, 4)
            kpis.append({"airport_iata": ap, "hour_start_utc": h.isoformat(), "dep_scheduled": dep, "departed": max(0, dep - rng.randint(0, 1)),
                         "arr_scheduled": dep, "arrived": max(0, dep - rng.randint(0, 1)),
                         "dep_otp_d0": rng.uniform(0.55, 0.85), "dep_otp_d15": rng.uniform(0.75, 0.95),
                         "arr_otp_d0": rng.uniform(0.55, 0.85), "arr_otp_d15": rng.uniform(0.75, 0.95),
                         "avg_taxi_out_min": rng.uniform(10, 22), "avg_taxi_in_min": rng.uniform(5, 12),
                         "cancellations": rng.choice([0, 0, 0, 1]), "diversions": rng.choice([0, 0, 0, 0, 1])})
    for g in range(1, 15):
        for h in hours:
            if rng.random() < 0.7:
                gates.append({"airport_iata": HUB, "gate_id": f"A{g:02d}", "hour_start_utc": h.isoformat(),
                              "occupied_minutes": rng.randint(10, 60), "flights": rng.randint(1, 2)})
    for h in hours:
        n = rng.randint(20, 90)
        risk, missed = rng.randint(0, 6), rng.randint(0, 2)
        conns.append({"hub_iata": HUB, "hour_start_utc": h.isoformat(), "connections": n, "pax_connecting": n * 14,
                      "connections_at_risk": risk, "pax_at_risk": risk * 12, "connections_missed": missed, "pax_missed": missed * 9})
    return flights, kpis, gates, conns


def _visible(row, airports, cols):
    return "*" in airports or any(row.get(c) in airports for c in cols)


def rows(endpoint, role_airports, airport=None, hours=24):
    flights, kpis, gates, conns = _build()
    ap = [airport] if airport else role_airports
    if endpoint == "flights":
        return [f for f in flights if _visible(f, ap, ["origin_iata", "destination_iata"])][::-1]
    if endpoint == "gates":
        return [g for g in gates if _visible(g, ap, ["airport_iata"])]
    if endpoint == "connections":
        return [c for c in conns if _visible(c, ap, ["hub_iata"])][::-1]
    k = [r for r in kpis if _visible(r, ap, ["airport_iata"])]
    if endpoint == "hourly":
        by = {}
        for r in k:
            b = by.setdefault(r["hour_start_utc"], {"hour_start_utc": r["hour_start_utc"], "dep_scheduled": 0, "departed": 0, "arr_scheduled": 0, "arrived": 0})
            for c in ("dep_scheduled", "departed", "arr_scheduled", "arrived"):
                b[c] += r[c]
        return sorted(by.values(), key=lambda r: r["hour_start_utc"])
    if endpoint == "kpis":
        out = {}
        for r in k:
            o = out.setdefault(r["airport_iata"], {"airport_iata": r["airport_iata"], "n": 0, "departed": 0, "arrived": 0, "cancellations": 0, "diversions": 0,
                                                   "dep_otp_d0": 0, "dep_otp_d15": 0, "arr_otp_d0": 0, "arr_otp_d15": 0, "avg_taxi_out": 0, "avg_taxi_in": 0})
            o["n"] += 1
            for c in ("departed", "arrived", "cancellations", "diversions"):
                o[c] += r[c]
            for c, s in (("dep_otp_d0", "dep_otp_d0"), ("dep_otp_d15", "dep_otp_d15"), ("arr_otp_d0", "arr_otp_d0"),
                         ("arr_otp_d15", "arr_otp_d15"), ("avg_taxi_out", "avg_taxi_out_min"), ("avg_taxi_in", "avg_taxi_in_min")):
                o[c] += r[s]
        for o in out.values():
            for c in ("dep_otp_d0", "dep_otp_d15", "arr_otp_d0", "arr_otp_d15", "avg_taxi_out", "avg_taxi_in"):
                o[c] /= o["n"]
            del o["n"]
        return sorted(out.values(), key=lambda r: r["airport_iata"])
    if endpoint == "delays":
        agg = {}
        for f in flights:
            if f["delay_cause"] and _visible(f, ap, ["origin_iata"]):
                a = agg.setdefault(f["delay_cause"], {"delay_cause": f["delay_cause"], "total_delay_minutes": 0, "flights": 0})
                a["total_delay_minutes"] += max(0, f["dep_delay_min"] or 0)
                a["flights"] += 1
        return sorted(agg.values(), key=lambda r: -r["total_delay_minutes"])
    return []


def genie_answer(question: str) -> dict:
    # canned Genie reply for local runs
    return {"status": "COMPLETED",
            "answer": f"**Mock answer** for: {question}\n\n- PTY: **81.2%** OTP D15 on **96** departures\n- BOG: **74.0%** on **25** departures",
            "sql": "SELECT airport_iata, MEASURE(dep_otp_d15) FROM genie_airport_metrics GROUP BY ALL",
            "columns": ["airport_iata", "departures", "dep_otp_d15_pct"],
            "rows": [["PTY", "96", "81.2"], ["BOG", "25", "33.285714285714285"], ["1621", "4", "1234.5678"]], "row_count": 3,
            "suggestions": ["Which airport has the most cancellations today?", "Show taxi-out by hour at PTY"],
            "error": None, "viz_attachment_id": "e" * 32}


# 1x1 transparent PNG; the real chart comes from Genie
VIZ_PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
