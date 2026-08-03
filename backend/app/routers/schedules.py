"""Scheduled queries: CRUD for the UI, plus the endpoint Airflow drives.

Execution deliberately lives in Airflow rather than in this process. The app is
a single uvicorn service that gets restarted by every deploy, so an in-process
scheduler would silently drop any run due during one. Airflow already runs on
the EC2 box, already has SMTP configured and already alerts on failure.

The split: definitions here (the UI edits them), execution there (a DAG asks
this app what is due and tells it to run each one).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import get_settings, is_revman
from app.database import get_db
from app.models import (
    Schedule,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleRun,
    ScheduleRunResponse,
    ScheduleUpdate,
    ServerConnection,
)
from app.services.audit import record
from app.services.connection import get_connection_string, execute_query_async
from app.services.drivers import get_driver
from app.services.permissions import can_access_server, check_query_permissions
from app.services.schedules import evaluate_condition, is_valid_condition

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


# ── CRUD ────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Your own schedules. RevMan sees every schedule, because a schedule that
    hammers production is an operational concern, not a private one."""
    stmt = select(Schedule)
    if not is_revman(user.get("email", "")):
        stmt = stmt.where(Schedule.owner_email == user["email"])
    return (await db.execute(stmt.order_by(Schedule.name))).scalars().all()


@router.post("", response_model=ScheduleResponse)
async def create_schedule(
    payload: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    if not is_valid_condition(payload.alert_condition):
        raise HTTPException(
            status_code=400,
            detail="Alert condition must look like 'rowcount > 0' or 'duration_ms > 30000'.",
        )
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == payload.server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")

    schedule = Schedule(**payload.model_dump(), owner_email=user["email"])
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def _own(db: AsyncSession, schedule_id: int, user: dict) -> Schedule:
    schedule = (
        await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    ).scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    # A RevMan may pause someone else's runaway schedule, but only the owner may
    # change what it runs — the SQL executes as the owner, so someone else
    # editing it would be running code under a colleague's permissions.
    if schedule.owner_email != user["email"] and not is_revman(user.get("email", "")):
        raise HTTPException(status_code=403, detail="Not your schedule")
    return schedule


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    schedule = await _own(db, schedule_id, user)
    fields = payload.model_dump(exclude_unset=True)

    if schedule.owner_email != user["email"]:
        # Non-owner (a RevMan) may only change the run state.
        disallowed = set(fields) - {"state"}
        if disallowed:
            raise HTTPException(
                status_code=403,
                detail="Only the owner can change a schedule's query — you can pause or resume it.",
            )

    if "alert_condition" in fields and not is_valid_condition(fields["alert_condition"]):
        raise HTTPException(status_code=400, detail="Alert condition not understood.")

    for key, value in fields.items():
        setattr(schedule, key, value)
    if fields.get("state") == "active":
        schedule.paused_reason = ""
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    schedule = await _own(db, schedule_id, user)
    if schedule.owner_email != user["email"]:
        raise HTTPException(status_code=403, detail="Only the owner can delete a schedule")
    await db.delete(schedule)
    await db.commit()
    return {"deleted": True}


@router.get("/{schedule_id}/runs", response_model=list[ScheduleRunResponse])
async def list_runs(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    await _own(db, schedule_id, user)
    stmt = (
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule_id)
        .order_by(ScheduleRun.started_at.desc())
        .limit(20)
    )
    return (await db.execute(stmt)).scalars().all()


# ── The runner surface Airflow drives ───────────────────────────────────────


def _require_runner(token: str | None) -> None:
    """Shared-secret auth for the DAG.

    This endpoint runs SQL as a schedule's owner without a browser session, so
    it cannot use the Google SSO dependency. It is gated by a secret the DAG
    holds. Refusing when the secret is unset is deliberate — an unset secret
    must fail closed, not open.
    """
    expected = (get_settings().scheduler_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Scheduler is not configured.")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Bad scheduler token")


@router.get("/runner/due", response_model=list[ScheduleResponse])
async def due_schedules(
    x_scheduler_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Every active schedule. The DAG owns cadence matching — it already has a
    cron implementation and a clock, and duplicating that here would give us two
    that can disagree."""
    _require_runner(x_scheduler_token)
    stmt = select(Schedule).where(Schedule.state == "active")
    return (await db.execute(stmt)).scalars().all()


@router.post("/runner/{schedule_id}/execute")
async def execute_schedule(
    schedule_id: int,
    x_scheduler_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Run one schedule as its owner and report what happened.

    The owner's permissions are re-checked on every run. If their access has
    been revoked since the schedule was created, it PAUSES rather than failing
    silently or continuing to read something they may no longer see.
    """
    _require_runner(x_scheduler_token)

    schedule = (
        await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    ).scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.state != "active":
        return {"ran": False, "reason": "paused"}

    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == schedule.server_id))
    ).scalar_one_or_none()
    if not server:
        schedule.state = "paused"
        schedule.paused_reason = "Its server no longer exists."
        await db.commit()
        return {"ran": False, "reason": "server_missing", "paused": True}

    owner = {"email": schedule.owner_email}
    driver = get_driver(server.dialect)
    allowed, payload = await check_query_permissions(
        db, owner, schedule.server_id, schedule.database, schedule.sql,
        driver.default_schema_for(server.database),
        write_policy=server.write_policy or "read_write",
    )
    if not allowed:
        # Pause, don't fail silently — the handoff's rule, and the difference
        # between a schedule someone notices and one that quietly stops working.
        schedule.state = "paused"
        schedule.paused_reason = (
            f"{schedule.owner_email} no longer has access: {payload.get('detail', 'permission denied')}"
        )
        db.add(
            ScheduleRun(schedule_id=schedule.id, status="paused", error=schedule.paused_reason)
        )
        await db.commit()
        return {"ran": False, "reason": "access_revoked", "paused": True}

    conn_str = await get_connection_string(db, schedule.server_id, schedule.database)
    result = await execute_query_async(conn_str, schedule.sql)

    row_count = result.get("row_count") or 0
    duration = result.get("execution_time_ms") or 0
    failed = bool(result.get("error"))
    alerted = (
        False
        if failed
        else evaluate_condition(
            schedule.alert_condition, row_count=row_count, duration_ms=duration
        )
    )

    db.add(
        ScheduleRun(
            schedule_id=schedule.id,
            status="error" if failed else "ok",
            row_count=row_count,
            duration_ms=duration,
            alerted=alerted,
            error=result.get("error"),
        )
    )
    schedule.last_run_at = datetime.utcnow()
    schedule.last_result = result.get("error") or f"{row_count} rows"
    await db.commit()

    await record(
        db,
        actor=schedule.owner_email,
        actor_kind="schedule",
        event_type="write" if failed is False and not _is_read(schedule.sql) else "schedule",
        server_id=server.id,
        server_name=server.name,
        database=schedule.database,
        detail=f"{schedule.name}: {(schedule.sql or '')[:500]}",
        result="error" if failed else "ok",
    )

    return {
        "ran": True,
        "schedule": schedule.name,
        "row_count": row_count,
        "duration_ms": duration,
        "error": result.get("error"),
        "alert": alerted,
        "notify": [e.strip() for e in (schedule.notify_emails or "").split(",") if e.strip()],
        "attach_csv": schedule.attach_csv,
        "columns": result.get("columns") or [],
        "rows": (result.get("rows") or [])[:1000] if alerted else [],
    }


def _is_read(sql: str) -> bool:
    from app.services.permissions import is_select_only

    ok, _ = is_select_only(sql or "")
    return ok
