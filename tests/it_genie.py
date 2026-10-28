"""Genie access objects on a live workspace: dynamic views + metric views in a scratch schema over the live gold replicas.
Group membership is simulated per role (array_contains) so every role is tested by one user; the real
is_member() variant is checked too. Run: AIRPORT_OPS_IT_PROFILE=<profile> AIRPORT_OPS_IT_CATALOG=<catalog> .venv-dbc/bin/python -m pytest -q tests/it_genie.py"""
import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src" / "setup"))
from genie_access import METRIC_VIEWS, SCOPE, view_statements  # noqa: E402

# Live run needs a workspace: AIRPORT_OPS_IT_PROFILE=<cli profile> AIRPORT_OPS_IT_CATALOG=<catalog with the deployed schema>
PROFILE = os.environ.get("AIRPORT_OPS_IT_PROFILE")
CATALOG = os.environ.get("AIRPORT_OPS_IT_CATALOG")
if not (PROFILE and CATALOG):
    pytest.skip("set AIRPORT_OPS_IT_PROFILE and AIRPORT_OPS_IT_CATALOG", allow_module_level=True)
SRC = f"{CATALOG}.airport_ops"
SCRATCH = f"{CATALOG}.airport_ops_genie_scratch"
ACCESS = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text())["access"]
ROLES = {r["group"]: r for r in ACCESS["roles"]}


@pytest.fixture(scope="module")
def spark():
    from databricks.connect import DatabricksSession
    sp = DatabricksSession.builder.profile(PROFILE).serverless().getOrCreate()
    sp.conf.set("spark.sql.session.timeZone", "UTC")
    sp.sql(f"CREATE SCHEMA IF NOT EXISTS {SCRATCH}")
    yield sp
    sp.sql(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")


def _build(spark, groups):
    member = "array_contains(array(" + ", ".join(f"'{g}'" for g in groups) + "), {})" if groups else "FALSE AND {} IS NULL"
    for s in view_statements(ACCESS, SCRATCH, member=member, source=SRC):
        spark.sql(s)


def _n(spark, sql):
    return spark.sql(sql).collect()[0][0]


def _expected(spark, groups, table):
    # oracle: rows of the replica allowed by any of the groups, computed in Python from config
    cols = SCOPE[table]
    rows = spark.sql(f"SELECT {', '.join(cols)} FROM {SRC}.gold_{table}").collect()
    allowed = [ROLES[g] for g in groups if any(table in ACCESS["section_tables"][s] for s in ROLES[g]["sections"])]
    ok = lambda r: any("*" in a["airports"] or any(r[c] in a["airports"] for c in cols) for a in allowed)
    return sum(1 for r in rows if ok(r))


CASES = [["aops-ops-control"], ["aops-hub-connections"], ["aops-station-PTY"], ["aops-station-BOG"],
         ["aops-station-BOG", "aops-hub-connections"], []]


@pytest.mark.parametrize("groups", CASES, ids=lambda g: "+".join(g) or "no-group")
def test_rows_and_sections(spark, groups):
    _build(spark, groups)
    for table in SCOPE:
        got = _n(spark, f"SELECT count(*) FROM {SCRATCH}.genie_{table}")
        assert got == _expected(spark, groups, table), (groups, table)
    # metric views inherit the filter
    airports = {r[0] for r in spark.sql(f"SELECT airport_iata FROM {SCRATCH}.genie_airport_metrics GROUP BY ALL").collect()}
    allowed = {a for g in groups for a in ROLES[g]["airports"]
               if any("airport_hour_kpis" in ACCESS["section_tables"][s] for s in ROLES[g]["sections"])}
    if "*" not in allowed:
        assert airports <= allowed, (groups, airports)


def test_bog_sees_no_hub_connections_and_only_bog_flights(spark):
    _build(spark, ["aops-station-BOG"])
    assert _n(spark, f"SELECT count(*) FROM {SCRATCH}.genie_hub_connections") == 0
    assert _n(spark, f"SELECT count(*) FROM {SCRATCH}.genie_flight_state WHERE 'BOG' NOT IN (origin_iata, destination_iata)") == 0
    assert _n(spark, f"SELECT count(*) FROM {SCRATCH}.genie_flight_state") > 0


def test_real_is_member_in_views(spark):
    # the test user belongs to every aops-* workspace group of the workspace
    for s in view_statements(ACCESS, SCRATCH, source=SRC):
        spark.sql(s)
    for table in SCOPE:
        assert _n(spark, f"SELECT count(*) FROM {SCRATCH}.genie_{table}") == _n(spark, f"SELECT count(*) FROM {SRC}.gold_{table}")


def test_metric_measures_match_flight_level(spark):
    _build(spark, ["aops-ops-control"])
    mv = {r[0]: r for r in spark.sql(
        f"SELECT airport_iata, MEASURE(departures), MEASURE(dep_otp_d15), MEASURE(cancellations) "
        f"FROM {SCRATCH}.genie_airport_metrics GROUP BY ALL").collect()}
    # same KPI recomputed from the flight grain (completed departures, delay <= 15 min)
    fl = {r[0]: r for r in spark.sql(f"""
        SELECT origin_iata, count(*) AS dep, avg(CAST(dep_delay_min <= 15 AS INT)) AS otp
        FROM {SRC}.gold_flight_state WHERE aobt IS NOT NULL AND phase <> 'CANCELLED' GROUP BY ALL""").collect()}
    assert set(mv) == set(fl) or set(fl) <= set(mv)
    for a, r in fl.items():
        assert mv[a][1] == r[1], (a, mv[a][1], r[1])
        assert abs(mv[a][2] - r[2]) < 1e-9, (a, mv[a][2], r[2])
    conn = spark.sql(f"SELECT MEASURE(connections), MEASURE(connections_missed), MEASURE(connection_success_rate) "
                     f"FROM {SCRATCH}.genie_connection_metrics").collect()[0]
    assert abs(conn[2] - (1 - conn[1] / conn[0])) < 1e-9


def test_technical_columns_hidden_and_comments_set(spark):
    _build(spark, ["aops-ops-control"])
    cols = {r.col_name: r.comment for r in spark.sql(f"DESCRIBE TABLE {SCRATCH}.genie_flight_state").collect()}
    assert not {"batch_id", "last_emitted_ts", "written_at"} & set(cols)
    assert cols["phase"].startswith("SCHEDULED")
    for n in METRIC_VIEWS:
        assert spark.sql(f"DESCRIBE TABLE {SCRATCH}.{n}").count() > 0


def test_space_example_sqls_run(spark):
    # every example SQL in resources/genie.yml runs against the views and returns rows for Ops Control
    _build(spark, ["aops-ops-control"])
    space = yaml.safe_load((ROOT / "resources" / "genie.yml").read_text())["resources"]["genie_spaces"]["airport_ops"]["serialized_space"]
    ex = space["instructions"]["example_question_sqls"]
    assert [e["id"] for e in ex] == sorted(e["id"] for e in ex)
    ids = [t["identifier"] for t in space["data_sources"]["tables"]]
    assert ids == sorted(ids)
    for e in ex:
        q = e["sql"][0].replace("${var.catalog}.${var.schema}", SCRATCH)
        assert spark.sql(q).count() > 0, e["question"]
