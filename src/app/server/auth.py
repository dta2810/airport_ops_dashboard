"""OBO to Lakebase: group-scoped credentials minted with the user's forwarded token, pooled per (user, role)."""

import os
import threading
import time

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg_pool import ConnectionPool

LAKEBASE_ENDPOINT = os.environ.get("LAKEBASE_ENDPOINT", "")
PGDATABASE = os.environ.get("PGDATABASE", "airport_ops")
MAX_LIFETIME_S = 300  # recycle quickly so group revocation takes effect; credential expires in ~1h


class GovernanceError(RuntimeError):
    pass


def _host() -> str:
    # DATABRICKS_HOST in Apps has no scheme; the SDK needs one
    h = os.environ.get("DATABRICKS_HOST", "")
    return h if h.startswith("http") else f"https://{h}"


def user_client(user_token: str) -> WorkspaceClient:
    return WorkspaceClient(host=_host(), token=user_token, auth_type="pat")


def mint(user_token: str, group: str) -> str:
    # platform refuses when the user is not a member of the group
    return user_client(user_token).postgres.generate_database_credential(
        endpoint=LAKEBASE_ENDPOINT, group_name=group).token


_host_cache: dict = {}


def pg_host(user_token: str) -> str:
    if "h" not in _host_cache:
        _host_cache["h"] = user_client(user_token).postgres.get_endpoint(name=LAKEBASE_ENDPOINT).status.hosts.host
    return _host_cache["h"]


def governance_warnings(conn) -> list[str]:
    # rolbypassrls ignores RLS even under FORCE: never serve data on such a connection
    row = conn.execute("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    out = []
    if row and (row[1] or row[2]):
        out.append(f"{row[0]} bypasses row-level security")
    member = conn.execute("""SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
                             WHERE r.rolname = 'databricks_superuser'
                               AND m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user)""").fetchone()
    if member:
        out.append("current_user is a member of databricks_superuser")
    return out


class _RolePool:
    # one pool per (user, role); the latest forwarded token mints the password for each new connection
    def __init__(self, role: str, user_token: str):
        self.role = role
        self.token = user_token
        self.last_used = time.time()
        rp = self

        class _Conn(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kw):
                kw["password"] = mint(rp.token, rp.role)
                return super().connect(conninfo, **kw)

        def _guard(conn):
            w = governance_warnings(conn)
            conn.commit()
            if w:
                raise GovernanceError("; ".join(w))

        self.pool = ConnectionPool(
            kwargs={"host": pg_host(user_token), "dbname": PGDATABASE, "user": role,
                    "sslmode": "require", "connect_timeout": 30},
            connection_class=_Conn, configure=_guard, check=ConnectionPool.check_connection,
            min_size=0, max_size=4, max_idle=60, max_lifetime=MAX_LIFETIME_S, open=True)


_pools: dict = {}  # (email, role) -> _RolePool
_lock = threading.Lock()
_POOL_IDLE_TIMEOUT_S = 15 * 60  # close pools unused for > 15 min


def connection(user_email: str, role: str, user_token: str):
    with _lock:
        # evict idle pools opportunistically
        now = time.time()
        for k in [k for k, p in _pools.items() if now - p.last_used > _POOL_IDLE_TIMEOUT_S]:
            _pools.pop(k).pool.close()
        rp = _pools.get((user_email, role))
        if rp is None:
            rp = _pools[(user_email, role)] = _RolePool(role, user_token)
        rp.token, rp.last_used = user_token, now
    return rp.pool.connection()
