# Databricks notebook source
# Generate backfill: dimensions + N days of milestone events → landing.

import sys
import os
import json
from datetime import datetime, timezone, timedelta

# Get parameters from widgets
landing_path = dbutils.widgets.get("landing_path")
dims_schema = dbutils.widgets.get("dims_schema")
config_path = dbutils.widgets.get("config_path")
backfill_start_date_str = dbutils.widgets.get("backfill_start_date")

# Parse backfill_start_date (YYYY-MM-DD format)
backfill_start_utc = None
if backfill_start_date_str:
    backfill_start_utc = datetime.fromisoformat(backfill_start_date_str).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )

# Import the simulator from notebook directory
sys.path.insert(0, os.getcwd())
from ops_sim import OpsSimulator

# Initialize simulator with explicit backfill start
sim = OpsSimulator(config_path, backfill_start_utc=backfill_start_utc)

# Get config values
backfill_days = sim.cfg["simulation"]["backfill_days"]
landing_format = sim.cfg["simulation"]["landing_format"]

print(f"{'='*80}")
print(f"Airport Ops Simulator — Backfill")
print(f"{'='*80}")
print(f"Config: {config_path}")
print(f"Landing path: {landing_path}")
print(f"Dims schema: {dims_schema}")
print(f"Backfill days: {backfill_days}")

# Build dimensions
print(f"\n[1/4] Building dimensions...")
airports = sim.build_airports()
gates = sim.build_gates(airports)
aircraft = sim.build_aircraft()
routes = sim.build_routes(airports)
schedule, flight_map = sim.build_schedule(backfill_days, aircraft, airports)
connections = sim.build_connections(schedule)

print(f"  ✅ Airports: {len(airports)}")
print(f"  ✅ Gates: {len(gates)}")
print(f"  ✅ Aircraft: {len(aircraft)}")
print(f"  ✅ Routes: {len(routes)}")
print(f"  ✅ Schedule: {len(schedule)} flights")
print(f"  ✅ Connections: {len(connections)}")

# Write dimensions to Delta tables
print(f"\n[2/4] Writing dimensions to {dims_schema}...")

# dim_airport
df_airports = spark.createDataFrame(airports)
df_airports.write.format("delta").mode("overwrite").saveAsTable(f"{dims_schema}.dim_airport")
print(f"  ✅ dim_airport written ({len(airports)} rows)")

# dim_gate
df_gates = spark.createDataFrame(gates)
df_gates.write.format("delta").mode("overwrite").saveAsTable(f"{dims_schema}.dim_gate")
print(f"  ✅ dim_gate written ({len(gates)} rows)")

# dim_aircraft
df_aircraft = spark.createDataFrame(aircraft)
df_aircraft.write.format("delta").mode("overwrite").saveAsTable(f"{dims_schema}.dim_aircraft")
print(f"  ✅ dim_aircraft written ({len(aircraft)} rows)")

# dim_route
df_routes = spark.createDataFrame(routes)
df_routes.write.format("delta").mode("overwrite").saveAsTable(f"{dims_schema}.dim_route")
print(f"  ✅ dim_route written ({len(routes)} rows)")

# dim_schedule
df_schedule = spark.createDataFrame(schedule)
df_schedule.write.format("delta").mode("overwrite").saveAsTable(f"{dims_schema}.dim_schedule")
print(f"  ✅ dim_schedule written ({len(schedule)} rows)")

# dim_connection
df_connections = spark.createDataFrame(connections)
df_connections.write.format("delta").mode("overwrite").saveAsTable(f"{dims_schema}.dim_connection")
print(f"  ✅ dim_connection written ({len(connections)} rows)")

# Generate events
print(f"\n[3/4] Generating {backfill_days} days of events...")
events = sim.generate_events(backfill_days, flight_map)
print(f"  ✅ Generated {len(events)} events")

# Write events to landing as JSON lines
print(f"\n[4/4] Writing events to {landing_path}/milestones/...")

# Create the landing directory
milestones_path = f"{landing_path}/milestones"
dbutils.fs.mkdirs(milestones_path)

# Group events by date for better organization
from collections import defaultdict
events_by_date = defaultdict(list)
for event in events:
    date_str = event["event_date"].isoformat()
    events_by_date[date_str].append(event)

# Write one JSON lines file per date
event_count = 0
for date_str in sorted(events_by_date.keys()):
    date_events = events_by_date[date_str]
    file_path = f"{milestones_path}/{date_str}.jsonl"

    # Convert timestamps to ISO format strings for JSON serialization
    content_lines = []
    for event in date_events:
        event_copy = dict(event)
        event_copy["event_ts"] = event_copy["event_ts"].isoformat()
        event_copy["emitted_ts"] = event_copy["emitted_ts"].isoformat()
        event_copy["event_date"] = event_copy["event_date"].isoformat()
        event_copy["std_utc"] = event_copy["std_utc"].isoformat()
        event_copy["sta_utc"] = event_copy["sta_utc"].isoformat()
        content_lines.append(json.dumps(event_copy))

    content = "\n".join(content_lines)
    dbutils.fs.put(file_path, content, overwrite=True)
    event_count += len(date_events)
    print(f"  ✅ {date_str}: {len(date_events)} events → {file_path}")

print(f"\n{'='*80}")
print(f"BACKFILL COMPLETE")
print(f"{'='*80}")
print(f"Total events: {event_count}")
print(f"Flights per day: {len(schedule) / backfill_days:.0f}")
print(f"Events per day: {event_count / backfill_days:.0f}")
