from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request
from app.database import get_db
from app.models import (
    ServerConnection,
    ServerConnectionCreate,
    ServerConnectionUpdate,
    ServerConnectionResponse,
)
from app.auth import require_auth
from app.services.connection import build_connection_string, get_sql_connection

router = APIRouter(prefix="/api/servers", tags=["servers"])


@router.get("/", response_model=list[ServerConnectionResponse])
async def list_servers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    result = await db.execute(
        select(ServerConnection).order_by(ServerConnection.name)
    )
    return result.scalars().all()


@router.post("/", response_model=ServerConnectionResponse)
async def create_server(
    server: ServerConnectionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    db_server = ServerConnection(**server.model_dump(), from_config=False)
    db.add(db_server)
    await db.commit()
    await db.refresh(db_server)
    return db_server


@router.get("/{server_id}", response_model=ServerConnectionResponse)
async def get_server(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    result = await db.execute(
        select(ServerConnection).where(ServerConnection.id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.put("/{server_id}", response_model=ServerConnectionResponse)
async def update_server(
    server_id: int,
    update: ServerConnectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    result = await db.execute(
        select(ServerConnection).where(ServerConnection.id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(server, key, value)

    await db.commit()
    await db.refresh(server)
    return server


@router.delete("/{server_id}")
async def delete_server(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    result = await db.execute(
        select(ServerConnection).where(ServerConnection.id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    await db.delete(server)
    await db.commit()
    return {"message": "Server deleted"}


@router.post("/{server_id}/test")
async def test_connection(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    result = await db.execute(
        select(ServerConnection).where(ServerConnection.id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    try:
        conn_str = build_connection_string(
            server.host, server.port, server.username, server.password
        )
        with get_sql_connection(conn_str) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"success": True, "message": "Connection successful"}
    except Exception as e:
        return {"success": False, "message": str(e)}
