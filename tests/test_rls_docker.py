# Contract §7 against a real Postgres 17 (docker): the same statements the setup notebook applies.
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

psycopg = pytest.importorskip("psycopg")
if not shutil.which("docker"):
    pytest.skip("docker not available", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/setup"))
from lakebase_access import access_statements  # noqa: E402

ACCESS = yaml.safe_load((ROOT / "config/demo.yaml").read_text())["access"]
SP = "pipeline-sp-test"
PORT = 55432
# throwaway password for the local test container
PG_PW = secrets.token_hex(8)
DSN = f"host=localhost port={PORT} user=postgres dbname=airport_ops password={PG_PW}"

ROWS = {  # (origin, destination) per flight; airport/hub per hour rows
    "flight_state": [("f1", "PTY", "BOG"), ("f2", "BOG", "PTY"), ("f3", "PTY", "LIM"), ("f4", "LIM", "PTY")],
    "airport_hour_kpis": ["PTY", "BOG", "LIM"],
    "gate_hour_occupancy": ["PTY", "PTY", "BOG"],
    "hub_connections": ["PTY", "PTY"],
}


@pytest.fixture(scope="module")
def pg():
    name = "aops-rls-test"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={PG_PW}",
                    "-e", "POSTGRES_DB=airport_ops", "-p", f"{PORT}:5432", "postgres:17"], check=True, capture_output=True)
    try:
        for _ in range(60):
            try:
                psycopg.connect(DSN, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                time.sleep(1)
        with psycopg.connect(DSN, autocommit=True) as c:
            for r in ACCESS["roles"]:
                c.execute(f'CREATE ROLE "{r["group"]}" NOLOGIN')
            c.execute(f'CREATE ROLE "{SP}" NOLOGIN')
            c.execute((ROOT / "src/sql/lakebase_ddl.sql").read_text()) if (ROOT / "src/sql/lakebase_ddl.sql").exists() else None
            c.execute("CREATE SCHEMA IF NOT EXISTS ops")
            c.execute("CREATE TABLE IF NOT EXISTS ops.batch_log (sink TEXT, batch_id BIGINT, written_at TIMESTAMPTZ DEFAULT now())")
            c.execute("CREATE TABLE IF NOT EXISTS ops.flight_state (flight_id TEXT PRIMARY KEY, origin_iata TEXT, destination_iata TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS ops.airport_hour_kpis (airport_iata TEXT, hour_start_utc TIMESTAMPTZ DEFAULT now())")
            c.execute("CREATE TABLE IF NOT EXISTS ops.gate_hour_occupancy (airport_iata TEXT, gate_id TEXT, hour_start_utc TIMESTAMPTZ DEFAULT now())")
            c.execute("CREATE TABLE IF NOT EXISTS ops.hub_connections (hub_iata TEXT, hour_start_utc TIMESTAMPTZ DEFAULT now())")
            c.execute("INSERT INTO ops.batch_log (sink, batch_id) VALUES ('gold_flight_state', 1)")
            assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'ops' AND table_name = 'flight_state'").fetchone()[0] > 3, "real DDL expected"
            for fid, o, d in ROWS["flight_state"]:
                c.execute("INSERT INTO ops.flight_state (flight_id, origin_iata, destination_iata) VALUES (%s, %s, %s)", (fid, o, d))
            for i, a in enumerate(ROWS["airport_hour_kpis"]):
                c.execute("INSERT INTO ops.airport_hour_kpis (airport_iata, hour_start_utc) VALUES (%s, now() + make_interval(hours => %s))", (a, i))
            for i, a in enumerate(ROWS["gate_hour_occupancy"]):
                c.execute("INSERT INTO ops.gate_hour_occupancy (airport_iata, gate_id, hour_start_utc) VALUES (%s, %s, now())", (a, f"G{i}"))
            for i, a in enumerate(ROWS["hub_connections"]):
                c.execute("INSERT INTO ops.hub_connections (hub_iata, hour_start_utc) VALUES (%s, now() + make_interval(hours => %s))", (a, i))
            for stmt in access_statements(ACCESS, "airport_ops", SP):
                c.execute(stmt)
            # idempotent: applying twice must not fail
            for stmt in access_statements(ACCESS, "airport_ops", SP):
                c.execute(stmt)
        yield
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _as(role, q):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f'SET ROLE "{role}"')
        try:
            return c.execute(q).fetchall()
        except psycopg.errors.InsufficientPrivilege:
            return "DENIED"


EXPECTED = {
    #                     flight_state  airport_hour  gates   hub
    "aops-ops-control":     (4,          3,            3,      2),
    "aops-hub-connections": (4,          1,            "DENIED", 2),  # overview grants airport_hour_kpis
    "aops-station-PTY":     (4,          1,            2,      "DENIED"),
    "aops-station-BOG":     (2,          1,            1,      "DENIED"),
}


@pytest.mark.parametrize("role", list(EXPECTED))
def test_rows_and_sections_per_role(pg, role):
    got = []
    for t in ["flight_state", "airport_hour_kpis", "gate_hour_occupancy", "hub_connections"]:
        r = _as(role, f"SELECT count(*) FROM ops.{t}")
        got.append(r if r == "DENIED" else r[0][0])
    print(f"\n{role}: {got}")
    assert tuple(got) == EXPECTED[role]


@pytest.mark.parametrize("role", list(EXPECTED))
def test_policy_shows_own_row_only(pg, role):
    assert _as(role, "SELECT pg_role FROM ops.access_policy") == [(role,)]


def test_station_bog_never_sees_other_airports(pg):
    rows = _as("aops-station-BOG", "SELECT origin_iata, destination_iata FROM ops.flight_state")
    assert rows and all("BOG" in r for r in rows)


def test_pipeline_sp_writes_everything(pg):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f'SET ROLE "{SP}"')
        assert c.execute("SELECT count(*) FROM ops.flight_state").fetchone()[0] == 4
        c.execute("UPDATE ops.flight_state SET destination_iata = destination_iata")
        c.execute("INSERT INTO ops.hub_connections (hub_iata, hour_start_utc) VALUES ('PTY', now() - interval '1 day')")
        c.execute("DELETE FROM ops.hub_connections WHERE hub_iata = 'PTY'")
        assert c.execute("SELECT count(*) FROM ops.hub_connections").fetchone()[0] == 0


def test_force_rls_binds_table_owner(pg):
    # owner (postgres superuser) bypasses; FORCE is set so a non-superuser owner would be bound
    with psycopg.connect(DSN) as c:
        forced = c.execute("SELECT relname FROM pg_class WHERE relforcerowsecurity AND relnamespace = 'ops'::regnamespace").fetchall()
    assert {r[0] for r in forced} == {"access_policy", "flight_state", "airport_hour_kpis", "gate_hour_occupancy", "hub_connections"}


def test_pool_connects_as_group_and_refuses_bypass(pg, monkeypatch):
    # real psycopg_pool path: rotating password via connection_class, guard via configure
    sys.path.insert(0, str(ROOT / "src/app"))
    from server import auth
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("ALTER ROLE \"aops-station-BOG\" LOGIN PASSWORD 'pw'")
        c.execute("DROP ROLE IF EXISTS bypasser")
        c.execute("CREATE ROLE bypasser LOGIN PASSWORD 'pw' BYPASSRLS")
    minted = []
    monkeypatch.setattr(auth, "mint", lambda token, group: minted.append((token, group)) or "pw")
    monkeypatch.setattr(auth, "pg_host", lambda token: "localhost")
    monkeypatch.setattr(auth, "PGDATABASE", "airport_ops")
    auth._pools.clear()
    orig = auth._RolePool.__init__

    def init(self, role, token):
        orig(self, role, token)
        self.pool.kwargs.update(port=PORT, sslmode="disable")
    monkeypatch.setattr(auth._RolePool, "__init__", init)

    with auth.connection("u@x", "aops-station-BOG", "tok1") as conn:
        assert conn.execute("SELECT current_user").fetchone()[0] == "aops-station-BOG"
        assert conn.execute("SELECT count(*) FROM ops.flight_state").fetchone()[0] == 2
    with auth.connection("u@x", "aops-station-BOG", "tok2") as conn:
        conn.execute("SELECT 1")
    assert len(auth._pools) == 1 and minted[0] == ("tok1", "aops-station-BOG")
    with pytest.raises(Exception):
        with auth.connection("u@x", "bypasser", "tok1") as conn:
            conn.execute("SELECT 1")
    for rp in auth._pools.values():
        rp.pool.close(timeout=1)


def test_owner_as_gold_principal(pg):
    # deployer mode: the gold principal owns the database + tables; same statements must apply (twice) and keep writes open
    me = "me@example.com"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f'CREATE ROLE "{me}" LOGIN')
        c.execute(f'CREATE DATABASE airport_ops_owner OWNER "{me}"')
    owner_dsn = DSN.replace("dbname=airport_ops", "dbname=airport_ops_owner")
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute(f'SET ROLE "{me}"')
        c.execute((ROOT / "src/sql/lakebase_ddl.sql").read_text())
        for _ in range(2):
            for stmt in access_statements(ACCESS, "airport_ops_owner", me):
                c.execute(stmt)
        c.execute("INSERT INTO ops.flight_state (flight_id, origin_iata, destination_iata) VALUES ('o1', 'PTY', 'BOG')")
        assert c.execute("SELECT count(*) FROM ops.flight_state").fetchone()[0] == 1
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute('SET ROLE "aops-station-BOG"')
        assert c.execute("SELECT count(*) FROM ops.flight_state").fetchone()[0] == 1
