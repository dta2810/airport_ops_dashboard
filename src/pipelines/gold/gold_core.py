import time
from datetime import datetime, timedelta, timezone

from gold_helpers import quote_literal
from gold_sql import GRAINS, grain_sql

KEY_CHUNK = 500
def load_params(spark, uc):
    # written by setup from config/demo.yaml, so views and sink share one source
    return {r["key"]: r["value"] for r in spark.table(f"{uc}.gold_params").collect()}


def _in(cols, keys):
    # literal IN on (cols), chunked; ADR 0002
    keys = sorted(set(keys))
    parts = []
    for i in range(0, len(keys), KEY_CHUNK):
        tup = ", ".join("(" + ", ".join(quote_literal(v) for v in k) + ")" for k in keys[i:i + KEY_CHUNK])
        parts.append(f"({', '.join(cols)}) IN ({tup})")
    return "(" + " OR ".join(parts) + ")" if parts else "FALSE"


def prune_for(dates):
    # flights touching date d have events in [d-2, d+1] (prior tail rotation, overnight arrivals)
    days = sorted({d + timedelta(days=o) for d in dates for o in (-2, -1, 0, 1)})
    return "event_date IN (" + ", ".join(quote_literal(d) for d in days) + ")" if days else "FALSE"


def _utc(v):
    return v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v


def _hour(t):
    return t.replace(minute=0, second=0, microsecond=0)


def _recompute(spark, uc, p, grain, prune, keys):
    return spark.sql(f"SELECT * FROM ({grain_sql(grain, uc, p, prune)}) WHERE {_in(GRAINS[grain], keys)}")


def _old_gates(spark, replica, flight_ids):
    # previous gate assignment from the replica, so a gate change also refreshes the old gate
    q = f"SELECT origin_iata, gate_dep, std_utc, aobt FROM {replica}.gold_flight_state WHERE {_in(['flight_id'], [(f,) for f in flight_ids])}"
    return [r.asDict() for r in spark.sql(q).collect() if r["gate_dep"]]


def _gate_keys(rows):
    keys = set()
    for r in rows:
        h = _hour(r["std_utc"] - timedelta(hours=6))
        end = (r["aobt"] or r["std_utc"]) + timedelta(hours=1)
        while h <= end:
            keys.add((r["origin_iata"], r["gate_dep"], h))
            h += timedelta(hours=1)
    return keys


def touched_keys(batch_flights, flight_rows, conn_rows, old_gate_rows, hub):
    # contract §5: base keys + expansions; supersets are safe (absent keys are deleted)
    keys = {"flight_state": {(r["flight_id"],) for r in flight_rows} | {(f,) for f in batch_flights}}
    keys["airport_hour_kpis"] = ({(r["origin_iata"], _hour(r["std_utc"])) for r in flight_rows}
                                 | {(r["destination_iata"], _hour(r["sta_utc"])) for r in flight_rows})
    keys["gate_hour_occupancy"] = _gate_keys([r for r in flight_rows if r["gate_dep"]] + old_gate_rows)
    keys["hub_connections"] = {(hub, _hour(r["std_utc"])) for r in conn_rows}
    return keys


def _merge_replica(spark, replica, grain, df, keys, batch_id):
    cols = GRAINS[grain]
    src = f"gold_src_{grain}"
    df.createOrReplaceTempView(src)
    on = " AND ".join(f"t.{c} = s.{c}" for c in cols)
    tkeys = _in([f"t.{c}" for c in cols], keys)
    spark.sql(f"""
        MERGE INTO {replica}.gold_{grain} t
        USING (SELECT *, CAST({batch_id} AS BIGINT) AS batch_id, current_timestamp() AS written_at FROM {src}) s
        ON {on}
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        WHEN NOT MATCHED BY SOURCE AND {tkeys} THEN DELETE""")


def process_batch(spark, uc, p, df, batch_id, writer, replica=None, lakebase_schema="ops"):
    # uc: views/dims/params; replica: UC schema of the gold_* Delta replicas; lakebase_schema: Postgres target
    replica = replica or uc
    started = time.time()
    batch = df.select("flight_id", "std_utc", "sta_utc", "tail_number").distinct().collect()
    if not batch:
        return {}
    dates = {r["std_utc"].date() for r in batch} | {r["sta_utc"].date() for r in batch}
    flight_ids = sorted({r["flight_id"] for r in batch})

    # contract §5: an event on a flight also changes the next flight of the same tail (turnaround, gate start)
    pairs = ", ".join(f"({quote_literal(r['tail_number'])}, {quote_literal(r['std_utc'])})" for r in batch)
    nxt = [r.asDict() for r in spark.sql(f"""
        SELECT n.flight_id, n.std_utc, n.sta_utc
        FROM {uc}.dim_schedule n JOIN (VALUES {pairs}) AS t(tail_number, std_utc)
          ON n.tail_number = t.tail_number AND n.std_utc > t.std_utc
        QUALIFY ROW_NUMBER() OVER (PARTITION BY t.tail_number, t.std_utc ORDER BY n.std_utc) = 1""").collect()]
    dates |= {r["std_utc"].date() for r in nxt} | {r["sta_utc"].date() for r in nxt}
    prune = prune_for(dates)
    refreshed = sorted(set(flight_ids) | {r["flight_id"] for r in nxt})

    fs = spark.sql(f"""SELECT * FROM ({grain_sql('flight_state', uc, p, prune)})
                       WHERE {_in(['flight_id'], [(f,) for f in refreshed])}""")
    flight_rows = [r.asDict() for r in fs.collect()]
    old_gates = _old_gates(spark, replica, refreshed)
    fl = [(f,) for f in refreshed]
    conn_rows = [r.asDict() for r in spark.sql(f"""
        SELECT o.std_utc FROM {uc}.dim_connection c
        JOIN {uc}.dim_schedule o ON c.outbound_flight_id = o.flight_id
        WHERE {_in(['c.inbound_flight_id'], fl)} OR {_in(['c.outbound_flight_id'], fl)}""").collect()]
    keys = touched_keys(flight_ids, flight_rows, conn_rows, old_gates, p["hub"])

    stats = {}
    for grain, ks in keys.items():
        ks = sorted(ks)
        rows_df = fs.where(_in(["flight_id"], ks)) if grain == "flight_state" else _recompute(spark, uc, p, grain, prune, ks)
        _merge_replica(spark, replica, grain, rows_df, ks, batch_id)
        rows = [{k: _utc(v) for k, v in r.asDict().items()} for r in rows_df.collect()]
        for r in rows:
            r["batch_id"] = batch_id
        cols = list(rows[0].keys()) if rows else GRAINS[grain] + ["batch_id"]
        stats[grain] = writer.write_batch(sink=f"gold_{grain}", batch_id=batch_id, table=grain,
                                          key_cols=GRAINS[grain], cols=cols,
                                          keys=[tuple(_utc(v) for v in k) for k in ks], rows=rows,
                                          schema=lakebase_schema, batch_started=started)
    print(f"batch {batch_id}: {len(flight_ids)} flights, {sum(len(v) for v in keys.values())} keys, {time.time() - started:.1f}s")
    return stats
