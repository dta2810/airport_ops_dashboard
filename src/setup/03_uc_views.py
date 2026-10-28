# Databricks notebook source
# Silver/gold views, Delta replicas and gold_params from config/demo.yaml (single source for views + sink)
import os
import sys
from pathlib import Path

import yaml

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
config_path = dbutils.widgets.get("config_path")
sp = dbutils.widgets.get("gold_principal")  # gold SP app id or deployer email
uc = f"{catalog}.{schema}"
spark.conf.set("spark.sql.session.timeZone", "UTC")

sys.path.insert(0, str(Path(os.getcwd()).parent / "pipelines" / "gold"))
sys.path.insert(0, os.getcwd())
from gold_targets import principal  # noqa: E402

principal(sp)
from gold_sql import GRAINS, view_ddl  # noqa: E402

cfg = yaml.safe_load(Path(config_path).read_text())
params = {"min_ground_time_min": cfg["fleet"]["min_ground_time_min"],
          "min_connection_time_min": cfg["kpis"]["min_connection_time_min"],
          "otp_threshold_min": cfg["kpis"]["otp_threshold_min"],
          "hub": cfg["network"]["hub"]}

# COMMAND ----------

spark.createDataFrame([(k, str(v)) for k, v in params.items()], "key STRING, value STRING") \
    .write.mode("overwrite").saveAsTable(f"{uc}.gold_params")

for g in GRAINS:
    spark.sql(view_ddl(g, uc, params))

ddl = (Path(os.getcwd()).parent / "sql" / "delta_replicas.sql").read_text().format(schema=uc)
for stmt in [s for s in ddl.split(";") if "CREATE TABLE" in s]:
    spark.sql(stmt)

# least privilege: the pipeline SP writes the replicas and reads params
for t in [f"gold_{g}" for g in GRAINS]:
    spark.sql(f"GRANT SELECT, MODIFY ON TABLE {uc}.{t} TO `{sp}`")
spark.sql(f"GRANT SELECT ON TABLE {uc}.gold_params TO `{sp}`")
