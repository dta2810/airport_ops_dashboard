# Gold target DDL/grants parametrized by schema. Defaults reproduce the serverless consumer's targets (ops, {catalog}.{schema}).
import re

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._%+@-]{0,254}$")  # SP app id or user email; no quotes/backticks


def _ident(name):
    if not _IDENT.match(name or ""):
        raise ValueError(f"invalid schema name: {name!r}")
    return name


def principal(name):
    if not _PRINCIPAL.match(name or ""):
        raise ValueError(f"invalid principal: {name!r}")
    return name


def lakebase_ddl(text, schema="ops"):
    # src/sql/lakebase_ddl.sql is written for schema ops
    _ident(schema)
    return text if schema == "ops" else re.sub(r"\bops\.", f"{schema}.", text).replace(
        "CREATE SCHEMA IF NOT EXISTS ops;", f"CREATE SCHEMA IF NOT EXISTS {schema};")


def lakebase_grants(schema, sp, create=False):
    # gold principal (SP or deployer): DML on the gold + control tables; CREATE only for the classic schema
    _ident(schema)
    principal(sp)
    priv = "USAGE, CREATE" if create else "USAGE"
    return [f'GRANT {priv} ON SCHEMA {schema} TO "{sp}"',
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO "{sp}"']


def replica_statements(text, uc_schema):
    # src/sql/delta_replicas.sql -> CREATE TABLE statements for {uc_schema}.gold_*
    return [s for s in text.format(schema=uc_schema).split(";") if "CREATE TABLE" in s]


def classic_uc_statements(catalog, schema, sp, volume="chk"):
    # classic replica schema + checkpoint volume; the gold principal writes both
    fq = f"{catalog}.{_ident(schema)}"
    principal(sp)
    return [f"CREATE SCHEMA IF NOT EXISTS {fq}",
            f"CREATE VOLUME IF NOT EXISTS {fq}.{_ident(volume)}",
            f"GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY ON SCHEMA {fq} TO `{sp}`",
            f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {fq}.{volume} TO `{sp}`"]
