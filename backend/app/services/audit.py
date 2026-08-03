"""Audit writing.

One helper so every call site records the same shape, and so a failure to write
an audit row can never take down the action it was recording — with one
deliberate exception, noted below.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent


async def record(
    db: AsyncSession,
    *,
    actor: str,
    event_type: str,
    detail: str = "",
    reason: str = "",
    result: str = "ok",
    server_id: int | None = None,
    server_name: str = "",
    database: str = "",
    actor_kind: str = "user",
    critical: bool = False,
) -> bool:
    """Append an audit event. Returns True when it was written.

    ``critical=False`` (the default) swallows failures: a broken audit write
    should not turn a successful export into a failed request.

    ``critical=True`` re-raises instead. Use it for actions where an unlogged
    occurrence is worse than the action not happening at all — killing a
    session is the case that matters, since the whole justification for
    allowing it is that it leaves a trace.
    """
    try:
        db.add(
            AuditEvent(
                actor=actor,
                actor_kind=actor_kind,
                event_type=event_type,
                server_id=server_id,
                server_name=server_name,
                database=database,
                detail=detail,
                reason=reason,
                result=result,
            )
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        if critical:
            raise
        return False
