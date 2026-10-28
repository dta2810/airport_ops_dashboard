"""Genie side panel API. Genie runs as the user (OBO token): the genie_* views filter rows by the user's groups.
History lives in Lakebase schema chat (server/chat_store.py), scoped per user."""

import csv
import io
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from server import auth
from server.chat_store import MemoryStore, PgStore

router = APIRouter(prefix="/genie")
SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"
logger = logging.getLogger(__name__)

# MOCK_MODE + Databricks Apps environment is unsafe; refuse to start
if MOCK_MODE and os.environ.get("DATABRICKS_APP_NAME"):
    raise RuntimeError("MOCK_MODE is unsafe inside Databricks Apps; refusing to start")
MAX_ROWS = 50
ID = re.compile(r"^[0-9a-f]{32}$")
DONE = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}
SAMPLES = [
    "What is the departure OTP D15 by airport in the last 24 hours?",
    "Which flights are more than 15 minutes late and still on the ground?",
    "What is the connection success rate at the hub today?",
    "Which delay causes cost the most minutes today?",
]

_store = None


def store():
    global _store
    if _store is None:
        _store = MemoryStore() if MOCK_MODE else PgStore()
    return _store


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = None
    role: str | None = Field(default=None, max_length=100)


def _who(request: Request):
    from server.routes import _identity  # noqa: PLC0415 (shared identity rules)
    token, email = _identity(request)
    if token is None and not MOCK_MODE:
        raise HTTPException(401, "Sign-in required")
    return token, email


def _id(v: str, what: str):
    if not ID.match(v or ""):
        raise HTTPException(400, f"Invalid {what}")
    return v


def _genie(token):
    if not SPACE_ID:
        raise HTTPException(503, "Genie space not configured")
    return auth.user_client(token).genie


def _err(e: Exception) -> HTTPException:
    from server.routes import _clean  # noqa: PLC0415
    msg = str(e)
    if "403" in msg or "PERMISSION_DENIED" in msg or "required scopes" in msg:
        return HTTPException(403, "Genie is not available for your account (permission or consent missing)")
    logger.error("genie request failed: %s", _clean(msg))
    return HTTPException(502, "Genie request failed")


def parse(msg, fetch_result) -> dict:
    # Genie message -> answer text, SQL, first rows, follow-up suggestions
    status = getattr(msg.status, "value", str(msg.status)) if msg.status else "UNKNOWN"
    out = {"status": status, "answer": None, "sql": None, "columns": None, "rows": None, "row_count": None,
           "suggestions": None, "error": None, "viz_attachment_id": None, "query_attachment_id": None}
    if msg.error:
        out["error"] = getattr(msg.error, "error", None) or str(msg.error)
    texts, desc = [], None
    for a in msg.attachments or []:
        if a.text and a.text.content:
            texts.append(a.text.content)
        if getattr(a, "viz", None) and status == "COMPLETED":
            out["viz_attachment_id"] = a.attachment_id
        if a.suggested_questions and a.suggested_questions.questions:
            out["suggestions"] = list(a.suggested_questions.questions)[:3]
        if a.query and a.query.query and status == "COMPLETED":
            out["sql"], out["query_attachment_id"] = a.query.query, a.attachment_id
            desc = a.query.description  # restates the question: only a fallback when there is no text
            try:
                res = fetch_result(a.attachment_id)
                sr = res.statement_response
                cols = [c.name for c in (sr.manifest.schema.columns or [])] if sr and sr.manifest and sr.manifest.schema else []
                data = (sr.result.data_array or []) if sr and sr.result else []
                out["columns"], out["rows"] = cols, data[:MAX_ROWS]
                out["row_count"] = (sr.manifest.total_row_count if sr.manifest and sr.manifest.total_row_count is not None else len(data))
            except Exception as e:  # answer text still useful without the table
                out["error"] = f"Could not load the result table: {type(e).__name__}"
    out["answer"] = "\n\n".join(texts) or desc
    return out


@router.get("/config")
def config():
    return {"enabled": bool(SPACE_ID) or MOCK_MODE, "samples": SAMPLES}


@router.post("/ask")
def ask(body: Ask, request: Request):
    token, email = _who(request)
    q = body.question.strip()
    if body.conversation_id:
        cid = _id(body.conversation_id, "conversation id")
        if not store().owns(email, cid):
            raise HTTPException(404, "Conversation not found")
    if token is None:
        from server import mock_data  # noqa: PLC0415
        cid = body.conversation_id or uuid.uuid4().hex
        mid = uuid.uuid4().hex
        store().add_question(email, cid, mid, q, body.role, new=not body.conversation_id)
        store().save_answer(email, mid, mock_data.genie_answer(q))
        return {"conversation_id": cid, "message_id": mid}
    g = _genie(token)
    try:
        if body.conversation_id:
            m = g.create_message(SPACE_ID, cid, q, enable_visualization=True).response
            mid = m.message_id or m.id
        else:
            r = g.start_conversation(SPACE_ID, q, enable_visualization=True).response
            cid, mid = r.conversation_id, r.message_id
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e)
    store().add_question(email, cid, mid, q, body.role, new=not body.conversation_id)
    return {"conversation_id": cid, "message_id": mid}


@router.get("/messages/{cid}/{mid}")
def message(cid: str, mid: str, request: Request):
    token, email = _who(request)
    _id(cid, "conversation id"), _id(mid, "message id")
    saved = store().get_message(email, mid)
    if not saved or saved["conversation_id"] != cid:
        raise HTTPException(404, "Message not found")
    if saved["status"] in DONE or token is None:
        return saved
    g = _genie(token)
    try:
        msg = g.get_message(SPACE_ID, cid, mid)
        status = getattr(msg.status, "value", str(msg.status))
        if status not in DONE:
            return {**saved, "status": status}
        a = parse(msg, lambda att: g.get_message_attachment_query_result(SPACE_ID, cid, mid, att))
    except Exception as e:
        raise _err(e)
    store().save_answer(email, mid, a)
    if a.get("viz_attachment_id"):
        _prefetch(token, email, cid, mid, a["viz_attachment_id"])  # chart ready by the time the <img> asks
    return store().get_message(email, mid)


# one PNG download per message, shared by the prefetch and the /viz request
_pool = ThreadPoolExecutor(max_workers=4)
_inflight: dict = {}
_inflight_lock = threading.Lock()


def _download_viz(token, email, cid, mid, att):
    name = f"spaces/{SPACE_ID}/conversations/{cid}/messages/{mid}/attachments/{att}"
    png = _genie(token).download_message_attachment_visualization(name).contents.read()
    store().save_viz(email, mid, png)
    return png


def _prefetch(token, email, cid, mid, att):
    with _inflight_lock:
        fut = _inflight.get(mid)
        if fut is None:
            fut = _inflight[mid] = _pool.submit(_download_viz, token, email, cid, mid, att)
            # a fast failure may run this callback before we return: return fut itself, not the dict entry
            fut.add_done_callback(lambda _f: _inflight.pop(mid, None))
        return fut


@router.get("/viz/{cid}/{mid}")
def viz(cid: str, mid: str, request: Request):
    # Genie renders the chart as PNG; fetched once with the user's token, then served from Lakebase
    token, email = _who(request)
    _id(cid, "conversation id"), _id(mid, "message id")
    saved = store().get_message(email, mid)
    if not saved or saved["conversation_id"] != cid or not saved.get("viz_attachment_id"):
        raise HTTPException(404, "No chart for this message")
    png = store().get_viz(email, mid)
    if png is None:
        if token is None:
            from server import mock_data  # noqa: PLC0415
            png = mock_data.VIZ_PNG
            store().save_viz(email, mid, png)
        else:
            fut = _prefetch(token, email, cid, mid, saved["viz_attachment_id"])
            try:
                png = fut.result(timeout=60)
            except Exception as e:
                raise _err(e)
    if not png:
        raise HTTPException(502, "Chart not available")  # never cache an empty image
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


_FORMULA = re.compile(r"^[=+@\t\r]|^-(?!\d)")


def _cell(v):
    # neutralise spreadsheet formulas; numbers (incl. negatives) stay numeric
    return "'" + v if isinstance(v, str) and _FORMULA.match(v) else v


@router.get("/csv/{cid}/{mid}")
def export_csv(cid: str, mid: str, request: Request):
    # full Genie result when still available, otherwise the rows kept in the history
    token, email = _who(request)
    _id(cid, "conversation id"), _id(mid, "message id")
    saved = store().get_message(email, mid)
    if not saved or saved["conversation_id"] != cid or not saved.get("columns"):
        raise HTTPException(404, "No data for this message")
    cols, rows, source = saved["columns"], saved.get("rows") or [], "history"
    if token and saved.get("query_attachment_id"):
        try:
            sr = _genie(token).get_message_attachment_query_result(SPACE_ID, cid, mid, saved["query_attachment_id"]).statement_response
            if sr and sr.result and sr.result.data_array is not None:
                rows, source = sr.result.data_array, "genie"
        except Exception:
            pass  # expired or unavailable: fall back to the stored rows
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows([[_cell(v) for v in r] for r in rows])
    slug = re.sub(r"[^a-z0-9]+", "-", saved["question"].lower()).strip("-")[:50] or "genie"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{slug}.csv"', "Cache-Control": "private, no-store",
        "X-Rows": str(len(rows)), "X-Source": source})


@router.get("/conversations")
def conversations(request: Request):
    _, email = _who(request)
    return {"conversations": store().list_conversations(email)}


@router.get("/conversations/{cid}")
def conversation(cid: str, request: Request):
    _, email = _who(request)
    c = store().get_conversation(email, _id(cid, "conversation id"))
    if not c:
        raise HTTPException(404, "Conversation not found")
    return c


@router.delete("/conversations/{cid}")
def delete(cid: str, request: Request):
    token, email = _who(request)
    if not store().delete_conversation(email, _id(cid, "conversation id")):
        raise HTTPException(404, "Conversation not found")
    if token:
        try:
            _genie(token).delete_conversation(SPACE_ID, cid)
        except Exception:
            pass  # local history is gone either way
    return {"deleted": cid}
