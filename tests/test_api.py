# HTTP-level tests through the real router (catches mount/prefix bugs), mock and OBO modes.
import importlib
import sys
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[1] / "src/app"
sys.path.insert(0, str(APP))
GROUPS = "aops-ops-control,aops-hub-connections,aops-station-PTY,aops-station-BOG"


def _client(monkeypatch, mock):
    monkeypatch.setenv("MOCK_MODE", "true" if mock else "false")
    monkeypatch.setenv("ACCESS_GROUPS", GROUPS)
    for m in [m for m in sys.modules if m == "app" or m.startswith("server")]:
        del sys.modules[m]
    app = importlib.import_module("app")
    return TestClient(app.app), importlib.import_module("server.routes"), importlib.import_module("server.auth")


# ---------- mock mode ----------

def test_mock_roles_and_sections(monkeypatch):
    c, _, _ = _client(monkeypatch, mock=True)
    roles = {r["role"]: r for r in c.get("/api/roles").json()["roles"]}
    assert list(roles) == GROUPS.split(",")
    for role, info in roles.items():
        for ep, sec in {"kpis": "overview", "flights": "flights", "hourly": "airport_kpis", "gates": "gates",
                        "delays": "delays", "connections": "connections"}.items():
            r = c.get(f"/api/{ep}", params={"role": role})
            assert r.status_code == (200 if sec in info["sections"] else 403), (role, ep, r.status_code)
            if r.status_code == 200:
                assert isinstance(r.json(), list)


def test_mock_station_sees_only_its_airport(monkeypatch):
    c, _, _ = _client(monkeypatch, mock=True)
    flights = c.get("/api/flights", params={"role": "aops-station-BOG"}).json()
    assert flights and all("BOG" in (f["origin_iata"], f["destination_iata"]) for f in flights)
    assert {k["airport_iata"] for k in c.get("/api/kpis", params={"role": "aops-station-BOG"}).json()} == {"BOG"}


def test_mock_airport_filter_rules(monkeypatch):
    c, _, _ = _client(monkeypatch, mock=True)
    assert c.get("/api/kpis", params={"role": "aops-station-BOG", "airport": "PTY"}).status_code == 403
    assert c.get("/api/kpis", params={"role": "aops-station-BOG", "airport": "bog'; --"}).status_code == 400
    assert c.get("/api/kpis", params={"role": "aops-ops-control", "airport": "LIM"}).status_code == 200


def test_spa_served_and_api_not_shadowed(monkeypatch):
    c, _, _ = _client(monkeypatch, mock=True)
    r = c.get("/api/flights", params={"role": "aops-ops-control"})
    assert r.headers["content-type"].startswith("application/json")


# ---------- OBO mode (auth.connection faked) ----------

class _Cur:
    def __init__(self, rows, cols):
        self._rows, self.description = rows, [type("D", (), {"name": c}) for c in cols]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, role, deny=False):
        self.role, self.deny, self.calls = role, deny, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, params=None):
        self.calls.append((q, params))
        if "ops.access_policy" in q:
            return _Cur([("Label " + self.role, ["BOG"] if "BOG" in self.role else ["*"], ["overview", "flights"], 3)], ["l", "a", "s", "p"])
        if self.deny:
            raise psycopg.errors.InsufficientPrivilege("permission denied")
        return _Cur([("BOG", 0.9)], ["airport_iata", "dep_otp_d0"])


H = {"x-forwarded-access-token": "tok", "x-forwarded-email": "u@example.com"}


class _FakeUser:
    def __init__(self, email):
        self.user_name = email


class _FakeCurrentUser:
    def __init__(self, email):
        self.email = email

    def me(self):
        return _FakeUser(self.email)


class _FakeUserClient:
    def __init__(self, email):
        self.current_user = _FakeCurrentUser(email)


def _fake_user_client(email):
    def _uc(token):
        return _FakeUserClient(email)
    return _uc


def test_obo_requires_token(monkeypatch):
    c, _, _ = _client(monkeypatch, mock=False)
    assert c.get("/api/roles").status_code == 401
    assert c.get("/api/kpis", params={"role": "aops-station-BOG"}).status_code == 401


def test_obo_roles_only_accepted_groups(monkeypatch):
    c, routes, auth = _client(monkeypatch, mock=False)

    def fake_mint(token, group):
        if group not in ("aops-station-BOG", "aops-ops-control"):
            raise RuntimeError("mint refused: not a member")
        return "pw"
    opened = []
    monkeypatch.setattr(auth, "mint", fake_mint)
    monkeypatch.setattr(auth, "connection", lambda e, r, t: opened.append(r) or _Conn(r))
    monkeypatch.setattr(auth, "user_client", _fake_user_client("u@example.com"))
    got = [r["role"] for r in c.get("/api/roles", headers=H).json()["roles"]]
    assert sorted(got) == ["aops-ops-control", "aops-station-BOG"]
    assert sorted(opened) == sorted(got)  # refused groups never get a pool


def test_obo_unknown_role_and_parameterized_airport(monkeypatch):
    c, routes, auth = _client(monkeypatch, mock=False)
    conns = []
    monkeypatch.setattr(auth, "connection", lambda e, r, t: conns.append(_Conn(r)) or conns[-1])
    monkeypatch.setattr(auth, "user_client", _fake_user_client("u@example.com"))
    assert c.get("/api/kpis", params={"role": "admins"}, headers=H).status_code == 400
    r = c.get("/api/kpis", params={"role": "aops-station-BOG", "airport": "BOG"}, headers=H)
    assert r.status_code == 200 and r.json() == [{"airport_iata": "BOG", "dep_otp_d0": 0.9}]
    q, params = conns[-1].calls[-1]
    assert params == {"ap": "BOG", "h": 24} and "BOG" not in q
    c.get("/api/kpis", params={"role": "aops-station-BOG", "hours": 6}, headers=H)
    assert conns[-1].calls[-1][1] == {"ap": None, "h": 6}
    assert c.get("/api/kpis", params={"role": "aops-station-BOG", "hours": 48}, headers=H).status_code == 400
    assert c.get("/api/kpis", params={"role": "aops-station-BOG", "hours": "1;drop"}, headers=H).status_code == 422


def test_obo_section_and_airport_403(monkeypatch):
    c, routes, auth = _client(monkeypatch, mock=False)
    monkeypatch.setattr(auth, "connection", lambda e, r, t: _Conn(r))
    monkeypatch.setattr(auth, "user_client", _fake_user_client("u@example.com"))
    assert c.get("/api/connections", params={"role": "aops-station-BOG"}, headers=H).status_code == 403
    assert c.get("/api/kpis", params={"role": "aops-station-BOG", "airport": "PTY"}, headers=H).status_code == 403


def test_obo_db_denial_is_403_and_guard_is_503(monkeypatch):
    c, routes, auth = _client(monkeypatch, mock=False)
    monkeypatch.setattr(auth, "connection", lambda e, r, t: _Conn(r, deny=True))
    monkeypatch.setattr(auth, "user_client", _fake_user_client("u@example.com"))
    assert c.get("/api/flights", params={"role": "aops-station-BOG"}, headers=H).status_code == 403

    def bypass(e, r, t):
        raise auth.GovernanceError("rolbypassrls")
    monkeypatch.setattr(auth, "connection", bypass)
    assert c.get("/api/flights", params={"role": "aops-station-BOG"}, headers=H).status_code == 503


def test_governance_warnings_detects_bypass():
    from server.auth import governance_warnings

    class C:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, q):
            r = self.rows.pop(0)
            return type("R", (), {"fetchone": lambda s: r})()
    assert governance_warnings(C([("g", False, False), None])) == []
    assert governance_warnings(C([("admin", False, True), None]))
    assert governance_warnings(C([("g", False, False), (1,)]))
