# Security fixes tests: identity, error messages, governance, isolation
import hashlib
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace as NS

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


def _fake_user_client_fn(email):
    def _uc(token):
        return _FakeUserClient(email)
    return _uc


def test_identity_mismatch_header_vs_token_is_401(monkeypatch):
    """If x-forwarded-email differs from token identity, reject with 401."""
    c, routes, auth = _client(monkeypatch, mock=False)
    monkeypatch.setattr(auth, "user_client", _fake_user_client_fn("a@example.com"))
    # Token claims a@example.com but header says b@example.com
    headers = {"x-forwarded-access-token": "tok", "x-forwarded-email": "b@example.com"}
    r = c.get("/api/roles", headers=headers)
    assert r.status_code == 401


def test_identity_no_token_requires_mock_mode(monkeypatch):
    """Without token and not in MOCK_MODE, requests fail with 401."""
    c, routes, auth = _client(monkeypatch, mock=False)
    # no token, no MOCK_MODE -> 401
    r = c.get("/api/roles")
    assert r.status_code == 401


def test_identity_cached_by_token_sha256(monkeypatch):
    """Token identity is cached; current_user.me() called once per unique token."""
    c, routes, auth = _client(monkeypatch, mock=False)

    call_count = [0]

    class CountingUser:
        user_name = "a@example.com"

    class CountingCurrentUser:
        def me(self):
            call_count[0] += 1
            return CountingUser()

    class CountingUserClient:
        current_user = CountingCurrentUser()

    monkeypatch.setattr(auth, "user_client", lambda token: CountingUserClient())

    headers = {"x-forwarded-access-token": "tok", "x-forwarded-email": "a@example.com"}
    # First request: cache miss, calls current_user.me()
    c.get("/api/roles", headers=headers)
    assert call_count[0] == 1
    # Second request with same token: cache hit, does not call current_user.me()
    c.get("/api/roles", headers=headers)
    assert call_count[0] == 1
    # Third request with different token: cache miss, calls current_user.me()
    headers2 = {"x-forwarded-access-token": "tok2", "x-forwarded-email": "a@example.com"}
    c.get("/api/roles", headers=headers2)
    assert call_count[0] == 2


def test_error_message_generic_no_hostname(monkeypatch):
    """502 errors return generic message, not hostname/connection details."""
    c, routes, auth = _client(monkeypatch, mock=False)

    class FailingConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, q, params=None):
            raise RuntimeError("Failed to connect to instance-abc.database.azuredatabricks.net:5432")

    monkeypatch.setattr(auth, "user_client", _fake_user_client_fn("a@example.com"))
    monkeypatch.setattr(auth, "connection", lambda e, r, t: FailingConn())

    headers = {"x-forwarded-access-token": "tok", "x-forwarded-email": "a@example.com"}
    r = c.get("/api/flights", params={"role": "aops-station-BOG"}, headers=headers)
    assert r.status_code == 502
    # Error message should be generic, not contain hostname
    assert "Data service unavailable" in r.json()["detail"]
    assert "database.azuredatabricks.net" not in r.json()["detail"]
    assert "instance-abc" not in r.json()["detail"]


def test_mock_mode_with_databricks_app_name_raises(monkeypatch):
    """MOCK_MODE + DATABRICKS_APP_NAME in env -> RuntimeError on module import."""
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("DATABRICKS_APP_NAME", "my-app")
    monkeypatch.setenv("ACCESS_GROUPS", GROUPS)

    # Clean up modules
    for m in [m for m in sys.modules if m == "app" or m.startswith("server")]:
        del sys.modules[m]

    # Importing routes should raise
    import pytest
    with pytest.raises(RuntimeError, match="MOCK_MODE is unsafe"):
        importlib.import_module("server.routes")


def test_mock_mode_genie_with_databricks_app_name_raises(monkeypatch):
    """MOCK_MODE + DATABRICKS_APP_NAME in genie.py -> RuntimeError on module import."""
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("DATABRICKS_APP_NAME", "my-app")
    monkeypatch.setenv("ACCESS_GROUPS", GROUPS)
    monkeypatch.setenv("GENIE_SPACE_ID", "f" * 32)

    # Clean up modules
    for m in [m for m in sys.modules if m == "app" or m.startswith("server")]:
        del sys.modules[m]

    # Importing genie should raise
    import pytest
    with pytest.raises(RuntimeError, match="MOCK_MODE is unsafe"):
        importlib.import_module("server.genie")
