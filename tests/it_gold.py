# Integration test for gold SQL on serverless (Databricks Connect).
# Run: AIRPORT_OPS_IT_PROFILE=<profile> AIRPORT_OPS_IT_CATALOG=<catalog> .venv-dbc/bin/python -m pytest -q -s tests/it_gold.py
# Writes only to SCRATCH and drops it at the end.
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["TZ"] = "UTC"
time.tzset()

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src/pipelines/gold"), str(ROOT / "src/simulator")]
from gold_sql import GRAINS, VIEWS, grain_sql, view_ddl  # noqa: E402
from gold_helpers import quote_literal  # noqa: E402
from ops_sim import OpsSimulator  # noqa: E402

# Live run needs a workspace: AIRPORT_OPS_IT_PROFILE=<cli profile> AIRPORT_OPS_IT_CATALOG=<catalog with the deployed schema>
PROFILE = os.environ.get("AIRPORT_OPS_IT_PROFILE")
CATALOG = os.environ.get("AIRPORT_OPS_IT_CATALOG")
if not (PROFILE and CATALOG):
    pytest.skip("set AIRPORT_OPS_IT_PROFILE and AIRPORT_OPS_IT_CATALOG", allow_module_level=True)

SCRATCH = f"{CATALOG}.airport_ops_scratch"
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
DAYS = 3
CFG = ROOT / "config/demo.yaml"


@pytest.fixture(scope="module")
def sim():
    s = OpsSimulator(str(CFG), backfill_start_utc=START)
    sched, fmap = s.build_schedule(DAYS, s.build_aircraft(), s.build_airports())
    events = s.generate_events(DAYS, fmap)
    conns = s.build_connections(sched)
    params = {"min_ground_time_min": s.cfg["fleet"]["min_ground_time_min"],
              "min_connection_time_min": s.cfg["kpis"]["min_connection_time_min"],
              "otp_threshold_min": s.cfg["kpis"]["otp_threshold_min"],
              "hub": s.cfg["network"]["hub"]}
    return events, conns, params


@pytest.fixture(scope="module")
def spark(sim):
    from databricks.connect import DatabricksSession
    from pyspark.sql import types as T
    sp = DatabricksSession.builder.profile(PROFILE).serverless().getOrCreate()
    sp.conf.set("spark.sql.session.timeZone", "UTC")
    events, conns, params = sim
    sp.sql(f"CREATE SCHEMA IF NOT EXISTS {SCRATCH}")
    try:
        ts, st, it, dt = T.TimestampType(), T.StringType(), T.IntegerType(), T.DateType()
        cols = [("event_id", st), ("event_type", st), ("event_ts", ts), ("emitted_ts", ts), ("event_date", dt),
                ("airport_iata", st), ("flight_id", st), ("flight_number", st), ("origin_iata", st),
                ("destination_iata", st), ("tail_number", st), ("std_utc", ts), ("sta_utc", ts), ("gate_id", st),
                ("delay_cause", st), ("delay_minutes", it), ("diversion_iata", st)]
        schema = T.StructType([T.StructField(n, t) for n, t in cols])
        rows = [tuple(e.get(n) for n, _ in cols) for e in events]
        sp.createDataFrame(rows, schema).write.mode("overwrite").option("overwriteSchema", "true") \
            .clusterBy("event_date", "airport_iata").saveAsTable(f"{SCRATCH}.bronze_flight_milestones")
        ccols = [("inbound_flight_id", st), ("outbound_flight_id", st), ("pax", it)]
        sp.createDataFrame([tuple(c[n] for n, _ in ccols) for c in conns],
                           T.StructType([T.StructField(n, t) for n, t in ccols])) \
            .write.mode("overwrite").saveAsTable(f"{SCRATCH}.dim_connection")
        for g in GRAINS:
            sp.sql(view_ddl(g, SCRATCH, params))
        yield sp
    finally:
        sp.sql(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
        assert sp.sql(f"SHOW SCHEMAS IN {CATALOG} LIKE 'airport_ops_scratch'").count() == 0


# ---------- independent oracle (plain Python over the same events) ----------

def _mins(a, b):
    return None if a is None or b is None else int((b - a).total_seconds() // 60)


def oracle(events, conns, p):
    latest = {}
    for e in events:
        k = (e["flight_id"], e["event_type"])
        cur = latest.get(k)
        rank = (e["event_ts"], e["emitted_ts"], [-ord(c) for c in e["event_id"]])
        if cur is None or rank > cur[0]:
            latest[k] = (rank, e)
    per = defaultdict(dict)
    for (fid, et), (_, e) in latest.items():
        per[fid][et] = e
    fl = {}
    for fid, m in per.items():
        any_e = next(iter(m.values()))
        ts = lambda t: m[t]["event_ts"] if t in m else None
        fl[fid] = dict(flight_id=fid, origin_iata=any_e["origin_iata"], destination_iata=any_e["destination_iata"],
                       tail_number=any_e["tail_number"], std_utc=any_e["std_utc"], sta_utc=any_e["sta_utc"],
                       gate_dep=m.get("GATE_ASSIGNED", {}).get("gate_id"), gate_arr=m.get("IN_BLOCK", {}).get("gate_id"),
                       boarding_start_ts=ts("BOARDING_START"), aobt=ts("OFF_BLOCK"), atot=ts("TAKEOFF"),
                       aldt=ts("LANDING"), aibt=ts("IN_BLOCK"), cancelled="CANCELLED" in m,
                       diversion_iata=m.get("DIVERTED", {}).get("diversion_iata"),
                       delay_cause=m.get("DELAY_REPORTED", {}).get("delay_cause"),
                       delay_minutes=m.get("DELAY_REPORTED", {}).get("delay_minutes"),
                       last_event_ts=max(e["event_ts"] for e in m.values()),
                       last_emitted_ts=max(e["emitted_ts"] for e in m.values()))
    by_tail = defaultdict(list)
    for f in fl.values():
        by_tail[f["tail_number"]].append(f)
    for fs in by_tail.values():
        fs.sort(key=lambda f: f["std_utc"])
        for prev, f in zip([None] + fs[:-1], fs):
            f["prior_aibt"] = prev["aibt"] if prev else None
            f["prior_destination"] = prev["destination_iata"] if prev else None
    for f in fl.values():
        f["phase"] = ("CANCELLED" if f["cancelled"] else "DIVERTED" if f["diversion_iata"] else
                      "AT_GATE" if f["aibt"] else "TAXI_IN" if f["aldt"] else "AIRBORNE" if f["atot"] else
                      "TAXI_OUT" if f["aobt"] else "BOARDING" if f["boarding_start_ts"] else "SCHEDULED")
        f["dep_delay_min"] = _mins(f["std_utc"], f["aobt"])
        f["arr_delay_min"] = _mins(f["sta_utc"], f["aibt"])
        f["taxi_out_min"] = _mins(f["aobt"], f["atot"])
        f["taxi_in_min"] = _mins(f["aldt"], f["aibt"])
        same = f["prior_destination"] == f["origin_iata"]
        f["turnaround_min"] = _mins(f["prior_aibt"], f["aobt"]) if same else None
        f["turnaround_ok"] = (f["turnaround_min"] >= p["min_ground_time_min"]) if f["turnaround_min"] is not None else None

    hour = lambda t: t.replace(minute=0, second=0, microsecond=0)
    ah = defaultdict(lambda: defaultdict(list))
    for f in fl.values():
        ah[(f["origin_iata"], hour(f["std_utc"]))]["dep"].append(f)
        ah[(f["destination_iata"], hour(f["sta_utc"]))]["arr"].append(f)
    airport = {}
    for k, d in ah.items():
        dep, arr = d["dep"], d["arr"]
        dep_c = [f for f in dep if not f["cancelled"] and f["aobt"]]
        arr_c = [f for f in arr if f["aibt"]]
        avg = lambda xs: sum(xs) / len(xs) if xs else None
        airport[k] = dict(dep_scheduled=len(dep), departed=len(dep_c), arr_scheduled=len(arr), arrived=len(arr_c),
                          dep_otp_d0=avg([int(f["dep_delay_min"] <= 0) for f in dep_c]),
                          dep_otp_d15=avg([int(f["dep_delay_min"] <= p["otp_threshold_min"]) for f in dep_c]),
                          arr_otp_d0=avg([int(f["arr_delay_min"] <= 0) for f in arr_c]),
                          arr_otp_d15=avg([int(f["arr_delay_min"] <= p["otp_threshold_min"]) for f in arr_c]),
                          avg_taxi_out_min=avg([f["taxi_out_min"] for f in dep_c if f["taxi_out_min"] is not None]),
                          avg_taxi_in_min=avg([f["taxi_in_min"] for f in arr_c if f["taxi_in_min"] is not None]),
                          delay_minutes=sum(f["delay_minutes"] or 0 for f in dep),
                          cancellations=sum(f["cancelled"] for f in dep),
                          diversions=sum(bool(f["diversion_iata"]) for f in arr))

    now = max(e["event_ts"] for e in events)
    gates = defaultdict(lambda: [0.0, set()])
    for f in fl.values():
        if not f["gate_dep"] or f["cancelled"]:
            continue
        start = f["std_utc"] - timedelta(minutes=60)
        pa = f["prior_aibt"]
        if (f["prior_destination"] == f["origin_iata"] and pa and pa >= f["std_utc"] - timedelta(hours=6)
                and pa < (f["aobt"] or f["std_utc"])):
            start = pa
        end = f["aobt"] or now
        h = hour(start)
        while h < end:
            ov = (min(end, h + timedelta(hours=1)) - max(start, h)).total_seconds() / 60
            if ov > 0:
                g = gates[(f["origin_iata"], f["gate_dep"], h)]
                g[0] += ov
                g[1].add(f["flight_id"])
            h += timedelta(hours=1)
    gate = {k: dict(occupied_minutes=round(v[0]), flights=len(v[1])) for k, v in gates.items()}

    hub, mct = p["hub"], p["min_connection_time_min"]
    hc = defaultdict(lambda: dict(connections=0, pax_connecting=0, connections_at_risk=0, pax_at_risk=0,
                                  connections_missed=0, pax_missed=0))
    for c in conns:
        i, o = fl.get(c["inbound_flight_id"]), fl.get(c["outbound_flight_id"])
        if not i or not o or o["origin_iata"] != hub or i["destination_iata"] != hub:
            continue
        ready = (i["aibt"] or (i["aldt"] + timedelta(minutes=8) if i["aldt"] else None)
                 or (i["sta_utc"] + timedelta(minutes=i["dep_delay_min"]) if i["dep_delay_min"] is not None else None)
                 or i["sta_utc"])
        r = hc[(hub, hour(o["std_utc"]))]
        r["connections"] += 1
        r["pax_connecting"] += c["pax"]
        lim = ready + timedelta(minutes=mct)
        if o["aobt"] is None and lim > o["std_utc"]:
            r["connections_at_risk"] += 1
            r["pax_at_risk"] += c["pax"]
        if o["aobt"] is not None and o["aobt"] < lim:
            r["connections_missed"] += 1
            r["pax_missed"] += c["pax"]
    return {"flight_state": {(k,): v for k, v in fl.items()}, "airport_hour_kpis": airport,
            "gate_hour_occupancy": gate, "hub_connections": dict(hc)}


def _key(row, grain):
    return tuple(row[c].replace(tzinfo=timezone.utc) if isinstance(row[c], datetime) else row[c] for c in GRAINS[grain])


def _norm(v):
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
    if isinstance(v, float):
        return round(v, 9)
    return v


def _rows(df, grain):
    return {_key(r, grain): {k: _norm(v) for k, v in r.asDict().items()} for r in df.collect()}


@pytest.mark.parametrize("grain", list(GRAINS))
def test_views_match_oracle(spark, sim, grain):
    events, conns, p = sim
    want = oracle(events, conns, p)[grain]
    got = _rows(spark.table(f"{SCRATCH}.{VIEWS[grain]}"), grain)
    extra = set(got) - set(want)
    if extra and grain == "gate_hour_occupancy":
        k = sorted(extra)[0]
        from gold_sql import _silver
        body = _silver(SCRATCH, "TRUE").replace("{min_ground_time_min}", "40")
        dbg = spark.sql(f"WITH {body} SELECT flight_id, std_utc, aobt, gate_dep, cancelled, prior_aibt, prior_destination "
                        f"FROM flight_state WHERE origin_iata = '{k[0]}' AND gate_dep = '{k[1]}' AND to_date(std_utc) = DATE'{k[2].date()}'")
        print("\nDEBUG extra", k, got[k], [r.asDict() for r in dbg.collect()])
    assert set(got) == set(want), f"{grain}: key sets differ (missing {list(set(want) - set(got))[:3]}, extra {list(set(got) - set(want))[:3]})"
    bad = []
    for k, w in want.items():
        for col, wv in w.items():
            if col in got[k] and _norm(got[k][col]) != _norm(wv):
                bad.append((k, col, got[k][col], wv))
    assert not bad, f"{grain}: {len(bad)} mismatches, e.g. {bad[:5]}"
    print(f"\n{grain}: {len(got)} rows match oracle")


def _prune(dates):
    days = sorted({d + timedelta(days=o) for d in dates for o in (-2, -1, 0, 1)})
    return "event_date IN (" + ", ".join(quote_literal(d) for d in days) + ")"


def _key_filter(grain, keys):
    cols = GRAINS[grain]
    tuples = ", ".join("(" + ", ".join(quote_literal(v) for v in k) + ")" for k in keys)
    return f"({', '.join(cols)}) IN ({tuples})"


def test_pruned_recompute_equals_full(spark, sim):
    events, conns, p = sim
    o = oracle(events, conns, p)
    day2 = START.date() + timedelta(days=1)
    rnd = random.Random(7)
    flights = rnd.sample([f for (k,), f in o["flight_state"].items() if f["std_utc"].date() == day2], 25)
    dates = {f["std_utc"].date() for f in flights} | {f["sta_utc"].date() for f in flights}
    hour = lambda t: t.replace(minute=0, second=0, microsecond=0)
    keys = {
        "flight_state": [(f["flight_id"],) for f in flights],
        "airport_hour_kpis": sorted({(f["origin_iata"], hour(f["std_utc"])) for f in flights}),
        "gate_hour_occupancy": sorted(k for k in o["gate_hour_occupancy"] if k[2].date() == day2)[:40],
        "hub_connections": sorted(k for k in o["hub_connections"] if k[1].date() == day2)[:10],
    }
    for grain, ks in keys.items():
        flt = _key_filter(grain, ks)
        part = _rows(spark.sql(f"SELECT * FROM ({grain_sql(grain, SCRATCH, p, _prune(dates))}) WHERE {flt}"), grain)
        full = _rows(spark.sql(f"SELECT * FROM {SCRATCH}.{VIEWS[grain]} WHERE {flt}"), grain)
        assert part == full, f"{grain}: pruned recompute differs on {[k for k in full if part.get(k) != full[k]][:3]}"
        print(f"\n{grain}: {len(part)} keys equal pruned vs full")


def test_prune_reaches_bronze_scan(spark, sim):
    _, _, p = sim
    sql = grain_sql("airport_hour_kpis", SCRATCH, p, _prune({START.date() + timedelta(days=1)}))
    plan = "\n".join(r[0] for r in spark.sql(f"EXPLAIN FORMATTED {sql}").collect())
    scan = [l for l in plan.splitlines() if "event_date" in l and ("Filters" in l or "PushedFilters" in l)]
    print("\n" + "\n".join(scan[:4]))
    assert scan, "event_date filter not visible on the bronze scan"


class _FakeWriter:
    # stands in for LakebaseWriter; records the last write per (grain, key)
    def __init__(self):
        self.state = defaultdict(dict)

    def write_batch(self, sink, batch_id, table, key_cols, cols, keys, rows, **_):
        present = {tuple(r[c] for c in key_cols) for r in rows}
        for r in rows:
            self.state[table][tuple(r[c] for c in key_cols)] = r
        for k in keys:
            if tuple(k) not in present:
                self.state[table].pop(tuple(k), None)
        return {"upserted": len(rows)}


def test_incremental_batches_match_full(spark, sim):
    from gold_core import process_batch
    from pyspark.sql import functions as F
    events, conns, p = sim
    inc = f"{SCRATCH}_inc"
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {inc}")
    try:
        # same dims, empty bronze + replicas; bronze grows batch by batch in emitted order
        spark.sql(f"CREATE TABLE {inc}.dim_connection AS SELECT * FROM {SCRATCH}.dim_connection")
        spark.sql(f"""CREATE TABLE {inc}.dim_schedule AS SELECT DISTINCT flight_id, tail_number, std_utc, sta_utc
                      FROM {SCRATCH}.bronze_flight_milestones""")
        spark.sql(f"CREATE TABLE {inc}.bronze_flight_milestones LIKE {SCRATCH}.bronze_flight_milestones")
        ddl = (ROOT / "src/sql/delta_replicas.sql").read_text().format(schema=inc)
        for stmt in [s for s in ddl.split(";") if "CREATE TABLE" in s]:
            spark.sql(stmt)
        src = spark.table(f"{SCRATCH}.bronze_flight_milestones")
        cut = sorted(e["emitted_ts"] for e in events)
        edges = [cut[0] - timedelta(seconds=1)] + [cut[int(len(cut) * q) - 1] for q in (0.6, 0.7, 0.8, 0.9, 1.0)]
        w = _FakeWriter()
        for b, (lo, hi) in enumerate(zip(edges, edges[1:])):
            part = src.where((F.col("emitted_ts") > lo) & (F.col("emitted_ts") <= hi))
            part.write.mode("append").saveAsTable(f"{inc}.bronze_flight_milestones")
            process_batch(spark, inc, p, spark.table(f"{inc}.bronze_flight_milestones")
                          .where((F.col("emitted_ts") > lo) & (F.col("emitted_ts") <= hi)), b, w)
        # replay of the last batch must not change the replica
        before = {g: spark.table(f"{inc}.gold_{g}").drop("written_at", "batch_id").collect() for g in GRAINS}
        process_batch(spark, inc, p, part, b, w)
        for g in GRAINS:
            after = spark.table(f"{inc}.gold_{g}").drop("written_at", "batch_id").collect()
            assert sorted(map(str, after)) == sorted(map(str, before[g])), f"{g}: replay changed the replica"

        horizon = max(e["event_ts"] for e in events) - timedelta(hours=8)
        for g in GRAINS:
            full = _rows(spark.table(f"{SCRATCH}.{VIEWS[g]}"), g)
            rep = {k: {c: v for c, v in r.items() if c not in ("batch_id", "written_at")}
                   for k, r in _rows(spark.table(f"{inc}.gold_{g}"), g).items()}
            lake = {tuple(_norm(v) for v in k): {c: _norm(v) for c, v in r.items() if c != "batch_id"}
                    for k, r in w.state[g].items()}
            if g == "gate_hour_occupancy":
                # open intervals use the ops clock at write time; compare closed hours only
                full = {k: v for k, v in full.items() if k[2] < horizon}
                rep = {k: v for k, v in rep.items() if k[2] < horizon}
                lake = {k: v for k, v in lake.items() if k[2] < horizon}
            assert set(rep) == set(full), f"{g}: replica keys differ: missing {list(set(full) - set(rep))[:3]} extra {list(set(rep) - set(full))[:3]}"
            diff = [k for k in full if rep[k] != full[k]]
            cols = sorted({c for k in diff for c in full[k] if rep[k].get(c) != full[k][c]})
            assert not diff, f"{g}: {len(diff)} replica rows differ in {cols}, e.g. {diff[0]}: " + str({c: (rep[diff[0]].get(c), full[diff[0]][c]) for c in cols})
            assert lake == rep, f"{g}: Lakebase writes differ from replica"
            print(f"\n{g}: incremental replica == full view ({len(full)} rows), Lakebase writes == replica")
    finally:
        spark.sql(f"DROP SCHEMA IF EXISTS {inc} CASCADE")
