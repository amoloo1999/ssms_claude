from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
import asyncio
from app.models import (
    QueryRequest,
    QueryResult,
    CancelRequest,
    ServerConnection,
    QueryHistory,
)
from app.services.connection import get_connection_string, execute_query_async, cancel_query
from app.services.drivers import get_driver
from app.services.permissions import (
    can_access_server,
    check_query_permissions,
    is_select_only,
)
from app.services.audit import record

router = APIRouter(prefix="/api/query", tags=["query"])


def _is_write(sql: str) -> bool:
    """Whether a statement mutates anything.

    Reuses the same denylist that gates view-only users, so "what counts as a
    write" has one definition rather than two that can drift apart.
    """
    ok, _ = is_select_only(sql or "")
    return not ok


@router.post("/execute", response_model=QueryResult)
async def execute_sql(
    query: QueryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == query.server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")

    allowed, payload = await check_query_permissions(
        db, user, query.server_id, query.database, query.sql,
        get_driver(server.dialect).default_schema_for(server.database),
        write_policy=server.write_policy or "read_write",
    )
    if not allowed:
        # A refused statement is worth recording — a pattern of denials is a
        # signal, whether it's someone needing a grant or something worse.
        await record(
            db,
            actor=user["email"],
            event_type="denied",
            server_id=server.id,
            server_name=server.name,
            database=query.database,
            detail=(query.sql or "")[:2000],
            reason=str(payload.get("detail", ""))[:500],
            result="denied",
        )
        raise HTTPException(status_code=403, detail=payload)

    conn_str = await get_connection_string(db, query.server_id, query.database)
    result = await execute_query_async(conn_str, query.sql, query_id=query.query_id)

    # Writes on any connection are recorded. Reads are not — they are already in
    # per-user history, and auditing every SELECT would bury the events that
    # matter under noise.
    if _is_write(query.sql) and not result.get("error"):
        await record(
            db,
            actor=user["email"],
            event_type="write",
            server_id=server.id,
            server_name=server.name,
            database=query.database,
            detail=(query.sql or "")[:2000],
            result="ok",
        )

    # Record the run. Deliberately after the permission check — a statement that
    # was refused never ran, so it isn't history. Failures ARE recorded: "what
    # was that query that errored yesterday" is most of why history is useful.
    # Wrapped because a history write must never turn a successful query into a
    # failed request.
    try:
        db.add(
            QueryHistory(
                user_email=user["email"],
                server_id=query.server_id,
                server_name=server.name,
                database=query.database,
                sql=query.sql,
                status="error" if result.get("error") else "ok",
                row_count=result.get("row_count") or 0,
                duration_ms=result.get("execution_time_ms") or 0,
                error=result.get("error"),
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()

    return QueryResult(**result)


@router.post("/plan")
async def explain_sql(
    query: QueryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Return the estimated execution plan for a statement.

    Safe against production by construction: SHOWPLAN_XML makes the server
    return the plan INSTEAD of executing the statement, so no row is read or
    written. The permission check still runs first — the plan text names the
    objects a query touches, so someone with no grant shouldn't be able to
    learn a table's shape by asking for its plan.
    """
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == query.server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")

    driver = get_driver(server.dialect)

    allowed, payload = await check_query_permissions(
        db, user, query.server_id, query.database, query.sql,
        driver.default_schema_for(server.database),
        write_policy=server.write_policy or "read_write",
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=payload)

    if not driver.supports("execution_plan"):
        return {"supported": False, "nodes": [], "warnings": [], "missing_indexes": []}

    stmts = driver.explain_statements(query.sql)
    if stmts is None:
        return {"supported": False, "nodes": [], "warnings": [], "missing_indexes": []}

    preamble, statement, epilogue = stmts
    conn_str = await get_connection_string(db, query.server_id, query.database)

    # The SET statements have to be their own batches, and the epilogue must run
    # even when the statement itself fails — otherwise the pooled connection
    # goes back with SHOWPLAN still on and every later query on it returns a
    # plan instead of results.
    await execute_query_async(conn_str, preamble)
    try:
        result = await execute_query_async(conn_str, statement)
    finally:
        await execute_query_async(conn_str, epilogue)

    if result["error"]:
        return {
            "supported": True,
            "error": result["error"],
            "nodes": [],
            "warnings": [],
            "missing_indexes": [],
        }

    raw = result["rows"][0][0] if result["rows"] and result["rows"][0] else ""
    parsed = driver.parse_plan(raw)
    if not parsed:
        return {
            "supported": True,
            "error": "The server returned a plan that could not be read.",
            "nodes": [],
            "warnings": [],
            "missing_indexes": [],
        }
    return {"supported": True, **parsed}


@router.post("/cancel")
async def cancel_sql(
    req: CancelRequest,
    user: dict = Depends(require_auth),
):
    """Abort an in-flight query by its client-supplied query_id (the Stop
    button). Returns {cancelled: bool} — False if the query already finished or
    the id is unknown. cancel_query does a small network round-trip, so it runs
    off the event loop for consistency with execute."""
    cancelled = await asyncio.to_thread(cancel_query, req.query_id)
    return {"cancelled": cancelled}
