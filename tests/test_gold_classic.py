# Classic gold consumer: shared process_batch contract + target routing (replica / lakebase_schema), with fakes (no pyspark).
import ast
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "src" / "pipelines" / "gold"
sys.path.insert(0, str(GOLD))
sys.path.insert(0, str(ROOT / "src" / "setup"))

import gold_core  # noqa: E402
import lakebase_writer  # noqa: E402
from gold_sql import GRAINS  # noqa: E402
from gold_targets import (classic_uc_statements, lakebase_ddl, lakebase_grants,  # noqa: E402
                          replica_statements)

UC = "cat.airport_ops"
CLASSIC = "cat.airport_ops_classic"
T0 = datetime(2026, 1, 15, 10, 0)
T1 = datetime(2026, 1, 15, 12, 0)
PARAMS = {"min_ground_time_min": "40", "min_connection_time_min": "45", "otp_threshold_min": "15", "hub": "PTY"}


class Row(dict):
    def asDict(self):
        return dict(self)


class FakeDF:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *cols):
        return self

    def distinct(self):
        return self

    def where(self, cond):
        return self

    def collect(self):
        return [Row(r) for r in self.rows]

    def createOrReplaceTempView(self, name):
        pass


FLIGHT = {"flight_id": "F1", "origin_iata": "PTY", "destination_iata": "BOG", "std_utc": T0, "sta_utc": T1,
          "gate_dep": "A1", "aobt": None, "last_emitted_ts": T0}


class FakeSpark:
    def __init__(self):
        self.sql_log = []

    def sql(self, q):
        self.sql_log.append(q)
        if "MERGE INTO" in q or "dim_schedule n JOIN" in q or "dim_connection" in q:
            return FakeDF([])
        if "SELECT origin_iata, gate_dep, std_utc, aobt FROM" in q:
            return FakeDF([{"origin_iata": "PTY", "gate_dep": "A2", "std_utc": T0, "aobt": None}])
        if "WHERE ((flight_id) IN" in q:
            return FakeDF([FLIGHT])
        return FakeDF([])


class FakeWriter:
    def __init__(self):
        self.calls = []

    def write_batch(self, **kw):
        self.calls.append(kw)
        return {"skipped": False}


def _run(**kw):
    spark, writer = FakeSpark(), FakeWriter()
    batch = FakeDF([{"flight_id": "F1", "std_utc": T0, "sta_utc": T1, "tail_number": "HP-1"}])
    gold_core.process_batch(spark, UC, PARAMS, batch, 7, writer, **kw)
    return spark.sql_log, writer.calls


def _merge_targets(log):
    return {re.search(r"MERGE INTO (\S+) t", q).group(1) for q in log if "MERGE INTO" in q}


def _old_gate_source(log):
    q = next(q for q in log if "SELECT origin_iata, gate_dep, std_utc, aobt FROM" in q)
    return re.search(r"FROM (\S+)\.gold_flight_state", q).group(1)


def _schemas_read(log, exclude_merge=True):
    # every catalog.schema prefix referenced by the non-MERGE SQL
    return {m for q in log if not (exclude_merge and "MERGE INTO" in q)
            for m in re.findall(r"\b(\w+\.\w+)\.(?:gold_|dim_|bronze_)\w+", q)}


# ---------- defaults reproduce today's serverless targets ----------

def test_defaults_target_uc_and_ops():
    log, calls = _run()
    assert _merge_targets(log) == {f"{UC}.gold_{g}" for g in GRAINS}
    assert _old_gate_source(log) == UC
    assert _schemas_read(log) == {UC}
    assert [c["schema"] for c in calls] == ["ops"] * len(GRAINS)
    assert [c["sink"] for c in calls] == [f"gold_{g}" for g in GRAINS]


def _no_clock(calls):
    return [{k: v for k, v in c.items() if k != "batch_started"} for c in calls]


def test_explicit_defaults_identical_sql():
    (la, ca), (lb, cb) = _run(), _run(replica=UC, lakebase_schema="ops")
    assert la == lb and _no_clock(ca) == _no_clock(cb)


# ---------- classic routing ----------

def test_replica_routes_merge_and_old_gates_only():
    log, calls = _run(replica=CLASSIC, lakebase_schema="ops_classic")
    assert _merge_targets(log) == {f"{CLASSIC}.gold_{g}" for g in GRAINS}
    assert _old_gate_source(log) == CLASSIC
    # views/dims/bronze still read from uc; the only other schema read is the replica (old gates)
    others = [q for q in log if "MERGE INTO" not in q and "SELECT origin_iata, gate_dep" not in q]
    assert _schemas_read(others) == {UC}
    assert {c["schema"] for c in calls} == {"ops_classic"}


def test_classic_keys_and_rows_match_default():
    _, a = _run()
    _, b = _run(replica=CLASSIC, lakebase_schema="ops_classic")
    strip = lambda cs: [{k: v for k, v in c.items() if k not in ("schema", "batch_started")} for c in cs]
    assert strip(a) == strip(b)


def test_empty_batch_writes_nothing():
    spark, writer = FakeSpark(), FakeWriter()
    assert gold_core.process_batch(spark, UC, PARAMS, FakeDF([]), 1, writer, replica=CLASSIC) == {}
    assert spark.sql_log == [] and writer.calls == []


# ---------- LakebaseWriter schema param ----------

class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, params=None):
        self.log.append(q.as_string(None))

    def executemany(self, q, params):
        self.log.append(q.as_string(None))

    def fetchone(self):
        return None


class FakeConn(FakeCursor):
    def transaction(self):
        return self

    def cursor(self):
        return FakeCursor(self.log)


class FakeWS:
    class current_user:
        @staticmethod
        def me():
            return type("U", (), {"user_name": "sp-app-id"})()

    class api_client:
        @staticmethod
        def do(method, path, body=None):
            return {"token": "t"}


@pytest.mark.parametrize("schema", ["ops", "ops_classic"])
def test_writer_schema_routes_every_statement(monkeypatch, schema):
    log = []
    monkeypatch.setattr(lakebase_writer.psycopg, "connect", lambda **kw: FakeConn(log))
    w = lakebase_writer.LakebaseWriter(FakeWS(), "projects/p/branches/b/endpoints/e", "h", "db")
    kw = {} if schema == "ops" else {"schema": schema}
    res = w.write_batch("gold_flight_state", 1, "flight_state", ["flight_id"], ["flight_id", "last_emitted_ts", "batch_id"],
                        [("F1",), ("F2",)], [{"flight_id": "F1", "last_emitted_ts": T0, "batch_id": 1}], **kw)
    assert res["upserted"] == 1 and res["deleted"] == 1
    refs = {m for q in log for m in re.findall(r'"(\w+)"\."\w+"', q)}
    assert refs == {schema}
    assert {t for q in log for t in re.findall(r'"\w+"\."(\w+)"', q)} == {"flight_state", "batch_control", "batch_log", "token_log"}


# ---------- entry-point contract ----------

def _tree(name):
    return ast.parse((GOLD / name).read_text())


def _imports(tree, module):
    return {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == module for a in n.names}


def _calls(tree, fn):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == fn]


@pytest.mark.parametrize("entry", ["gold_sink.py", "gold_stream_classic.py"])
def test_entry_points_share_process_batch_and_writer(entry):
    t = _tree(entry)
    assert {"load_params", "process_batch"} <= _imports(t, "gold_core")
    assert "LakebaseWriter" in _imports(t, "lakebase_writer")
    assert len(_calls(t, "process_batch")) == 1 and len(_calls(t, "LakebaseWriter")) == 1
    assert not any(n.name == "process_batch" for n in ast.walk(t) if isinstance(n, ast.FunctionDef))


def test_sink_uses_default_targets():
    call = _calls(_tree("gold_sink.py"), "process_batch")[0]
    assert not call.keywords


def test_classic_passes_separate_targets_and_stays_process_safe():
    t = _tree("gold_stream_classic.py")
    call = _calls(t, "process_batch")[0]
    assert {k.arg for k in call.keywords} == {"replica", "lakebase_schema"}
    fn = next(n for n in ast.walk(t) if isinstance(n, ast.FunctionDef) and n.name == "gold_classic_batch")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "dbutils" not in names and "spark" not in names  # session comes from df.sparkSession
    # WorkspaceClient built lazily inside the batch function, not at notebook level
    assert any(isinstance(n, ast.ImportFrom) and n.module == "databricks.sdk" for n in ast.walk(fn))
    assert not any(isinstance(n, ast.ImportFrom) and n.module == "databricks.sdk" for n in t.body)
    assert not {"threading", "concurrent.futures"} & {a.name for n in ast.walk(t) if isinstance(n, ast.Import) for a in n.names}
    src = (GOLD / "gold_stream_classic.py").read_text()
    for needle in ['readStream.table(f"{UC}.bronze_flight_milestones")', ".foreachBatch(gold_classic_batch)",
                   '.option("checkpointLocation", CHECKPOINT)', '.queryName("gold_classic")',
                   ".trigger(processingTime=TRIGGER)", "awaitTermination()"]:
        assert needle in src


# ---------- setup DDL / grants ----------

LB_DDL = (ROOT / "src/sql/lakebase_ddl.sql").read_text()
REPLICAS = (ROOT / "src/sql/delta_replicas.sql").read_text()


def test_lakebase_ddl_default_is_unchanged():
    assert lakebase_ddl(LB_DDL) == LB_DDL == lakebase_ddl(LB_DDL, "ops")


def test_lakebase_ddl_classic_schema():
    out = lakebase_ddl(LB_DDL, "ops_classic")
    assert "CREATE SCHEMA IF NOT EXISTS ops_classic;" in out
    assert not re.search(r"\bops\.", out)
    assert out.count("CREATE TABLE IF NOT EXISTS ops_classic.") == LB_DDL.count("CREATE TABLE IF NOT EXISTS ops.") == 7
    assert "access_policy" not in out  # app access (04) is not part of the classic schema


def test_lakebase_grants_default_matches_previous_statements():
    assert lakebase_grants("ops", "sp") == ['GRANT USAGE ON SCHEMA ops TO "sp"',
                                            'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ops TO "sp"']
    assert lakebase_grants("ops_classic", "sp", create=True)[0] == 'GRANT USAGE, CREATE ON SCHEMA ops_classic TO "sp"'


def test_replica_statements_match_03_logic():
    assert replica_statements(REPLICAS, UC) == [s for s in REPLICAS.format(schema=UC).split(";") if "CREATE TABLE" in s]
    out = replica_statements(REPLICAS, CLASSIC)
    assert len(out) == len(GRAINS) and all(f"{CLASSIC}.gold_" in s for s in out)


def test_classic_uc_statements():
    out = classic_uc_statements("cat", "airport_ops_classic", "sp-1")
    assert out[0] == "CREATE SCHEMA IF NOT EXISTS cat.airport_ops_classic"
    assert out[1] == "CREATE VOLUME IF NOT EXISTS cat.airport_ops_classic.chk"
    assert "MODIFY" in out[2] and "CREATE TABLE" in out[2] and "USE SCHEMA" in out[2]
    assert out[3] == "GRANT READ VOLUME, WRITE VOLUME ON VOLUME cat.airport_ops_classic.chk TO `sp-1`"


@pytest.mark.parametrize("bad", ["ops; DROP", "Ops", "", "a-b"])
def test_schema_names_validated(bad):
    with pytest.raises(ValueError):
        lakebase_ddl(LB_DDL, bad)


def test_principal_rejects_quote_breakout():
    import pytest
    from gold_targets import lakebase_grants, classic_uc_statements, principal
    assert principal("ops.user@example.com") and principal("00000000-1111-2222-3333-444444444444")
    for bad in ['x" TO PUBLIC; --', "x` TO `account users", "", "a b"]:
        with pytest.raises(ValueError):
            lakebase_grants("ops", bad)
        with pytest.raises(ValueError):
            classic_uc_statements("c", "s", bad)
