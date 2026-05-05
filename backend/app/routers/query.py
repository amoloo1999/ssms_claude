from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
from app.models import QueryRequest, QueryResult, ServerConnection
from app.services.connection import get_connection_string, execute_query
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
        db, user, query.server_id, query.database, query.sql
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=payload)

    conn_str = await get_connection_string(db, query.server_id, query.database)
    result = execute_query(conn_str, query.sql)
    return QueryResult(**result)
