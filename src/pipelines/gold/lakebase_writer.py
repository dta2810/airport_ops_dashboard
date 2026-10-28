import time
import psycopg
from psycopg import sql


class LakebaseWriter:
    def __init__(self, workspace_client, endpoint, host, database, token_ttl_s=3000):
        self.w = workspace_client
        self.endpoint = endpoint
        self.host = host
        self.database = database
        self.token_ttl_s = token_ttl_s
        self._token = None
        self._minted = 0.0
        self._user = None

    # OAuth token minted on the driver/worker; re-minted before TTL
    def _credentials(self):
        if self._user is None:
            # user_name is the email for users and the application id for service principals
            self._user = self.w.current_user.me().user_name
        refreshed = False
        if self._token is None or time.time() - self._minted > self.token_ttl_s:
            self._token = self.w.api_client.do("POST", "/api/2.0/postgres/credentials",
                                               body={"endpoint": self.endpoint})["token"]
            self._minted = time.time()
            refreshed = True
        return self._user, self._token, refreshed

    def write_batch(self, sink, batch_id, table, key_cols, cols, keys, rows,
                    ts_col="last_emitted_ts", schema="ops", batch_started=None):
        """keys: touched key tuples; rows: dicts with the recomputed gold for those keys."""
        user, token, refreshed = self._credentials()
        tbl = sql.Identifier(schema, table)
        ctl = lambda t: sql.Identifier(schema, t)
        kc = [sql.Identifier(c) for c in key_cols]
        with psycopg.connect(host=self.host, dbname=self.database, user=user, password=token,
                             sslmode="require", connect_timeout=15) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("SELECT last_batch_id FROM {} WHERE sink = %s FOR UPDATE").format(ctl("batch_control")), (sink,))
                    r = cur.fetchone()
                    if r is not None and r[0] >= batch_id:
                        return {"skipped": True, "upserted": 0, "deleted": 0, "refreshed": refreshed}
                    if rows:
                        upd = [sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in cols if c not in key_cols]
                        cur.executemany(
                            sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}, written_at = now()").format(
                                tbl, sql.SQL(", ").join(map(sql.Identifier, cols)),
                                sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                                sql.SQL(", ").join(kc), sql.SQL(", ").join(upd)),
                            [tuple(row[c] for c in cols) for row in rows])
                    # touched key tuples absent from recomputed gold → delete
                    present = {tuple(row[c] for c in key_cols) for row in rows}
                    gone = [k for k in keys if tuple(k) not in present]
                    deleted = 0
                    if gone:
                        cur.executemany(
                            sql.SQL("DELETE FROM {} WHERE ({}) = ({})").format(
                                tbl, sql.SQL(", ").join(kc), sql.SQL(", ").join(sql.Placeholder() * len(key_cols))),
                            [tuple(k) for k in gone])
                        deleted = len(gone)
                    cur.execute(sql.SQL(
                        "INSERT INTO {} (sink, last_batch_id, updated_at, token_minted_at) VALUES (%s, %s, now(), to_timestamp(%s)) "
                        "ON CONFLICT (sink) DO UPDATE SET last_batch_id = EXCLUDED.last_batch_id, updated_at = now(), "
                        "token_minted_at = EXCLUDED.token_minted_at").format(ctl("batch_control")), (sink, batch_id, self._minted))
                    cur.execute(sql.SQL(
                        "INSERT INTO {} (sink, batch_id, n_keys, n_rows, n_deleted, batch_started_at, max_event_ts) "
                        "VALUES (%s, %s, %s, %s, %s, to_timestamp(%s), %s) ON CONFLICT DO NOTHING").format(ctl("batch_log")),
                        (sink, batch_id, len(keys), len(rows), deleted, batch_started or time.time(),
                         max((row[ts_col] for row in rows if row.get(ts_col)), default=None)))
                    if refreshed:
                        cur.execute(sql.SQL("INSERT INTO {} (minted_at, batch_id) VALUES (to_timestamp(%s), %s) ON CONFLICT DO NOTHING").format(ctl("token_log")),
                                    (self._minted, batch_id))
        return {"skipped": False, "upserted": len(rows), "deleted": deleted, "refreshed": refreshed}
