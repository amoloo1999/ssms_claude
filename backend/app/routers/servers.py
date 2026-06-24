from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from starlette.requests import Request
from app.database import get_db
from app.models import (
    ServerConnection,
    ServerConnectionCreate,
    ServerConnectionUpdate,
    ServerConnectionResponse,
)
import asyncio
from app.auth import require_auth, require_revman
from app.services.drivers import get_driver
from app.services.permissions import can_access_server

router = APIRouter(prefix="/api/servers", tags=["servers"])


@router.get("/", response_model=list[ServerConnectionResponse])
async def list_servers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    # Show shared servers (from_config) + user's own servers, then drop any
    # the user can't access (non-RevMan: gp servers are hidden entirely).
    result = await db.execute(
        select(ServerConnection)
        .where(
            or_(
                ServerConnection.from_config == True,
                ServerConnection.owner_email == user["email"],
            )
        )
        .order_by(ServerConnection.name)
    )
    return [s for s in result.scalars().all() if can_access_server(user, s)]


@router.post("/", response_model=ServerConnectionResponse)
async def create_server(
    server: ServerConnectionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_revman),
):
    # RevMan-only: a non-RevMan creating their own server (kind defaults to
    # 'main') would bypass the per-table permission system entirely, since
    # can_access_server returns True for any main-kind server.
    db_server = ServerConnection(
        **server.model_dump(),
        from_config=False,
        owner_email=user["email"],
    )
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
    # Allow access to shared servers or own servers, then enforce role gating.
    if not server.from_config and server.owner_email != user["email"]:
        raise HTTPException(status_code=403, detail="Access denied")
    if not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.put("/{server_id}", response_model=ServerConnectionResponse)
async def update_server(
    server_id: int,
    update: ServerConnectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_revman),
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
    user: dict = Depends(require_revman),
):
    result = await db.execute(
        select(ServerConnection).where(ServerConnection.id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.from_config:
        raise HTTPException(status_code=403, detail="Cannot delete shared servers")

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
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")

    driver = get_driver(server.dialect)
    conn_db = server.database if driver.single_database else None
    conn_str = driver.build_connection_string(
        server.host, server.port, server.username, server.password, conn_db
    )

    def _probe() -> None:
        # A standalone connection (not pooled) — the test must reflect the real
        # credentials/reachability, not a recycled handle. Runs off the event
        # loop so a slow/unreachable host doesn't stall other requests.
        conn = driver.connect(conn_str)
        try:
            driver.probe(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    try:
        await asyncio.to_thread(_probe)
        return {"success": True, "message": "Connection successful"}
    except Exception as e:
        return {"success": False, "message": str(e)}
