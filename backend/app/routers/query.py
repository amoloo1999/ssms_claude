from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
import asyncio
from app.models import QueryRequest, QueryResult, CancelRequest, ServerConnection
from app.services.connection import get_connection_string, execute_query_async, cancel_query
from app.services.drivers import get_driver
from app.services.permissions import can_access_server, check_query_permissions

router = APIRouter(prefix="/api/query", tags=["query"])


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
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=payload)

    conn_str = await get_connection_string(db, query.server_id, query.database)
    result = await execute_query_async(conn_str, query.sql, query_id=query.query_id)
    return QueryResult(**result)


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
