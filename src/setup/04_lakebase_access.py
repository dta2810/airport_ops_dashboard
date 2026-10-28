# Databricks notebook source
# MAGIC %pip install psycopg
# MAGIC %restart_python

# COMMAND ----------

import os
import sys
from pathlib import Path

import psycopg
import yaml
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.postgres import Role, RoleAuthMethod, RoleIdentityType, RoleRoleSpec

endpoint = dbutils.widgets.get("endpoint")
database = dbutils.widgets.get("database")
sp = dbutils.widgets.get("gold_principal")  # gold SP app id or deployer email
config_path = dbutils.widgets.get("config_path")
access = yaml.safe_load(Path(config_path).read_text())["access"]
sys.path.insert(0, os.getcwd())
from lakebase_access import access_statements  # noqa: E402

w = WorkspaceClient()
branch = endpoint.rsplit("/endpoints/", 1)[0]

# COMMAND ----------

# one Postgres role per Databricks group; never DATABRICKS_SUPERUSER (rolbypassrls defeats RLS)
for r in access["roles"]:
    try:
        w.postgres.create_role(parent=branch, role=Role(spec=RoleRoleSpec(
            postgres_role=r["group"], identity_type=RoleIdentityType.GROUP,
            auth_method=RoleAuthMethod.LAKEBASE_OAUTH_V1)))
        print(f"role created: {r['group']}")
    except Exception as e:
        if "exist" not in str(e).lower():
            raise
        print(f"role exists: {r['group']}")

# COMMAND ----------

host = w.postgres.get_endpoint(name=endpoint).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=endpoint).token
with psycopg.connect(host=host, dbname=database, user=w.current_user.me().user_name, password=token,
                     sslmode="require", autocommit=True) as conn:
    with conn.transaction():  # one tx: readers block on the lock instead of seeing an empty policy table
        for stmt in access_statements(access, database, sp):
            conn.execute(stmt)
print(f"access applied for {len(access['roles'])} roles")
