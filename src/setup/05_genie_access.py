# Databricks notebook source
# Genie access objects from config/demo.yaml: filtered views + metric views + reader grants (src/setup/genie_access.py)
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.getcwd())
from genie_access import grant_statements, view_statements  # noqa: E402

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
config_path = dbutils.widgets.get("config_path")
reader = dbutils.widgets.get("genie_reader")
uc = f"{catalog}.{schema}"

# COMMAND ----------

access = yaml.safe_load(Path(config_path).read_text())["access"]
for stmt in view_statements(access, uc) + grant_statements(uc, reader):
    spark.sql(stmt)
