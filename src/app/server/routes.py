"""API routes. Every data query runs as the user's group role (OBO); RLS + grants in Lakebase enforce access."""

import hashlib
import logging
import os
import re
import time

import psycopg
from fastapi import APIRouter, HTTPException, Request

from server import auth

logger = logging.getLogger(__name__)

router = APIRouter()
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"

# mock data must never serve real users
if MOCK_MODE and os.environ.get("DATABRICKS_APP_NAME"):
    raise RuntimeError("MOCK_MODE is unsafe inside Databricks Apps; refusing to start")

ACCESS_GROUPS = [g.strip() for g in os.environ.get("ACCESS_GROUPS", "").split(",") if g.strip()]
WINDOW_H = 24
WINDOWS = (6, 12, 24)
IATA = re.compile(r"^[A-Z]{3}$")

# endpoint -> contract §7 section
SECTION = {"kpis": "overview", "flights": "flights", "hourly": "airport_kpis", "gates": "gates",
           "delays": "delays", "connections": "connections"}

# ops clock = latest event seen; the window is relative to it (the simulator runs on an accelerated clock)
_CLOCK = "(SELECT max(last_event_ts) FROM ops.flight_state)"
_WIN = f"BETWEEN {_CLOCK} - make_interval(hours => %(h)s) AND {_CLOCK}"
_AP = "(%(ap)s::text IS NULL OR {col} = %(ap)s)"

SQL = {
    "kpis": f"""
        SELECT airport_iata,
          sum(dep_otp_d0 * departed) / nullif(sum(departed) FILTER (WHERE dep_otp_d0 IS NOT NULL), 0) AS dep_otp_d0,
          sum(dep_otp_d15 * departed) / nullif(sum(departed) FILTER (WHERE dep_otp_d15 IS NOT NULL), 0) AS dep_otp_d15,
          sum(arr_otp_d0 * arrived) / nullif(sum(arrived) FILTER (WHERE arr_otp_d0 IS NOT NULL), 0) AS arr_otp_d0,
          sum(arr_otp_d15 * arrived) / nullif(sum(arrived) FILTER (WHERE arr_otp_d15 IS NOT NULL), 0) AS arr_otp_d15,
          sum(avg_taxi_out_min * departed) / nullif(sum(departed) FILTER (WHERE avg_taxi_out_min IS NOT NULL), 0) AS avg_taxi_out,
          sum(avg_taxi_in_min * arrived) / nullif(sum(arrived) FILTER (WHERE avg_taxi_in_min IS NOT NULL), 0) AS avg_taxi_in,
          sum(departed) AS departed, sum(arrived) AS arrived,
          sum(cancellations) AS cancellations, sum(diversions) AS diversions
        FROM ops.airport_hour_kpis
        WHERE hour_start_utc {_WIN} AND {_AP.format(col='airport_iata')}
        GROUP BY airport_iata ORDER BY airport_iata""",
    "hourly": f"""
        SELECT hour_start_utc, sum(dep_scheduled) AS dep_scheduled, sum(departed) AS departed,
               sum(arr_scheduled) AS arr_scheduled, sum(arrived) AS arrived
        FROM ops.airport_hour_kpis
        WHERE hour_start_utc {_WIN} AND {_AP.format(col='airport_iata')}
        GROUP BY hour_start_utc ORDER BY hour_start_utc""",
    "flights": f"""
        SELECT flight_id, flight_number, origin_iata, destination_iata, tail_number, phase, std_utc, sta_utc,
               gate_dep, gate_arr, boarding_start_ts, aobt, atot, aldt, aibt,
               dep_delay_min, arr_delay_min, delay_cause, taxi_out_min, taxi_in_min,
               turnaround_min, turnaround_ok, last_event_ts
        FROM ops.flight_state
        WHERE std_utc BETWEEN {_CLOCK} - make_interval(hours => %(h)s) AND {_CLOCK} + interval '6 hours'
          AND (%(ap)s::text IS NULL OR origin_iata = %(ap)s OR destination_iata = %(ap)s)
        ORDER BY std_utc DESC LIMIT 500""",
    "gates": f"""
        SELECT airport_iata, gate_id, hour_start_utc, occupied_minutes, flights
        FROM ops.gate_hour_occupancy
        WHERE hour_start_utc {_WIN} AND {_AP.format(col='airport_iata')}
        ORDER BY airport_iata, gate_id, hour_start_utc""",
    "delays": f"""
        SELECT delay_cause, sum(greatest(dep_delay_min, 0)) AS total_delay_minutes, count(*) AS flights
        FROM ops.flight_state
        WHERE delay_cause IS NOT NULL AND aobt {_WIN}
          AND (%(ap)s::text IS NULL OR origin_iata = %(ap)s)
        GROUP BY delay_cause ORDER BY total_delay_minutes DESC""",
    "connections": f"""
        SELECT hub_iata, hour_start_utc, connections, pax_connecting, connections_at_risk, pax_at_risk,
               connections_missed, pax_missed
        FROM ops.hub_connections
        WHERE hour_start_utc {_WIN} AND {_AP.format(col='hub_iata')}
        ORDER BY hour_start_utc DESC""",
}

_SCRUB = [
    (re.compile(r"\.?\s*Config:.*", re.S), ""),
    (re.compile(r"\s*Env:.*", re.S), ""),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"(eyJ[A-Za-z0-9_-]{10})[A-Za-z0-9_.-]+"), r"\1<redacted>"),
    (re.compile(r"\b[\w.-]+\.(?:azure)?databricks\.(?:net|com)\b", re.I), "<host>"),
    (re.compile(r"\bpostgresql://[^\s]+"), "<connection>"),
]


def _clean(msg: str, limit: int = 300) -> str:
    for pat, repl in _SCRUB:
        msg = pat.sub(repl, msg)
    msg = " ".join(msg.split())
    return msg[:limit] + ("…" if len(msg) > limit else "")


def _identity(request: Request):
    token = request.headers.get("x-forwarded-access-token")
    if not token:
        if MOCK_MODE:
            return None, "mock"
        raise HTTPException(401, "Sign-in required (no forwarded user token)")

    # identity comes from the token; the proxy header must agree with it
    key, now = hashlib.sha256(token.encode()).hexdigest()[:16], time.time()
    hit = _identity_cache.get(key)
    if hit and now - hit[0] < _IDENTITY_CACHE_TTL:
        email = hit[1]
    else:
        try:
            email = auth.user_client(token).current_user.me().user_name
        except Exception as e:
            logger.error("identity lookup failed: %s", type(e).__name__)
            raise HTTPException(401, "Invalid or expired token")
        if len(_identity_cache) >= _IDENTITY_CACHE_MAX:
            _identity_cache.pop(min(_identity_cache, key=lambda k: _identity_cache[k][0]))
        _identity_cache[key] = (now, email)
    header = request.headers.get("x-forwarded-email")
    if header and header.lower() != email.lower():
        logger.warning("identity mismatch between x-forwarded-email and token")
        raise HTTPException(401, "Identity mismatch")
    return token, email


def _policy(conn) -> dict:
    # RLS on access_policy returns only the connected role's own row
    row = conn.execute("SELECT label, airports, sections, priority FROM ops.access_policy").fetchone()
    if not row:
        raise HTTPException(403, "No access policy for this role")
    return {"label": row[0], "airports": list(row[1]), "sections": list(row[2]), "priority": row[3]}


_roles_cache: dict = {}  # email -> (time, result)
_ROLES_CACHE_TTL = 300
_identity_cache: dict = {}  # sha256(token)[:16] -> (time, email)
_IDENTITY_CACHE_TTL = 600  # 10 min
_IDENTITY_CACHE_MAX = 1000


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/roles")
def roles(request: Request):
    token, email = _identity(request)
    if token is None:
        from server.mock_data import MOCK_ROLES
        return {"roles": [{"role": r, **v} for r, v in MOCK_ROLES.items()]}

    now = time.time()
    for k in [k for k, (t, _) in _roles_cache.items() if now - t >= _ROLES_CACHE_TTL]:
        del _roles_cache[k]
    if email in _roles_cache:
        return _roles_cache[email][1]

    out, transient = [], False
    for group in ACCESS_GROUPS:
        try:
            auth.mint(token, group)  # fail fast: a refused mint would otherwise block on the pool timeout
        except Exception:
            continue  # not a member: the platform refuses the mint
        try:
            with auth.connection(email, group, token) as conn:
                out.append({"role": group, **_policy(conn)})
        except Exception:
            transient = True  # member, but Lakebase/policy failed (e.g. cold start): do not cache
    res = {"roles": sorted(out, key=lambda r: r["priority"])}
    if not transient:
        _roles_cache[email] = (now, res)
    return res


def _check_inputs(role: str, airport: str | None, hours: int = WINDOW_H):
    if role not in ACCESS_GROUPS and not MOCK_MODE:
        raise HTTPException(400, "Unknown role")
    if hours not in WINDOWS:
        raise HTTPException(400, "Invalid window")
    if airport is not None and not IATA.match(airport):
        raise HTTPException(400, "Invalid airport code")


def _allowed(policy: dict, endpoint: str, airport: str | None):
    if SECTION[endpoint] not in policy["sections"]:
        raise HTTPException(403, "Section not available for this role")
    if airport and "*" not in policy["airports"] and airport not in policy["airports"]:
        raise HTTPException(403, "Airport not available for this role")


def _data(endpoint: str, request: Request, role: str, airport: str | None, hours: int = WINDOW_H):
    _check_inputs(role, airport, hours)
    token, email = _identity(request)
    if token is None:
        from server import mock_data
        policy = mock_data.MOCK_ROLES.get(role) or HTTPException(400, "Unknown role")
        if isinstance(policy, HTTPException):
            raise policy
        _allowed(policy, endpoint, airport)
        return mock_data.rows(endpoint, policy["airports"], airport, hours)
    try:
        with auth.connection(email, role, token) as conn:
            _allowed(_policy(conn), endpoint, airport)
            cur = conn.execute(SQL[endpoint], {"ap": airport, "h": hours})
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except HTTPException:
        raise
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(403, "Not permitted for this role")
    except auth.GovernanceError:
        raise HTTPException(503, "Connection identity bypasses row-level security; refusing to serve data")
    except Exception as e:
        logger.error("data query %s failed: %s", endpoint, _clean(str(e)))
        raise HTTPException(502, "Data service unavailable")


def _route(endpoint):
    def handler(request: Request, role: str, airport: str | None = None, hours: int = WINDOW_H):
        return _data(endpoint, request, role, airport, hours)
    handler.__name__ = f"get_{endpoint}"
    return handler


for _ep in SECTION:
    router.add_api_route(f"/{_ep}", _route(_ep), methods=["GET"])


@router.get("/freshness")
def freshness(request: Request, role: str):
    _check_inputs(role, None)
    token, email = _identity(request)
    if token is None:
        return {"written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        with auth.connection(email, role, token) as conn:
            row = conn.execute("SELECT max(written_at) FROM ops.batch_log").fetchone()
            return {"written_at": row[0]}
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(403, "Not permitted for this role")
    except auth.GovernanceError:
        raise HTTPException(503, "Connection identity bypasses row-level security; refusing to serve data")
    except Exception as e:
        logger.error("freshness query failed: %s", _clean(str(e)))
        raise HTTPException(502, "Data service unavailable")
