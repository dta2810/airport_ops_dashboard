"""Live emitter: pure helpers + the notebook run end to end against fake dbutils/spark."""

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "src/simulator")
from ops_sim import OpsSimulator, live_file_name, live_simulator, live_window_start, plan_live, to_json_line

UTC = timezone.utc
CFG = "config/demo.yaml"
NB = Path("src/simulator/run_live.py")


def _window(start, days=2, flight_num_start=1000, stop_before=None):
    sim = live_simulator(CFG, start)
    sched, fmap = sim.build_schedule(days, sim.build_aircraft(), sim.build_airports(),
                                     flight_num_start=flight_num_start, stop_before=stop_before)
    gen_now = datetime.now(UTC)
    return sim, sched, plan_live(sim.generate_events(days, fmap), gen_now)


def _backfill(b0=datetime(2026, 10, 9, tzinfo=UTC), days=7):
    back = OpsSimulator(CFG, backfill_start_utc=b0)
    bs, bmap = back.build_schedule(days, back.build_aircraft(), back.build_airports())
    events = back.generate_events(days, bmap)
    return back, bs, events


def _live_after(bs, events, hhmm="05:30"):
    # mirrors run_live: bronze max(event_ts), max(sta_utc), max(flight_number)
    min_ground = OpsSimulator(CFG).cfg["fleet"]["min_ground_time_min"]
    last = max(e["event_ts"] for e in events)
    ready = max(e["sta_utc"] for e in events) + timedelta(minutes=min_ground)
    start = live_window_start(last, hhmm, ready)
    num = max(int(e["flight_number"]) for e in events) + 1
    return last, start, _window(start, 3, num, start + timedelta(hours=60))


def test_window_starts_at_hhmm_on_last_event_day():
    assert live_window_start(datetime(2026, 10, 19, 2, 11, tzinfo=UTC)) == datetime(2026, 10, 19, 5, 30, tzinfo=UTC)
    assert live_window_start(datetime(2026, 10, 19, 2, 11, tzinfo=UTC), "04:00") == datetime(2026, 10, 19, 4, tzinfo=UTC)
    assert live_window_start(datetime(2026, 10, 19, 2, 11)) == datetime(2026, 10, 19, 5, 30, tzinfo=UTC)  # naive = UTC


def test_window_never_before_last_event_or_tails_ready():
    # HH:MM already past on that day: earliest whole minute after the data, same day
    assert live_window_start(datetime(2026, 10, 18, 23, 4, 10, tzinfo=UTC)) == datetime(2026, 10, 18, 23, 5, tzinfo=UTC)
    ready = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    assert live_window_start(datetime(2026, 10, 19, 2, 11, tzinfo=UTC), "05:30", ready) == datetime(2026, 10, 19, 6, 1, tzinfo=UTC)


def test_bad_hhmm_rejected():
    for bad in ("5", "25:00", "05:60", "ab:cd", None):
        try:
            live_window_start(datetime(2026, 10, 19, tzinfo=UTC), bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_no_empty_day_between_backfill_and_live():
    _, bs, events = _backfill()
    last, start, (_, ls, _) = _live_after(bs, events)
    assert last <= start < last + timedelta(hours=24)
    assert (start.date() - last.date()).days <= 1
    gap = min(s["std_utc"] for s in ls) - max(s["std_utc"] for s in bs)
    assert timedelta(0) < gap < timedelta(hours=24), gap
    # app "last 24 h" is anchored on the sim clock: backfill departures still in view when live opens
    assert any(start - timedelta(hours=24) < s["std_utc"] <= start for s in bs)


def test_first_live_departure_within_minutes_of_start():
    _, bs, events = _backfill()
    _, start, (_, ls, plan) = _live_after(bs, events)
    assert min(s["std_utc"] for s in ls) - start <= timedelta(minutes=60)
    first_off = min(e["event_ts"] for _, e in plan if e["event_type"] == "OFF_BLOCK")
    assert first_off - start <= timedelta(minutes=90)


def test_live_flights_never_collide_with_backfill():
    back, bs, events = _backfill()
    _, _, (_, ls, plan) = _live_after(bs, events)
    assert not {s["flight_id"] for s in bs} & {s["flight_id"] for s in ls}
    assert not {e["event_id"] for e in events} & {e["event_id"] for _, e in plan}


def test_no_tail_overlap_across_boundary():
    _, bs, events = _backfill()
    _, _, (sim, ls, _) = _live_after(bs, events)
    min_ground = sim.cfg["fleet"]["min_ground_time_min"]
    by_tail = {}
    for f in bs + ls:
        by_tail.setdefault(f["tail_number"], []).append(f)
    bad = []
    for flights in by_tail.values():
        flights.sort(key=lambda f: f["std_utc"])
        for a, b in zip(flights, flights[1:]):
            if b["std_utc"] < a["sta_utc"] + timedelta(minutes=min_ground) or b["origin_iata"] != a["destination_iata"]:
                bad.append((a["flight_id"], b["flight_id"]))
    assert not bad, f"{len(bad)} overlapping rotations across the boundary, e.g. {bad[:3]}"


def test_stop_before_keeps_whole_rotations():
    start = datetime(2026, 10, 19, 5, 30, tzinfo=UTC)
    end = start + timedelta(hours=6)
    _, ls, _ = _window(start, 2, 3000, end)
    hub = OpsSimulator(CFG).cfg["network"]["hub"]
    outs = [s for s in ls if s["origin_iata"] == hub]
    assert outs and all(s["std_utc"] < end for s in outs)
    assert len(ls) == 2 * len(outs)
    assert min(int(s["flight_number"]) for s in ls) == 3000


def test_plan_is_ordered_and_late_copies_emit_later():
    _, _, plan = _window(datetime(2026, 10, 20, tzinfo=UTC))
    times = [t for t, _ in plan]
    assert times == sorted(times)
    assert any(t - e["event_ts"] >= timedelta(minutes=10) for t, e in plan)  # late arrivals


def test_file_names_unique_and_json_lines_parse():
    assert len({live_file_name("r1", i) for i in range(1000)}) == 1000
    _, _, plan = _window(datetime(2026, 10, 20, tzinfo=UTC))
    row = json.loads(to_json_line(plan[0][1], datetime(2026, 10, 20, 0, 0, 1, tzinfo=UTC)))
    assert row["emitted_ts"] == "2026-10-20T00:00:01+00:00" and row["event_date"] == row["event_ts"][:10]


class _DF:
    def __init__(self, rows): self.rows = rows
    def join(self, other, keys, how):
        seen = {tuple(r[k] for k in keys) for r in other.rows}
        return _DF([r for r in self.rows if tuple(r[k] for k in keys) not in seen])
    def select(self, *cols): return _DF([{c: r[c] for c in cols} for r in self.rows])
    def count(self): return len(self.rows)
    @property
    def write(self): return self
    def format(self, f): return self
    def mode(self, m): return self
    def saveAsTable(self, name): _FakeSpark.tables.setdefault(name.split(".")[-1], []).extend(self.rows)


class _FakeSpark:
    tables = {}
    def __init__(self, max_ts, max_sta=None, max_num=None): self.row = [max_ts, max_sta, max_num]
    def sql(self, q): return types.SimpleNamespace(first=lambda: self.row)
    def createDataFrame(self, rows): return _DF(rows)
    def table(self, name): return _DF(self.tables.get(name.split(".")[-1], []))


def test_notebook_end_to_end(monkeypatch, tmp_path):
    files = {}
    widgets = {"landing_path": "/Volumes/c/s/landing", "dims_schema": "c.s", "config_path": CFG, "duration_min": "12"}
    dbutils = types.SimpleNamespace(
        widgets=types.SimpleNamespace(get=widgets.__getitem__, text=widgets.setdefault),
        fs=types.SimpleNamespace(mkdirs=lambda p: None,
                                 put=lambda p, c, overwrite=False: files.__setitem__(p, c) if p not in files else (_ for _ in ()).throw(AssertionError(f"overwrite {p}")))) 
    clock = {"t": 1_000_000.0}
    import time
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    monkeypatch.setattr(time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    _FakeSpark.tables = {"dim_schedule": [], "dim_connection": []}
    spark = _FakeSpark(datetime(2026, 10, 19, 2, 11), datetime(2026, 10, 19, 1, 40), 2470)  # naive, like Spark
    g = {"dbutils": dbutils, "spark": spark, "__name__": "nb"}
    exec(compile(NB.read_text(), str(NB), "exec"), g)
    rows = [json.loads(l) for c in files.values() for l in c.splitlines()]
    assert files and all("/milestones/live-" in p for p in files)
    assert rows and widgets["live_start_hhmm"] == "05:30"
    # 12 wall min = 12 sim h from 05:30: departures within the first wall hour-of-sim, nothing past the window
    assert any(r["event_type"] == "OFF_BLOCK" and r["event_ts"] < "2026-10-19T06:30" for r in rows)
    assert max(r["event_ts"] for r in rows) < "2026-10-19T17:31"
    assert min(int(s["flight_number"]) for s in _FakeSpark.tables["dim_schedule"]) >= 2471
    assert all(s["std_utc"] >= datetime(2026, 10, 19, 5, 30, tzinfo=UTC) for s in _FakeSpark.tables["dim_schedule"])
    assert {r["flight_id"] for r in rows} <= {s["flight_id"] for s in _FakeSpark.tables["dim_schedule"]}
    assert _FakeSpark.tables["dim_connection"]
