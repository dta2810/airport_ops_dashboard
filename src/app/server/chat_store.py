"""Genie chat history in Lakebase schema `chat` (CHAT_SCHEMA), owned by the app SP. FORCE RLS scopes every row to app.user_email."""

import json
import os
import re
import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from server import auth

# separate apps sharing one database each own their schema
S = os.environ.get("CHAT_SCHEMA", "chat")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", S):
    raise RuntimeError("CHAT_SCHEMA must be a lowercase Postgres identifier")

DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {S}",
    f"""CREATE TABLE IF NOT EXISTS {S}.conversations (
         conversation_id TEXT PRIMARY KEY, user_email TEXT NOT NULL, title TEXT NOT NULL, role TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    f"CREATE INDEX IF NOT EXISTS conversations_user ON {S}.conversations (user_email, updated_at DESC)",
    f"""CREATE TABLE IF NOT EXISTS {S}.messages (
         message_id TEXT PRIMARY KEY,
         conversation_id TEXT NOT NULL REFERENCES {S}.conversations ON DELETE CASCADE,
         user_email TEXT NOT NULL, question TEXT NOT NULL, status TEXT NOT NULL,
         answer TEXT, sql TEXT, columns JSONB, rows JSONB, row_count INT, suggestions JSONB, error TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    f"CREATE INDEX IF NOT EXISTS messages_conv ON {S}.messages (conversation_id, created_at)",
    f"ALTER TABLE {S}.messages ADD COLUMN IF NOT EXISTS viz_attachment_id TEXT",
    f"ALTER TABLE {S}.messages ADD COLUMN IF NOT EXISTS viz_png BYTEA",
    f"ALTER TABLE {S}.messages ADD COLUMN IF NOT EXISTS query_attachment_id TEXT",
]
for _t in ("conversations", "messages"):
    DDL += [f"ALTER TABLE {S}.{_t} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {S}.{_t} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS own_rows ON {S}.{_t}",
            f"CREATE POLICY own_rows ON {S}.{_t} USING (user_email = current_setting('app.user_email', true)) "
            f"WITH CHECK (user_email = current_setting('app.user_email', true))"]

MSG_COLS = "message_id, question, status, answer, sql, columns, rows, row_count, suggestions, error, viz_attachment_id, query_attachment_id, created_at"


class PgStore:
    # app SP connection (its own Lakebase role from the app's postgres resource), not the user's group role
    def __init__(self, conninfo: dict | None = None, password=None):
        self._kw, self._password, self._pool, self._lock = conninfo, password, None, threading.Lock()

    def _connect_kwargs(self):
        if self._kw:
            return self._kw
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        self._password = lambda: w.postgres.generate_database_credential(endpoint=auth.LAKEBASE_ENDPOINT).token
        host = w.postgres.get_endpoint(name=auth.LAKEBASE_ENDPOINT).status.hosts.host
        return {"host": host, "dbname": auth.PGDATABASE, "user": os.environ["DATABRICKS_CLIENT_ID"],
                "sslmode": "require", "connect_timeout": 30}

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                kw, store = self._connect_kwargs(), self

                class _Conn(psycopg.Connection):
                    @classmethod
                    def connect(cls, conninfo="", **k):
                        if store._password:
                            k["password"] = store._password()
                        return super().connect(conninfo, **k)

                def _guard(conn):
                    w = auth.governance_warnings(conn)
                    conn.commit()
                    if w:
                        raise auth.GovernanceError("; ".join(w))

                self._pool = ConnectionPool(kwargs=kw, connection_class=_Conn, configure=_guard, check=ConnectionPool.check_connection,
                                            min_size=0, max_size=4, max_idle=60, max_lifetime=auth.MAX_LIFETIME_S, open=True)
                with self._pool.connection() as conn:
                    for s in DDL:
                        conn.execute(s)
            return self._pool

    @contextmanager
    def _tx(self, email: str):
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            with conn.transaction():
                conn.execute("SELECT set_config('app.user_email', %s, true)", (email,))
                yield conn

    def list_conversations(self, email, limit=30):
        with self._tx(email) as c:
            return c.execute(f"SELECT conversation_id, title, role, created_at, updated_at FROM {S}.conversations "
                             "WHERE user_email = %s "
                             "ORDER BY updated_at DESC LIMIT %s", (email, limit)).fetchall()

    def get_conversation(self, email, cid):
        with self._tx(email) as c:
            conv = c.execute(f"SELECT conversation_id, title, role, created_at, updated_at FROM {S}.conversations "
                             "WHERE conversation_id = %s AND user_email = %s", (cid, email)).fetchone()
            if not conv:
                return None
            conv["messages"] = c.execute(f"SELECT {MSG_COLS} FROM {S}.messages WHERE conversation_id = %s AND user_email = %s "
                                         "ORDER BY created_at", (cid, email)).fetchall()
            return conv

    def owns(self, email, cid):
        with self._tx(email) as c:
            return c.execute(f"SELECT 1 FROM {S}.conversations WHERE conversation_id = %s AND user_email = %s", (cid, email)).fetchone() is not None

    def add_question(self, email, cid, mid, question, role, new):
        with self._tx(email) as c:
            if new:
                c.execute(f"INSERT INTO {S}.conversations (conversation_id, user_email, title, role) VALUES (%s, %s, %s, %s) "
                          "ON CONFLICT (conversation_id) DO NOTHING", (cid, email, question[:120], role))
            c.execute(f"UPDATE {S}.conversations SET updated_at = now() WHERE conversation_id = %s AND user_email = %s", (cid, email))
            c.execute(f"INSERT INTO {S}.messages (message_id, conversation_id, user_email, question, status) "
                      "VALUES (%s, %s, %s, %s, 'PENDING') ON CONFLICT (message_id) DO NOTHING", (mid, cid, email, question))

    def save_answer(self, email, mid, a: dict):
        with self._tx(email) as c:
            c.execute(f"UPDATE {S}.messages SET status = %s, answer = %s, sql = %s, columns = %s, rows = %s, "
                      "row_count = %s, suggestions = %s, error = %s, viz_attachment_id = %s, query_attachment_id = %s "
                      "WHERE message_id = %s AND user_email = %s",
                      (a["status"], a.get("answer"), a.get("sql"), _j(a.get("columns")), _j(a.get("rows")),
                       a.get("row_count"), _j(a.get("suggestions")), a.get("error"), a.get("viz_attachment_id"),
                       a.get("query_attachment_id"), mid, email))

    def get_viz(self, email, mid):
        with self._tx(email) as c:
            r = c.execute(f"SELECT viz_png FROM {S}.messages WHERE message_id = %s AND user_email = %s", (mid, email)).fetchone()
            return bytes(r["viz_png"]) if r and r["viz_png"] else None

    def save_viz(self, email, mid, png: bytes):
        with self._tx(email) as c:
            c.execute(f"UPDATE {S}.messages SET viz_png = %s WHERE message_id = %s AND user_email = %s", (png, mid, email))

    def get_message(self, email, mid):
        with self._tx(email) as c:
            return c.execute(f"SELECT conversation_id, {MSG_COLS} FROM {S}.messages WHERE message_id = %s AND user_email = %s", (mid, email)).fetchone()

    def delete_conversation(self, email, cid):
        with self._tx(email) as c:
            return c.execute(f"DELETE FROM {S}.conversations WHERE conversation_id = %s AND user_email = %s", (cid, email)).rowcount > 0


def _j(v):
    return None if v is None else json.dumps(v, default=str)


class MemoryStore:
    # MOCK_MODE / tests: same interface, per-user dicts
    def __init__(self):
        self.convs, self.msgs = {}, {}

    def list_conversations(self, email, limit=30):
        rows = [c for c in self.convs.values() if c["user_email"] == email]
        return [{k: v for k, v in c.items() if k != "user_email"} for c in sorted(rows, key=lambda c: c["updated_at"], reverse=True)][:limit]

    def get_conversation(self, email, cid):
        c = self.convs.get(cid)
        if not c or c["user_email"] != email:
            return None
        out = {k: v for k, v in c.items() if k != "user_email"}
        out["messages"] = [{k: v for k, v in m.items() if k not in ("user_email", "conversation_id", "viz_png")}
                           for m in self.msgs.values() if m["conversation_id"] == cid]
        return out

    def owns(self, email, cid):
        return self.get_conversation(email, cid) is not None

    def add_question(self, email, cid, mid, question, role, new):
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        if new and cid not in self.convs:
            self.convs[cid] = {"conversation_id": cid, "user_email": email, "title": question[:120], "role": role,
                               "created_at": now, "updated_at": now}
        self.convs[cid]["updated_at"] = now
        self.msgs.setdefault(mid, {"message_id": mid, "conversation_id": cid, "question": question, "status": "PENDING",
                                   "created_at": now, "user_email": email})

    def save_answer(self, email, mid, a):
        m = self.msgs.get(mid)
        if m and m["user_email"] == email:
            m.update({k: a.get(k) for k in ("status", "answer", "sql", "columns", "rows", "row_count", "suggestions", "error", "viz_attachment_id", "query_attachment_id")})

    def get_viz(self, email, mid):
        m = self.msgs.get(mid)
        return m.get("viz_png") if m and m["user_email"] == email else None

    def save_viz(self, email, mid, png):
        m = self.msgs.get(mid)
        if m and m["user_email"] == email:
            m["viz_png"] = png

    def get_message(self, email, mid):
        m = self.msgs.get(mid)
        return {k: v for k, v in m.items() if k not in ("user_email", "viz_png")} if m and m["user_email"] == email else None

    def delete_conversation(self, email, cid):
        if not self.owns(email, cid):
            return False
        del self.convs[cid]
        self.msgs = {k: m for k, m in self.msgs.items() if m["conversation_id"] != cid}
        return True
