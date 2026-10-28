# LakebaseWriter against a real Postgres 17 (docker): replay skip, checkpoint reset, delete of vanished keys.
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

psycopg = pytest.importorskip("psycopg")
if not shutil.which("docker"):
    pytest.skip("docker not available", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/pipelines/gold"))
import lakebase_writer  # noqa: E402

PORT = 55434
# throwaway password for the local test container
PG_PW = secrets.token_hex(8)
CONN = {"host": "localhost", "port": PORT, "dbname": "airport_ops", "user": "postgres", "password": PG_PW}
COLS, KEYS = ["k", "v", "last_emitted_ts"], ["k"]


@pytest.fixture(scope="module")
def writer():
    name = "aops-writer-test"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={PG_PW}",
                    "-e", "POSTGRES_DB=airport_ops", "-p", f"{PORT}:5432", "postgres:17"], check=True, capture_output=True)
    try:
        for _ in range(60):
            try:
                psycopg.connect(**CONN, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                time.sleep(1)
        with psycopg.connect(**CONN, autocommit=True) as c:
            c.execute((ROOT / "src/sql/lakebase_ddl.sql").read_text())
            c.execute("CREATE TABLE ops.t (k TEXT PRIMARY KEY, v INT, last_emitted_ts TIMESTAMPTZ, written_at TIMESTAMPTZ)")
        # local container: no TLS, fixed port; the writer's own SQL is unchanged
        lakebase_writer.psycopg = SimpleNamespace(connect=lambda **k: psycopg.connect(**{**k, "port": PORT, "sslmode": "disable"}))
        w = SimpleNamespace(current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="postgres")),
                            api_client=SimpleNamespace(do=lambda *a, **k: {"token": PG_PW}))
        yield lakebase_writer.LakebaseWriter(w, "projects/p/branches/b/endpoints/e", "localhost", "airport_ops")
    finally:
        lakebase_writer.psycopg = psycopg
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _rows():
    with psycopg.connect(**CONN) as c:
        return dict(c.execute("SELECT k, v FROM ops.t").fetchall())


def _write(writer, batch_id, rows, keys):
    return writer.write_batch("t", batch_id, "t", KEYS, COLS, keys, [{"k": k, "v": v, "last_emitted_ts": None} for k, v in rows])


def test_replay_skip_reset_and_delete(writer):
    assert not _write(writer, 5, [("a", 1), ("b", 1)], [("a",), ("b",)])["skipped"]
    # replay of the committed batch: no-op
    assert _write(writer, 5, [("a", 99)], [("a",)])["skipped"]
    assert _rows() == {"a": 1, "b": 1}
    # checkpoint reset: batch ids restart below the stored one and must still be written
    r = _write(writer, 0, [("a", 2)], [("a",), ("b",)])
    assert not r["skipped"] and r["deleted"] == 1
    assert _rows() == {"a": 2}
    with psycopg.connect(**CONN) as c:
        assert c.execute("SELECT last_batch_id FROM ops.batch_control WHERE sink = 't'").fetchone()[0] == 0
        assert c.execute("SELECT n_rows FROM ops.batch_log WHERE sink = 't' AND batch_id = 0").fetchone()[0] == 1
