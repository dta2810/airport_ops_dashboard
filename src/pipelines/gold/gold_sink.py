from pyspark import pipelines as dp

from gold_core import load_params, process_batch
from lakebase_writer import LakebaseWriter

spark.conf.set("spark.sql.session.timeZone", "UTC")
UC = spark.conf.get("airport_ops.uc_schema")
ENDPOINT = spark.conf.get("airport_ops.endpoint")
PG_DB = spark.conf.get("airport_ops.database")

_state = {}


def _writer():
    # created lazily inside the foreachBatch worker process
    if "w" not in _state:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        host = w.api_client.do("GET", f"/api/2.0/postgres/{ENDPOINT}")["status"]["hosts"]["host"]
        _state["w"] = LakebaseWriter(w, ENDPOINT, host, PG_DB)
        _state["p"] = load_params(spark, UC)
    return _state["w"]


@dp.foreach_batch_sink(name="gold_dual_sink")
def gold_dual_sink(df, batch_id):
    w = _writer()
    process_batch(df.sparkSession, UC, _state["p"], df, batch_id, w)


@dp.append_flow(target="gold_dual_sink")
def milestones_flow():
    return spark.readStream.table(f"{UC}.bronze_flight_milestones")
