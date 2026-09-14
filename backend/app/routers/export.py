import io
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
from app.models import ExportRequest, ServerConnection
from app.services.connection import execute_query_async
from app.services.permissions import (
    authorize_query,
    can_access_server,
    client_surface,
)
from app.services.audit import record

router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("/download")
async def export_data(
    export_req: ExportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == export_req.server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")
    # This path executes the SQL it is handed, so it goes through the same gate
    # as /query/execute. Without it, a write could reach a read-only connection
    # through the export endpoint.
    allowed, payload, conn_str = await authorize_query(
        db, user, server, export_req.database, export_req.sql, client_surface(request)
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=payload)

    result = await execute_query_async(conn_str, export_req.sql)

    if result["error"]:
        raise HTTPException(status_code=400, detail=result["error"])

    df = pd.DataFrame(result["rows"], columns=result["columns"])

    # Exports leave the tenant, so every one is recorded with its row count —
    # this is the event you want when asking "who pulled that data out".
    await record(
        db,
        actor=user["email"],
        event_type="export",
        server_id=server.id,
        server_name=server.name,
        database=export_req.database,
        detail=f"{export_req.format.upper()} · {len(df)} rows · {(export_req.sql or '')[:1000]}",
        result="ok",
    )

    if export_req.format == "xlsx":
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, engine="openpyxl")
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=export.xlsx"},
        )
    else:
        buffer = io.StringIO()
        df.to_csv(buffer, index=False)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )
