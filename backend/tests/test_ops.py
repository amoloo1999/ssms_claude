"""Tests for the operational surfaces: session monitor, kill, audit log.

These are the hardest-gated endpoints in the app and the gating is the point:

- the session monitor exposes other users' in-flight SQL, which on PROD-MAIN can
  contain customer data in literals, so a non-RevMan must be refused;
- killing a session rolls back somebody else's transaction, so it is
  approver-only, needs a real reason, and must not happen unlogged;
- the audit log is approver-only and has no delete path at all.

Needs the web deps; skipped cleanly when they aren't installed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.auth import require_auth, require_approver  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers.ops import router as ops_router  # noqa: E402
from app.services.drivers import get_driver  # noqa: E402

APPROVER = {"email": "amoloo@williamwarren.com"}
REVMAN = {"email": "chillyer@williamwarren.com"}   # RevMan, not an approver
VIEWER = {"email": "marketing@williamwarren.com"}  # neither


@pytest.fixture()
def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(ops_router)

    async def _get_db():
        async with session_maker() as session:
            yield session

    current = {"user": APPROVER}

    def _auth():
        return current["user"]

    def _approver():
        # Mirror the real dependency: approver-gated routes must reject a
        # non-approver before any handler code runs.
        from fastapi import HTTPException

        u = current["user"]
        if u["email"] not in {"amoloo@williamwarren.com", "cpj@williamwarren.com", "wfan@williamwarren.com"}:
            raise HTTPException(status_code=403, detail="Approver role required")
        return u

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[require_auth] = _auth
    app.dependency_overrides[require_approver] = _approver

    with TestClient(app) as c:
        import asyncio

        async def _create():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_create())
        c.as_user = lambda u: current.__setitem__("user", u)  # type: ignore[attr-defined]
        c.session_maker = session_maker  # type: ignore[attr-defined]
        yield c


def _audit(client, **kw):
    import asyncio

    from app.models import AuditEvent

    async def _insert():
        async with client.session_maker() as session:
            session.add(AuditEvent(**kw))
            await session.commit()

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_insert())


# ── Session monitor gating ──────────────────────────────────────────────────


def test_session_monitor_refuses_non_revman(client):
    # The failure that matters: a marketing/accounting login — or an external
    # Uniti contractor — reading everyone else's in-flight SQL.
    client.as_user(VIEWER)
    r = client.get("/api/ops/servers/1/sessions")
    assert r.status_code == 403
    assert "Revenue Management" in r.json()["detail"]


def test_session_monitor_reaches_server_lookup_for_revman(client):
    # A RevMan gets past the role gate; the 404 here is the server lookup, not
    # the permission check — which is what we're asserting.
    client.as_user(REVMAN)
    r = client.get("/api/ops/servers/999/sessions")
    assert r.status_code == 404


# ── Kill gating ─────────────────────────────────────────────────────────────


def test_kill_refuses_non_approver(client):
    client.as_user(REVMAN)  # RevMan but not an approver
    r = client.post(
        "/api/ops/kill",
        json={"server_id": 1, "session_id": 104, "reason": "blocking the nightly load"},
    )
    assert r.status_code == 403


def test_kill_requires_a_substantive_reason(client):
    client.as_user(APPROVER)
    for reason in ("", "   ", "asdf"):
        r = client.post(
            "/api/ops/kill", json={"server_id": 1, "session_id": 104, "reason": reason}
        )
        assert r.status_code == 400, reason
        assert "reason" in r.json()["detail"].lower()


def test_kill_of_unknown_server_is_404_not_a_silent_success(client):
    client.as_user(APPROVER)
    r = client.post(
        "/api/ops/kill",
        json={"server_id": 999, "session_id": 104, "reason": "blocking the nightly load"},
    )
    assert r.status_code == 404


# ── Audit log ───────────────────────────────────────────────────────────────


def test_audit_is_approver_only(client):
    for u in (VIEWER, REVMAN):
        client.as_user(u)
        assert client.get("/api/ops/audit").status_code == 403, u["email"]

    client.as_user(APPROVER)
    assert client.get("/api/ops/audit").status_code == 200


def test_audit_returns_newest_first_and_filters(client):
    _audit(client, actor="a@x.com", event_type="write", detail="UPDATE dbo.Sites SET x=1")
    _audit(client, actor="b@x.com", event_type="export", detail="CSV · 1200 rows")
    _audit(client, actor="c@x.com", event_type="kill", detail="KILL 104", reason="blocked the load")

    client.as_user(APPROVER)
    rows = client.get("/api/ops/audit").json()
    assert len(rows) == 3

    kills = client.get("/api/ops/audit", params={"event_type": "kill"}).json()
    assert len(kills) == 1
    assert kills[0]["detail"] == "KILL 104"
    assert kills[0]["reason"] == "blocked the load"

    found = client.get("/api/ops/audit", params={"search": "dbo.Sites"}).json()
    assert len(found) == 1
    assert found[0]["actor"] == "a@x.com"


def test_audit_has_no_delete_route(client):
    # Append-only by construction. If a DELETE ever appears here, the log stops
    # being evidence of anything.
    paths = {r.path for r in client.app.routes if hasattr(r, "path")}
    audit_paths = {p for p in paths if "audit" in p}
    for route in client.app.routes:
        if getattr(route, "path", "") in audit_paths:
            assert "DELETE" not in getattr(route, "methods", set())


# ── Driver capability ───────────────────────────────────────────────────────


def test_kill_sql_coerces_the_session_id():
    # The id is interpolated into SQL; the int() coercion is what keeps that
    # from being an injection point.
    mssql = get_driver("mssql")
    assert mssql.kill_session_sql(104) == "KILL 104"
    assert mssql.kill_session_sql("104") == "KILL 104"
    with pytest.raises(ValueError):
        mssql.kill_session_sql("104; DROP TABLE x")


def test_other_engines_have_no_session_monitor():
    for dialect in ("postgres", "mysql", "snowflake"):
        d = get_driver(dialect)
        assert d.supports("session_monitor") is False, dialect
        assert d.sessions_sql() is None, dialect
        assert d.kill_session_sql(1) is None, dialect
