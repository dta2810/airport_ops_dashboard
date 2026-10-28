# Databricks notebook source
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
volume = dbutils.widgets.get("landing_volume")
import re

sp = dbutils.widgets.get("gold_principal")  # gold SP app id or deployer email
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._%+@-]{0,254}", sp):
    raise ValueError(f"invalid gold_principal: {sp!r}")

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{volume}")

# least privilege for the gold pipeline run-as SP (see docs/security.md)
for stmt in [
    f"GRANT USE CATALOG ON CATALOG {catalog} TO `{sp}`",
    f"GRANT USE SCHEMA, CREATE TABLE ON SCHEMA {catalog}.{schema} TO `{sp}`",
    f"GRANT SELECT ON SCHEMA {catalog}.{schema} TO `{sp}`",
]:
    spark.sql(stmt)

# replica MODIFY grants: 03_uc_views (tables are created there)
