# SPA catch-all must never serve files outside frontend/dist (found by security review: /%2Fetc/hosts leaked).
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[1] / "src/app"
sys.path.insert(0, str(APP))
DIST = APP / "frontend" / "dist"


@pytest.fixture
def client(monkeypatch):
    if not (DIST / "index.html").exists():
        pytest.skip("frontend not built")
    monkeypatch.setenv("MOCK_MODE", "true")
    for m in [m for m in sys.modules if m == "app" or m.startswith("server")]:
        del sys.modules[m]
    return TestClient(importlib.import_module("app").app)


@pytest.mark.parametrize("path", ["/%2Fetc/hosts", "/%2Fproc/self/environ", "/..%2fserver/auth.py", "/%2e%2e/server/auth.py",
                                  "/..%2f..%2f..%2fREADME.md", "/assets/..%2f..%2fapp.py", "//etc/hosts"])
def test_traversal_returns_index(client, path):
    r = client.get(path)
    assert r.status_code in (200, 404) and (r.status_code == 404 or "<div id=\"root\"" in r.text or "<!doctype html" in r.text.lower())
    assert "Host Database" not in r.text and "import " not in r.text and "DATABRICKS" not in r.text


def test_real_assets_still_served(client):
    asset = next((DIST / "assets").glob("*.js"))
    assert client.get(f"/assets/{asset.name}").status_code == 200
    assert client.get("/index.html").status_code == 200
