# Genie panel API: mock flow over HTTP, OBO flow with a fake Genie client, parse() against real SDK dataclasses.
import importlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace as NS

from databricks.sdk.service.dashboards import GenieGetMessageQueryResultResponse, GenieMessage
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/app"))
H = {"x-forwarded-access-token": "tok", "x-forwarded-email": "a@example.com"}
CID, MID = "c" * 32, "d" * 32


def _app(monkeypatch, mock, space="f" * 32):
    monkeypatch.setenv("MOCK_MODE", "true" if mock else "false")
    monkeypatch.setenv("GENIE_SPACE_ID", space)
    monkeypatch.setenv("ACCESS_GROUPS", "aops-ops-control")
    for m in [m for m in sys.modules if m == "app" or m.startswith("server")]:
        del sys.modules[m]
    app = importlib.import_module("app")
    g = importlib.import_module("server.genie")
    if not mock:
        from server.chat_store import MemoryStore
        g._store = MemoryStore()
    return TestClient(app.app), g


def test_mock_flow(monkeypatch):
    c, _ = _app(monkeypatch, mock=True)
    assert c.get("/api/genie/config").json()["enabled"]
    r = c.post("/api/genie/ask", json={"question": "OTP?", "role": "aops-ops-control"}).json()
    m = c.get(f"/api/genie/messages/{r['conversation_id']}/{r['message_id']}").json()
    assert m["status"] == "COMPLETED" and m["rows"] and m["sql"]
    assert c.get(f"/api/genie/viz/{r['conversation_id']}/{r['message_id']}").headers["content-type"] == "image/png"
    csvr = c.get(f"/api/genie/csv/{r['conversation_id']}/{r['message_id']}")
    assert csvr.status_code == 200 and csvr.text.startswith("airport_iata,departures")
    r2 = c.post("/api/genie/ask", json={"question": "and BOG?", "conversation_id": r["conversation_id"]}).json()
    assert r2["conversation_id"] == r["conversation_id"]
    convs = c.get("/api/genie/conversations").json()["conversations"]
    assert [x["title"] for x in convs] == ["OTP?"]
    assert len(c.get(f"/api/genie/conversations/{r['conversation_id']}").json()["messages"]) == 2
    assert c.delete(f"/api/genie/conversations/{r['conversation_id']}").status_code == 200
    assert c.get(f"/api/genie/conversations/{r['conversation_id']}").status_code == 404


def test_rejects_bad_ids_and_foreign_conversations(monkeypatch):
    c, _ = _app(monkeypatch, mock=True)
    assert c.get("/api/genie/conversations/not-an-id").status_code == 400
    assert c.get("/api/genie/messages/xyz/abc").status_code == 400
    assert c.post("/api/genie/ask", json={"question": "q", "conversation_id": "e" * 32}).status_code == 404
    assert c.post("/api/genie/ask", json={"question": ""}).status_code == 422


class FakeGenie:
    def __init__(self):
        self.polls = 0
        self.downloads = []

    def start_conversation(self, space, q, enable_visualization=None):
        assert enable_visualization is True
        return NS(response=NS(conversation_id=CID, message_id=MID))

    def download_message_attachment_visualization(self, name):
        self.downloads.append(name)
        return NS(contents=io.BytesIO(b'\x89PNGfake'))

    def get_message(self, space, cid, mid):
        self.polls += 1
        if self.polls == 1:
            return GenieMessage.from_dict({"id": mid, "conversation_id": cid, "content": "q", "status": "EXECUTING_QUERY"})
        return GenieMessage.from_dict({"id": mid, "conversation_id": cid, "content": "q", "status": "COMPLETED", "attachments": [
            {"attachment_id": "att1", "query": {"query": "SELECT 1", "description": "desc"}},
            {"attachment_id": "att2", "text": {"content": "PTY is **80%**"}},
            {"attachment_id": "att3", "suggested_questions": {"questions": ["a?", "b?"]}},
            {"attachment_id": "att4", "viz": {"query_attachment_id": "att1"}}]})

    def get_message_attachment_query_result(self, space, cid, mid, att):
        assert att == "att1"
        return GenieGetMessageQueryResultResponse.from_dict({"statement_response": {
            "manifest": {"schema": {"columns": [{"name": "airport_iata"}, {"name": "otp"}]}, "total_row_count": 2},
            "result": {"data_array": [["PTY", "0.8"], ["BOG", "0.7"], ["=HYPERLINK(1)", "-5"]]}}})


def test_obo_flow_polls_then_stores(monkeypatch):
    c, g = _app(monkeypatch, mock=False)
    fake = FakeGenie()
    # mock user_client to return different identity based on token
    def fake_user_client(tok):
        # token "tok" -> a@example.com, token "tok2" -> b@example.com
        email = "b@example.com" if tok == "tok2" else "a@example.com"
        return NS(genie=fake, current_user=NS(me=lambda: NS(user_name=email)))
    monkeypatch.setattr(g.auth, "user_client", fake_user_client)
    r = c.post("/api/genie/ask", json={"question": "OTP?"}, headers=H).json()
    assert r == {"conversation_id": CID, "message_id": MID}
    assert c.get(f"/api/genie/messages/{CID}/{MID}", headers=H).json()["status"] == "EXECUTING_QUERY"
    m = c.get(f"/api/genie/messages/{CID}/{MID}", headers=H).json()
    assert m["status"] == "COMPLETED" and m["answer"] == "PTY is **80%**" and m["sql"] == "SELECT 1"
    assert m["columns"] == ["airport_iata", "otp"] and m["row_count"] == 2 and m["suggestions"] == ["a?", "b?"]
    assert m["query_attachment_id"] == "att1"
    c.get(f"/api/genie/messages/{CID}/{MID}", headers=H)
    assert fake.polls == 2  # completed answers come from the store
    assert m["viz_attachment_id"] == "att4"
    for _ in range(2):
        img = c.get(f"/api/genie/viz/{CID}/{MID}", headers=H)
        assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content == b"\x89PNGfake"
    assert fake.downloads == [f"spaces/{'f' * 32}/conversations/{CID}/messages/{MID}/attachments/att4"]  # second from store
    # identity mismatch (spoofed header with different email) -> 401
    spoofed = {**H, "x-forwarded-email": "b@example.com"}
    assert c.get(f"/api/genie/viz/{CID}/{MID}", headers=spoofed).status_code == 401
    # other user with valid token -> 404 (data not found)
    other = {"x-forwarded-access-token": "tok2", "x-forwarded-email": "b@example.com"}
    # CSV: full Genie result, formulas neutralised, negatives kept numeric
    r = c.get(f"/api/genie/csv/{CID}/{MID}", headers=H)
    assert r.status_code == 200 and r.headers["x-source"] == "genie" and r.headers["x-rows"] == "3"
    assert r.text.splitlines() == ["airport_iata,otp", "PTY,0.8", "BOG,0.7", "'=HYPERLINK(1),-5"]
    assert 'filename="otp.csv"' in r.headers["content-disposition"]
    assert c.get(f"/api/genie/csv/{CID}/{MID}", headers=other).status_code == 404
    fake.get_message_attachment_query_result = lambda *a: (_ for _ in ()).throw(RuntimeError("expired"))
    r = c.get(f"/api/genie/csv/{CID}/{MID}", headers=H)
    assert r.headers["x-source"] == "history" and r.text.splitlines()[0] == "airport_iata,otp"
    assert c.get(f"/api/genie/messages/{CID}/{MID}", headers=other).status_code == 404
    assert c.get("/api/genie/conversations", headers=other).json()["conversations"] == []


def test_obo_requires_token(monkeypatch):
    c, _ = _app(monkeypatch, mock=False)
    assert c.post("/api/genie/ask", json={"question": "q"}).status_code == 401


def test_permission_error_is_403(monkeypatch):
    c, g = _app(monkeypatch, mock=False)

    class Denied(FakeGenie):
        def start_conversation(self, space, q, enable_visualization=None):
            raise RuntimeError("PERMISSION_DENIED: Provided OAuth token does not have required scopes: genie")
    def fake_user_client(tok):
        return NS(genie=Denied(), current_user=NS(me=lambda: NS(user_name="a@example.com")))
    monkeypatch.setattr(g.auth, "user_client", fake_user_client)
    r = c.post("/api/genie/ask", json={"question": "q"}, headers=H)
    assert r.status_code == 403 and "consent" in r.json()["detail"]
