from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
from app.models import QueryRequest, QueryResult
from app.services.connection import get_connection_string, execute_query

router = APIRouter(prefix="/api/query", tags=["query"])


@router.post("/execute", response_model=QueryResult)
async def execute_sql(
    query: QueryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, query.server_id, query.database)
    result = execute_query(conn_str, query.sql)
    return QueryResult(**result)
