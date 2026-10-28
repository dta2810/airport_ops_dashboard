# Databricks notebook source
# Live event emitter on an accelerated clock: a new window after the backfill, same generator (ops_sim).

import math
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import yaml

landing_path = dbutils.widgets.get("landing_path")
dims_schema = dbutils.widgets.get("dims_schema")
config_path = dbutils.widgets.get("config_path")
duration_min = int(dbutils.widgets.get("duration_min"))
dbutils.widgets.text("live_start_hhmm", "05:30")
live_start_hhmm = dbutils.widgets.get("live_start_hhmm") or "05:30"
FLUSH_S = 5

sys.path.insert(0, os.getcwd())
from ops_sim import live_file_name, live_simulator, live_window_start, plan_live, to_json_line

# window right after the landed data: no empty day, no tail still flying, fresh flight numbers
max_ts, max_sta, max_num = spark.sql(
    f"SELECT max(event_ts), max(sta_utc), max(try_cast(flight_number AS INT)) FROM {dims_schema}.bronze_flight_milestones").first()
with open(config_path) as f:
    min_ground = yaml.safe_load(f)["fleet"]["min_ground_time_min"]
tails_ready = max_sta + timedelta(minutes=min_ground) if max_sta else None
start = live_window_start(max_ts or datetime.now(timezone.utc), live_start_hhmm, tails_ready)
sim = live_simulator(config_path, start)
time_scale = sim.cfg["simulation"]["time_scale"]
days = math.ceil(duration_min * time_scale / 1440) + 1
window_end = start + timedelta(minutes=duration_min * time_scale)
print(f"live window {start.isoformat()} → {window_end.isoformat()} · {time_scale}x · {duration_min} wall min")

# schedule + connections for rotations departing in the window; append only new keys (re-runs are idempotent)
airports = sim.build_airports()
schedule, flight_map = sim.build_schedule(days, sim.build_aircraft(), airports,
                                          flight_num_start=(max_num or 999) + 1, stop_before=window_end)
connections = sim.build_connections(schedule)
for table, rows, keys in [("dim_schedule", schedule, ["flight_id"]),
                          ("dim_connection", connections, ["inbound_flight_id", "outbound_flight_id"])]:
    if not rows:
        print(f"{table}: +0 rows")
        continue
    new = spark.createDataFrame(rows).join(spark.table(f"{dims_schema}.{table}").select(*keys), keys, "left_anti")
    new.write.format("delta").mode("append").saveAsTable(f"{dims_schema}.{table}")
    print(f"{table}: +{new.count()} rows")

gen_now = datetime.now(timezone.utc)
plan = plan_live(sim.generate_events(days, flight_map), gen_now)
print(f"events planned: {len(plan)}")

# emit on the accelerated clock; one new file per flush
out_dir = f"{landing_path}/milestones"
dbutils.fs.mkdirs(out_dir)
run_id = uuid.uuid4().hex[:8]
t0, deadline = time.time(), time.time() + duration_min * 60
i = seq = emitted = 0
while i < len(plan) and time.time() < deadline:
    sim_now = start + timedelta(seconds=(time.time() - t0) * time_scale)
    batch = []
    while i < len(plan) and plan[i][0] <= sim_now:
        batch.append(plan[i][1])
        i += 1
    if batch:
        ts = datetime.now(timezone.utc)
        dbutils.fs.put(f"{out_dir}/{live_file_name(run_id, seq)}", "\n".join(to_json_line(e, ts) for e in batch))
        seq, emitted = seq + 1, emitted + len(batch)
    time.sleep(FLUSH_S)

print(f"emitted {emitted} events in {seq} files over {(time.time() - t0) / 60:.1f} min")
