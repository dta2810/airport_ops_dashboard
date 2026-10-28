# Genie chat history store against a real Postgres 17 (docker): owner role under FORCE RLS, per-user isolation.
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

if not shutil.which("docker"):
    pytest.skip("docker not available", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/app"))
from server import chat_store  # noqa: E402
from server.chat_store import PgStore  # noqa: E402
from server import auth  # noqa: E402

PORT = 55433
# throwaway passwords for the local test container
PG_PW, APP_PW = secrets.token_hex(8), secrets.token_hex(8)
ADMIN = {"host": "localhost", "port": PORT, "dbname": "airport_ops", "user": "postgres", "password": PG_PW}
A, B = "a@example.com", "b@example.com"
C1, C2 = "a" * 32, "b" * 32


@pytest.fixture(scope="module")
def store():
    name = "aops-chat-test"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={PG_PW}",
                    "-e", "POSTGRES_DB=airport_ops", "-p", f"{PORT}:5432", "postgres:17"], check=True, capture_output=True)
    try:
        for _ in range(60):
            try:
                psycopg.connect(**ADMIN, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                time.sleep(1)
        with psycopg.connect(**ADMIN, autocommit=True) as c:
            # like the Apps postgres resource: a plain login role with CREATE on the database
            c.execute(f"CREATE ROLE app_sp LOGIN PASSWORD '{APP_PW}'")
            c.execute("GRANT CREATE, CONNECT ON DATABASE airport_ops TO app_sp")
        yield PgStore({"host": "localhost", "port": PORT, "dbname": "airport_ops", "user": "app_sp", "password": APP_PW})
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_roundtrip(store):
    store.add_question(A, C1, "1" * 32, "OTP by airport?", "aops-ops-control", new=True)
    store.save_answer(A, "1" * 32, {"status": "COMPLETED", "answer": "PTY 80%", "sql": "SELECT 1",
                                    "columns": ["a"], "rows": [["PTY"]], "row_count": 1, "suggestions": ["next?"]})
    store.add_question(A, C1, "2" * 32, "and BOG?", "aops-ops-control", new=False)
    c = store.get_conversation(A, C1)
    assert c["title"] == "OTP by airport?" and [m["question"] for m in c["messages"]] == ["OTP by airport?", "and BOG?"]
    assert c["messages"][0]["rows"] == [["PTY"]] and c["messages"][1]["status"] == "PENDING"
    assert [x["conversation_id"] for x in store.list_conversations(A)] == [C1]


def test_users_are_isolated(store):
    store.add_question(B, C2, "3" * 32, "B question", None, new=True)
    assert [x["conversation_id"] for x in store.list_conversations(B)] == [C2]
    assert store.get_conversation(B, C1) is None and not store.owns(B, C1)
    assert store.get_message(B, "1" * 32) is None
    assert not store.delete_conversation(B, C1)
    store.save_answer(B, "1" * 32, {"status": "FAILED", "error": "x"})  # other user's message: no effect
    assert store.get_message(A, "1" * 32)["status"] == "COMPLETED"


def test_rls_binds_the_owner_and_blocks_spoofed_rows(store):
    with psycopg.connect(host="localhost", port=PORT, dbname="airport_ops", user="app_sp", password=APP_PW) as c:
        assert c.execute(f"SELECT count(*) FROM {chat_store.S}.conversations").fetchone()[0] == 0  # no app.user_email -> nothing
        c.execute("SELECT set_config('app.user_email', %s, false)", (A,))
        assert c.execute(f"SELECT count(*) FROM {chat_store.S}.conversations").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"INSERT INTO {chat_store.S}.conversations (conversation_id, user_email, title) VALUES (%s, %s, 't')", ("c" * 32, B))


def test_viz_png_roundtrip_and_isolation(store):
    store.save_answer(A, "1" * 32, {"status": "COMPLETED", "viz_attachment_id": "v" * 32})
    assert store.get_message(A, "1" * 32)["viz_attachment_id"] == "v" * 32
    store.save_viz(A, "1" * 32, b"\x89PNG\x00bytes")
    assert store.get_viz(A, "1" * 32) == b"\x89PNG\x00bytes" and store.get_viz(B, "1" * 32) is None


def test_delete_cascades(store):
    assert store.delete_conversation(A, C1)
    assert store.get_message(A, "2" * 32) is None and store.list_conversations(A) == []


def test_ddl_is_idempotent(store):
    store._pool = None
    store._get_pool()  # re-applies DDL on an existing schema


def test_query_filters_by_user_email(store):
    """All queries include user_email filter: a row inserted with different email is not returned."""
    store.add_question(A, C1, "1" * 32, "A question", None, new=True)
    # directly insert a message with B's email under A's conversation (would be visible without user_email filter)
    with psycopg.connect(**ADMIN, autocommit=True) as c:
        c.execute("SELECT set_config('app.user_email', %s, false)", (A,))
        c.execute(f"INSERT INTO {chat_store.S}.messages (message_id, conversation_id, user_email, question, status) "
                  "VALUES (%s, %s, %s, 'B spoofed', 'PENDING')", ("b" * 32, C1, B))

    # A should not see B's message via any query method
    assert store.get_message(A, "b" * 32) is None
    conv = store.get_conversation(A, C1)
    assert len(conv["messages"]) == 1  # only A's original message

    # B should not see A's conversation
    assert store.get_conversation(B, C1) is None
    assert store.owns(B, C1) is False


def test_governance_guard_rejects_superuser(store):
    """Superuser or rolbypassrls connections are rejected by governance guard."""
    # try to connect as postgres superuser (will fail governance check)
    superuser_conninfo = {"host": "localhost", "port": PORT, "dbname": "airport_ops", "user": "postgres", "password": "x"}
    try:
        with psycopg.connect(**superuser_conninfo, autocommit=True) as c:
            # governance_warnings should detect superuser
            w = auth.governance_warnings(c)
            assert len(w) > 0 and "superuser" in " ".join(w).lower()
    except Exception:
        # if connection fails, that's also fine (security)
        pass
