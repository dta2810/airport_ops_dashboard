# Databricks notebook source
# MAGIC %pip install psycopg
# MAGIC %restart_python

# COMMAND ----------

import os
import sys
from pathlib import Path

import psycopg
from databricks.sdk import WorkspaceClient

sys.path.insert(0, os.getcwd())
from gold_targets import lakebase_ddl, lakebase_grants  # noqa: E402

endpoint = dbutils.widgets.get("endpoint")
database = dbutils.widgets.get("database")
sp = dbutils.widgets.get("gold_principal")  # gold SP app id or deployer email
app_name = dbutils.widgets.get("app_name")
# ops = serverless consumer (default); another schema = classic comparison targets (no app grants)
dbutils.widgets.text("lakebase_schema", "ops")
pg_schema = dbutils.widgets.get("lakebase_schema") or "ops"

w = WorkspaceClient()
host = w.api_client.do("GET", f"/api/2.0/postgres/{endpoint}")["status"]["hosts"]["host"]
token = w.api_client.do("POST", "/api/2.0/postgres/credentials", body={"endpoint": endpoint})["token"]
user = w.current_user.me().user_name

# COMMAND ----------

ddl = lakebase_ddl(Path("../sql/lakebase_ddl.sql").read_text(), pg_schema)
with psycopg.connect(host=host, dbname=database, user=user, password=token, sslmode="require", autocommit=True) as conn:
    conn.execute(ddl)
    for stmt in lakebase_grants(pg_schema, sp, create=pg_schema != "ops"):
        conn.execute(stmt)
    # TODO(F1): read-only grants for the app SP (resolve client id from app_name)
