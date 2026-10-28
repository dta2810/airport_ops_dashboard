# Contract §7 as SQL: access_policy, section grants, FORCE RLS, explicit gold-principal policy.
# Pure function so the setup notebook and the RLS test run the exact same statements.
from psycopg import sql

RLS = {
    "flight_state": ["origin_iata", "destination_iata"],
    "airport_hour_kpis": ["airport_iata"],
    "gate_hour_occupancy": ["airport_iata"],
    "hub_connections": ["hub_iata"],
}


def access_statements(access, database, gold_principal):
    I, L = sql.Identifier, sql.Literal
    roles = access["roles"]
    out = [
        sql.SQL("CREATE TABLE IF NOT EXISTS ops.access_policy (pg_role TEXT PRIMARY KEY, label TEXT NOT NULL, "
                "airports TEXT[] NOT NULL, sections TEXT[] NOT NULL, priority INT NOT NULL)"),
        # reload without FORCE: an owner without BYPASSRLS (deployer identity) is otherwise blocked by own_row
        sql.SQL("ALTER TABLE ops.access_policy NO FORCE ROW LEVEL SECURITY"),
        sql.SQL("TRUNCATE ops.access_policy"),
    ]
    for r in roles:
        out.append(sql.SQL("INSERT INTO ops.access_policy VALUES ({}, {}, {}, {}, {})").format(
            L(r["group"]), L(r["label"]), L(list(r["airports"])), L(list(r["sections"])), L(int(r["priority"]))))
    out += [sql.SQL("ALTER TABLE ops.access_policy ENABLE ROW LEVEL SECURITY"),
            sql.SQL("ALTER TABLE ops.access_policy FORCE ROW LEVEL SECURITY"),
            sql.SQL("DROP POLICY IF EXISTS own_row ON ops.access_policy"),
            sql.SQL("CREATE POLICY own_row ON ops.access_policy FOR SELECT USING (pg_role = current_user)")]

    out += [sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(I(database), I(gold_principal)),
            sql.SQL("GRANT USAGE ON SCHEMA ops TO {}").format(I(gold_principal))]

    # row filter: the role's airports, '*' = all
    allowed = "COALESCE((SELECT airports FROM ops.access_policy WHERE pg_role = current_user), '{{}}'::text[])"
    for table, cols in RLS.items():
        t = sql.SQL("ops.{}").format(I(table))
        cond = " OR ".join([f"{c} = ANY({allowed})" for c in cols] + [f"'*' = ANY({allowed})"])
        out += [sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(t),
                sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(t),
                sql.SQL("DROP POLICY IF EXISTS role_rows ON {}").format(t),
                sql.SQL("CREATE POLICY role_rows ON {} FOR SELECT USING (" + cond + ")").format(t),
                sql.SQL("DROP POLICY IF EXISTS pipeline_all ON {}").format(t),
                sql.SQL("CREATE POLICY pipeline_all ON {} FOR ALL TO {} USING (true) WITH CHECK (true)").format(t, I(gold_principal)),
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(t, I(gold_principal))]

    # section -> table grants; revoke first so a removed section really disappears
    tables = sorted({t for ts in access["section_tables"].values() for t in ts})
    for r in roles:
        g = I(r["group"])
        out += [sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(I(database), g),
                sql.SQL("GRANT USAGE ON SCHEMA ops TO {}").format(g),
                sql.SQL("GRANT SELECT ON ops.access_policy, ops.batch_log TO {}").format(g)]
        out += [sql.SQL("REVOKE ALL ON ops.{} FROM {}").format(I(t), g) for t in tables]
        granted = sorted({t for s in r["sections"] for t in access["section_tables"][s]})
        out += [sql.SQL("GRANT SELECT ON ops.{} TO {}").format(I(t), g) for t in granted]
    return out
