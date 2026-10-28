# Databricks notebook source
# UC targets of the classic gold consumer: replica schema, gold_* replicas, checkpoint volume, SP grants
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
from gold_targets import classic_uc_statements, replica_statements  # noqa: E402

catalog = dbutils.widgets.get("catalog")
classic_schema = dbutils.widgets.get("classic_schema")
volume = dbutils.widgets.get("checkpoint_volume")
sp = dbutils.widgets.get("gold_principal")  # gold SP app id or deployer email
replica = f"{catalog}.{classic_schema}"

# COMMAND ----------

stmts = classic_uc_statements(catalog, classic_schema, sp, volume)
spark.sql(stmts[0])
spark.sql(stmts[1])
ddl = (Path(os.getcwd()).parent / "sql" / "delta_replicas.sql").read_text()
for stmt in replica_statements(ddl, replica):
    spark.sql(stmt)
for stmt in stmts[2:]:
    spark.sql(stmt)

# COMMAND ----------

display(spark.sql(f"SHOW TABLES IN {replica}"))
display(spark.sql(f"SHOW VOLUMES IN {replica}"))
