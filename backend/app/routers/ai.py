import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.auth import block_guests
from app.models import (
    AIGenerateRequest,
    AIFixRequest,
    AIFindDataRequest,
    AIResponse,
    ServerConnection,
)
from app.services.connection import get_connection_string, build_connection_string
from app.services import ai as ai_service
from app.services import lineage
from app.services.permissions import can_access_server

router = APIRouter(prefix="/api/ai", tags=["ai"])


async def _lineage_conn(db: AsyncSession, server: ServerConnection):
    """Connection to the server holding RMTools.dbo.LineageSnapshot.

    The lineage knowledge base lives on the main SQL Server, but the question
    may be asked from any connection — Aurora included, and the graph covers
    Aurora gold too. So the snapshot is always read from the `main` server
    rather than from whichever one the user happens to be on.

    Returns None when there is no such server, which makes the AI service fall
    back to name-similarity ranking rather than fail. This deliberately does not
    consult `can_access_server`: the caller has already been authorised for the
    server they are querying, and the snapshot contributes table *metadata* to a
    prompt, never rows. A user who cannot see the main server still must not be
    shown its data, and this path never reads any.
    """
    if server.kind == "main" and server.dialect == "mssql":
        source = server
    else:
        source = (
            await db.execute(
                select(ServerConnection)
                .where(ServerConnection.kind == "main")
                .where(ServerConnection.dialect == "mssql")
                .order_by(ServerConnection.id)
            )
        ).scalars().first()
    if source is None:
        return None
    return build_connection_string(
        source.host, source.port, source.username, source.password, "master"
    )


@router.post("/generate", response_model=AIResponse)
async def generate(
    body: AIGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(block_guests),
):
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == body.server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        result = await asyncio.to_thread(
            ai_service.generate_sql,
            server.host,
            server.port,
            server.username,
            server.password,
            body.database,
            body.prompt,
            body.current_sql,
            server.dialect,
            server.database,
            await _lineage_conn(db, server),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI request failed: {e}")
    return AIResponse(**result)


@router.post("/find-data", response_model=AIResponse)
async def find_data(
    body: AIFindDataRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(block_guests),
):
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == body.server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        result = await asyncio.to_thread(
            ai_service.find_data,
            server.host, server.port, server.username, server.password, body.prompt,
            server.dialect, server.database,
            25, 200,
            await _lineage_conn(db, server),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI request failed: {e}")
    return AIResponse(**result)


@router.get("/suggestions")
async def suggestions(
    server_id: int,
    database: str = "",
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(block_guests),
):
    """Example questions for the AI chat, grounded in the connected database.

    Never fails the caller: any problem resolving the lineage snapshot returns
    the generic catalog questions instead, because a chat screen with no
    starting point is a worse outcome than a slightly less specific one.
    """
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")

    target = database or server.database or ""
    try:
        conn = await _lineage_conn(db, server)
        snapshot = await asyncio.to_thread(lineage.load_snapshot, conn) if conn else None
        return {"suggestions": lineage.build_suggestions(snapshot, target)}
    except Exception:
        return {"suggestions": list(lineage.GENERIC_SUGGESTIONS)}


@router.post("/fix", response_model=AIResponse)
async def fix(
    body: AIFixRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(block_guests),
):
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == body.server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")
    conn_str = await get_connection_string(db, body.server_id, body.database)
    try:
        result = await asyncio.to_thread(
            ai_service.fix_sql, conn_str, body.database, body.sql, body.error
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI request failed: {e}")
    return AIResponse(**result)
