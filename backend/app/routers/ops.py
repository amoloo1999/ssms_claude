"""Operational surfaces: the session/lock monitor, kill-session, and the audit log.

These are gated harder than anything else in the app, for two different reasons.

The session monitor shows every user's in-flight statement. On PROD-MAIN that
text can contain customer data in literals, so it is RevMan-only — it is not
something a marketing or accounting login, or an external Uniti contractor,
should be able to read.

Killing a session is the only destructive action in the whole feature set: it
rolls back somebody else's open transaction on production. Approver-only, a
reason is required, and the audit write is critical — if the event cannot be
recorded, the kill does not happen, because the justification for allowing it at
all is that it always leaves a trace.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import require_auth, require_approver
from app.config import is_revman
from app.database import get_db
from app.models import (
    AuditEvent,
    AuditEventResponse,
    KillSessionRequest,
    ServerConnection,
)
from app.services.audit import record
from app.services.connection import get_connection_string, execute_query_async
from app.services.drivers import get_driver
from app.services.permissions import can_access_server

router = APIRouter(prefix="/api/ops", tags=["ops"])


async def _load_server(db: AsyncSession, user: dict, server_id: int) -> ServerConnection:
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _require_revman(user: dict) -> None:
    if not is_revman(user.get("email", "")):
        # The monitor exposes other users' statement text; 403 rather than an
        # empty list, so it is clear this is a permission boundary.
        raise HTTPException(
            status_code=403,
            detail="The session monitor shows other users' in-flight SQL and is limited to Revenue Management.",
        )


# ── Sessions and locks ──────────────────────────────────────────────────────


@router.get("/servers/{server_id}/sessions")
async def list_sessions(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    _require_revman(user)
    server = await _load_server(db, user, server_id)
    driver = get_driver(server.dialect)

    if not driver.supports("session_monitor"):
        return {"supported": False, "sessions": [], "chains": []}

    sql = driver.sessions_sql()
    if not sql:
        return {"supported": False, "sessions": [], "chains": []}

    conn_str = await get_connection_string(db, server_id, server.database or "master")
    result = await execute_query_async(conn_str, sql)
    if result["error"]:
        return {"supported": True, "error": result["error"], "sessions": [], "chains": []}

    sessions = [
        {
            "session_id": r[0],
            "login": r[1] or "",
            "host": r[2] or "",
            "program": r[3] or "",
            "database": r[4] or "",
            "status": r[5] or "",
            "blocked_by": r[6] or 0,
            "wait_type": r[7] or "",
            "elapsed_ms": r[8] or 0,
            "open_transactions": r[9] or 0,
            "statement": (r[10] or "").strip(),
        }
        for r in result["rows"]
    ]

    # Assemble blocking chains server-side: for each blocked session, walk up
    # blocked_by until a session that isn't itself blocked — the head blocker.
    by_id = {s["session_id"]: s for s in sessions}
    chains: list[dict] = []
    for s in sessions:
        if not s["blocked_by"]:
            continue
        waiter = s
        seen = {waiter["session_id"]}
        head = by_id.get(waiter["blocked_by"])
        # A cycle would loop forever; SQL Server resolves deadlocks itself, but
        # a stale snapshot can still look circular.
        while head and head["blocked_by"] and head["session_id"] not in seen:
            seen.add(head["session_id"])
            head = by_id.get(head["blocked_by"])
        if head:
            chains.append({"head": head["session_id"], "waiter": s["session_id"]})

    blocked_ids = {c["waiter"] for c in chains}
    for s in sessions:
        s["blocking_count"] = sum(1 for c in chains if c["head"] == s["session_id"])
        s["is_blocked"] = s["session_id"] in blocked_ids

    return {"supported": True, "sessions": sessions, "chains": chains}


# ── Kill ────────────────────────────────────────────────────────────────────


@router.post("/kill")
async def kill_session(
    payload: KillSessionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    reason = (payload.reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(
            status_code=400,
            detail="A reason of at least 10 characters is required — it is recorded with the kill.",
        )

    server = await _load_server(db, user, payload.server_id)
    driver = get_driver(server.dialect)
    if not driver.supports("session_monitor"):
        raise HTTPException(status_code=400, detail="Not supported for this connection's engine.")

    sql = driver.kill_session_sql(int(payload.session_id))
    if not sql:
        raise HTTPException(status_code=400, detail="Not supported for this connection's engine.")

    conn_str = await get_connection_string(db, payload.server_id, server.database or "master")

    # Audit BEFORE the kill, and critically: if the event cannot be written the
    # kill does not happen. An untraceable termination of someone else's
    # transaction is worse than a failed one.
    await record(
        db,
        actor=user["email"],
        event_type="kill",
        server_id=server.id,
        server_name=server.name,
        detail=f"KILL {int(payload.session_id)}",
        reason=reason,
        result="attempted",
        critical=True,
    )

    result = await execute_query_async(conn_str, sql)
    if result["error"]:
        await record(
            db,
            actor=user["email"],
            event_type="kill",
            server_id=server.id,
            server_name=server.name,
            detail=f"KILL {int(payload.session_id)}",
            reason=reason,
            result="failed",
        )
        raise HTTPException(status_code=400, detail=result["error"])

    await record(
        db,
        actor=user["email"],
        event_type="kill",
        server_id=server.id,
        server_name=server.name,
        detail=f"KILL {int(payload.session_id)}",
        reason=reason,
        result="ok",
    )
    return {"killed": True, "session_id": payload.session_id}


# ── Audit log ───────────────────────────────────────────────────────────────


@router.get("/audit", response_model=list[AuditEventResponse])
async def list_audit(
    event_type: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(300, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    """Approver-only. Records who did what, and there is no delete endpoint —
    the log is append-only by construction, not by convention."""
    stmt = select(AuditEvent)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if search:
        stmt = stmt.where(
            AuditEvent.detail.ilike(f"%{search}%") | AuditEvent.actor.ilike(f"%{search}%")
        )
    stmt = stmt.order_by(AuditEvent.at.desc()).limit(limit)
    return (await db.execute(stmt)).scalars().all()
