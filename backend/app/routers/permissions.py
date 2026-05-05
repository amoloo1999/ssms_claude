"""Permission management API: list grants, request access, approve/deny, revoke."""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from starlette.requests import Request

from app.database import get_db
from app.auth import require_auth, require_approver
from app.models import (
    TablePermission,
    TablePermissionResponse,
    TablePermissionCreate,
    AccessRequest,
    AccessRequestCreate,
    AccessRequestResponse,
    AccessRequestDecision,
    ServerConnection,
)
from app.config import is_revman
from app.services.permissions import can_access_server

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


def _apply_scope(scope: str, database: str, schema_name: str, table_name: str) -> tuple[str, str, str]:
    """Normalize scope → DB/schema/table fields. 'server' = all wildcards;
    'database' = wildcard schema+table; 'table' = exact triple."""
    if scope == "server":
        return "*", "*", "*"
    if scope == "database":
        return database, "*", "*"
    return database, schema_name, table_name


@router.get("/me", response_model=list[TablePermissionResponse])
async def list_my_grants(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """The current user's table grants. RevMan returns empty (they don't need
    grants — access is implicit). Frontend uses `auth/me` `role` to decide
    whether to call this."""
    if is_revman(user.get("email", "")):
        return []
    result = await db.execute(
        select(TablePermission).where(TablePermission.user_email == user["email"])
    )
    return result.scalars().all()


@router.get("/my-requests", response_model=list[AccessRequestResponse])
async def list_my_requests(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    result = await db.execute(
        select(AccessRequest)
        .where(AccessRequest.user_email == user["email"])
        .order_by(AccessRequest.created_at.desc())
    )
    return result.scalars().all()


@router.post("/request", response_model=AccessRequestResponse)
async def create_request(
    body: AccessRequestCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Create a pending access request. Validates the server is one this user
    is even allowed to see, so non-RevMan can't request a `gp` table."""
    server_res = await db.execute(
        select(ServerConnection).where(ServerConnection.id == body.server_id)
    )
    server = server_res.scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")

    db_name, sch_name, tbl_name = _apply_scope(
        body.scope, body.database, body.schema_name, body.table_name
    )

    # Block dupes for the same pending target.
    dupe = await db.execute(
        select(AccessRequest).where(
            and_(
                AccessRequest.user_email == user["email"],
                AccessRequest.server_id == body.server_id,
                AccessRequest.database == db_name,
                AccessRequest.schema_name == sch_name,
                AccessRequest.table_name == tbl_name,
                AccessRequest.status == "pending",
            )
        )
    )
    existing = dupe.scalar_one_or_none()
    if existing:
        return existing

    req = AccessRequest(
        user_email=user["email"],
        server_id=body.server_id,
        database=db_name,
        schema_name=sch_name,
        table_name=tbl_name,
        reason=body.reason,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


# ── Approver-only endpoints ─────────────────────────────────────────────────


@router.get("/requests", response_model=list[AccessRequestResponse])
async def list_requests(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    stmt = select(AccessRequest).order_by(AccessRequest.created_at.desc())
    if status:
        stmt = stmt.where(AccessRequest.status == status)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/requests/{req_id}/approve", response_model=AccessRequestResponse)
async def approve_request(
    req_id: int,
    body: AccessRequestDecision,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    req = (await db.execute(select(AccessRequest).where(AccessRequest.id == req_id))).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")

    # Idempotent grant — skip if it already exists.
    existing = await db.execute(
        select(TablePermission).where(
            and_(
                TablePermission.user_email == req.user_email,
                TablePermission.server_id == req.server_id,
                TablePermission.database == req.database,
                TablePermission.schema_name == req.schema_name,
                TablePermission.table_name == req.table_name,
            )
        )
    )
    if not existing.scalar_one_or_none():
        db.add(
            TablePermission(
                user_email=req.user_email,
                server_id=req.server_id,
                database=req.database,
                schema_name=req.schema_name,
                table_name=req.table_name,
                granted_by=user["email"],
            )
        )

    req.status = "approved"
    req.decided_by = user["email"]
    req.decided_at = datetime.utcnow()
    req.decision_note = body.note
    await db.commit()
    await db.refresh(req)
    return req


@router.post("/requests/{req_id}/deny", response_model=AccessRequestResponse)
async def deny_request(
    req_id: int,
    body: AccessRequestDecision,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    req = (await db.execute(select(AccessRequest).where(AccessRequest.id == req_id))).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")
    req.status = "denied"
    req.decided_by = user["email"]
    req.decided_at = datetime.utcnow()
    req.decision_note = body.note
    await db.commit()
    await db.refresh(req)
    return req


@router.get("/grants", response_model=list[TablePermissionResponse])
async def list_all_grants(
    user_email: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    stmt = select(TablePermission).order_by(TablePermission.user_email, TablePermission.granted_at.desc())
    if user_email:
        stmt = stmt.where(TablePermission.user_email == user_email)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/grants", response_model=TablePermissionResponse)
async def create_grant(
    body: TablePermissionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    """Direct grant — used by Admin UI to give access without a prior request."""
    db_name, sch_name, tbl_name = _apply_scope(
        body.scope, body.database, body.schema_name, body.table_name
    )
    existing = await db.execute(
        select(TablePermission).where(
            and_(
                TablePermission.user_email == body.user_email,
                TablePermission.server_id == body.server_id,
                TablePermission.database == db_name,
                TablePermission.schema_name == sch_name,
                TablePermission.table_name == tbl_name,
            )
        )
    )
    found = existing.scalar_one_or_none()
    if found:
        return found

    grant = TablePermission(
        user_email=body.user_email,
        server_id=body.server_id,
        database=db_name,
        schema_name=sch_name,
        table_name=tbl_name,
        granted_by=user["email"],
    )
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return grant


@router.delete("/grants/{grant_id}")
async def revoke_grant(
    grant_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_approver),
):
    grant = (await db.execute(select(TablePermission).where(TablePermission.id == grant_id))).scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    await db.delete(grant)
    await db.commit()
    return {"message": "Grant revoked"}
