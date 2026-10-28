# Databricks notebook source
# Classic-compute gold consumer (comparison run): same process_batch as gold_sink.py, separate targets.
# Standard access mode runs Python foreachBatch in a separate process: no dbutils or notebook state inside it.
import os
import sys

GOLD_DIR = os.getcwd()  # workspace files: the notebook's own folder (gold_core, gold_sql, lakebase_writer)
sys.path.insert(0, GOLD_DIR)

for name, default in [("uc_schema", ""), ("replica_schema", ""), ("endpoint", ""), ("database", ""),
                      ("lakebase_schema", "ops_classic"), ("checkpoint", ""), ("trigger_interval", "0 seconds")]:
    dbutils.widgets.text(name, default)
UC = dbutils.widgets.get("uc_schema")
REPLICA = dbutils.widgets.get("replica_schema") or UC
ENDPOINT = dbutils.widgets.get("endpoint")
PG_DB = dbutils.widgets.get("database")
PG_SCHEMA = dbutils.widgets.get("lakebase_schema")
CHECKPOINT = dbutils.widgets.get("checkpoint")
TRIGGER = dbutils.widgets.get("trigger_interval") or "0 seconds"

missing = [k for k, v in {"uc_schema": UC, "endpoint": ENDPOINT, "database": PG_DB, "checkpoint": CHECKPOINT}.items() if not v]
if missing:
    raise ValueError(f"missing job parameters: {missing}")
if REPLICA == UC or PG_SCHEMA == "ops":
    # guard: the comparison must never write the serverless consumer's targets
    raise ValueError(f"classic consumer must use separate targets (replica={REPLICA}, lakebase_schema={PG_SCHEMA})")

spark.conf.set("spark.sql.session.timeZone", "UTC")

# the foreachBatch process can't use default SDK auth (HOME unreadable); pass the run's own host + token
_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
API_HOST, API_TOKEN = _ctx.apiUrl().get(), _ctx.apiToken().get()

# COMMAND ----------

_state = {}


def gold_classic_batch(df, batch_id):
    # runs in the foreachBatch worker process: import + build clients here, cache across batches
    if GOLD_DIR not in sys.path:
        sys.path.insert(0, GOLD_DIR)
    from gold_core import load_params, process_batch
    from lakebase_writer import LakebaseWriter

    spark_b = df.sparkSession
    spark_b.conf.set("spark.sql.session.timeZone", "UTC")
    if "w" not in _state:
        # HOME is unreadable on standard access mode: keep the SDK and libpq off the home-dir config/cert/pass files
        for k, v in {"DATABRICKS_CONFIG_FILE": "/tmp/aops-none.cfg", "PGSSLCERT": "/tmp/aops-none.crt",
                     "PGSSLKEY": "/tmp/aops-none.key", "PGPASSFILE": "/tmp/aops-none.pgpass"}.items():
            os.environ.setdefault(k, v)
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient(host=API_HOST, token=API_TOKEN, auth_type="pat")
        host = w.api_client.do("GET", f"/api/2.0/postgres/{ENDPOINT}")["status"]["hosts"]["host"]
        _state["w"] = LakebaseWriter(w, ENDPOINT, host, PG_DB)
        _state["p"] = load_params(spark_b, UC)
    process_batch(spark_b, UC, _state["p"], df, batch_id, _state["w"], replica=REPLICA, lakebase_schema=PG_SCHEMA)


# COMMAND ----------

query = (spark.readStream.table(f"{UC}.bronze_flight_milestones")
         .writeStream.foreachBatch(gold_classic_batch)
         .option("checkpointLocation", CHECKPOINT)
         .queryName("gold_classic")
         .trigger(processingTime=TRIGGER)
         .start())
print(f"gold_classic started: {UC} -> {REPLICA} + lakebase {PG_DB}.{PG_SCHEMA}, trigger={TRIGGER}, checkpoint={CHECKPOINT}")
query.awaitTermination()
