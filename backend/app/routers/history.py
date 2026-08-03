"""Query history and the snippet library.

History is strictly per-user. It is never shared, and there is deliberately no
"all users" view: the SQL text names schemas, tables and columns, so exposing
one person's history to another would leak the existence and shape of objects
they hold no grant for — the exact thing the permission system prevents in the
object tree.

Snippets are the opposite by design: a user's own, plus anything explicitly
shared. Sharing is an act, not a default.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models import (
    QueryHistory,
    QueryHistoryResponse,
    Snippet,
    SnippetCreate,
    SnippetResponse,
    SnippetUpdate,
)

router = APIRouter(prefix="/api", tags=["history"])


# ── History ─────────────────────────────────────────────────────────────────


@router.get("/history", response_model=list[QueryHistoryResponse])
async def list_history(
    search: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """The caller's own runs, newest first. Never anyone else's."""
    stmt = select(QueryHistory).where(QueryHistory.user_email == user["email"])
    if search:
        stmt = stmt.where(QueryHistory.sql.ilike(f"%{search}%"))
    stmt = stmt.order_by(QueryHistory.started_at.desc()).limit(limit)
    return (await db.execute(stmt)).scalars().all()


@router.delete("/history")
async def clear_history(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Clear the caller's own history. Scoped by user_email, so it cannot
    delete anyone else's rows even if the caller is an approver."""
    await db.execute(delete(QueryHistory).where(QueryHistory.user_email == user["email"]))
    await db.commit()
    return {"cleared": True}


# ── Snippets ────────────────────────────────────────────────────────────────


@router.get("/snippets", response_model=list[SnippetResponse])
async def list_snippets(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Your own snippets plus anything shared with the team."""
    stmt = (
        select(Snippet)
        .where((Snippet.owner_email == user["email"]) | (Snippet.is_shared == True))  # noqa: E712
        .order_by(Snippet.name)
    )
    return (await db.execute(stmt)).scalars().all()


@router.post("/snippets", response_model=SnippetResponse)
async def create_snippet(
    payload: SnippetCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    snippet = Snippet(**payload.model_dump(), owner_email=user["email"])
    db.add(snippet)
    await db.commit()
    await db.refresh(snippet)
    return snippet


async def _load_own_snippet(db: AsyncSession, snippet_id: int, email: str) -> Snippet:
    """Load a snippet the caller owns.

    A shared snippet is readable by everyone but editable only by its owner —
    otherwise one person could silently rewrite the SQL behind a snippet the
    rest of the team runs.
    """
    snippet = (
        await db.execute(select(Snippet).where(Snippet.id == snippet_id))
    ).scalar_one_or_none()
    if not snippet:
        raise HTTPException(status_code=404, detail="Snippet not found")
    if snippet.owner_email != email:
        raise HTTPException(status_code=403, detail="Only the owner can change this snippet")
    return snippet


@router.put("/snippets/{snippet_id}", response_model=SnippetResponse)
async def update_snippet(
    snippet_id: int,
    payload: SnippetUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    snippet = await _load_own_snippet(db, snippet_id, user["email"])
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(snippet, key, value)
    await db.commit()
    await db.refresh(snippet)
    return snippet


@router.delete("/snippets/{snippet_id}")
async def delete_snippet(
    snippet_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    snippet = await _load_own_snippet(db, snippet_id, user["email"])
    await db.delete(snippet)
    await db.commit()
    return {"deleted": True}


@router.post("/snippets/{snippet_id}/used", response_model=SnippetResponse)
async def mark_used(
    snippet_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Bump the use count when a snippet is opened into the editor.

    Any reader may bump a shared snippet's counter — that's the point of the
    number — so this deliberately does not go through _load_own_snippet.
    """
    snippet = (
        await db.execute(select(Snippet).where(Snippet.id == snippet_id))
    ).scalar_one_or_none()
    if not snippet:
        raise HTTPException(status_code=404, detail="Snippet not found")
    if snippet.owner_email != user["email"] and not snippet.is_shared:
        raise HTTPException(status_code=404, detail="Snippet not found")
    snippet.use_count = (snippet.use_count or 0) + 1
    await db.commit()
    await db.refresh(snippet)
    return snippet
