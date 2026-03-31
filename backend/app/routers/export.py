import io
import pandas as pd
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
from app.models import ExportRequest
from app.services.connection import get_connection_string, execute_query

router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("/download")
async def export_data(
    export_req: ExportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, export_req.server_id, export_req.database)
    result = execute_query(conn_str, export_req.sql)

    if result["error"]:
        return {"error": result["error"]}

    df = pd.DataFrame(result["rows"], columns=result["columns"])

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
