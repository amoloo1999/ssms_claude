"""Tests for sandbox write access (SANDBOX_USERS).

A sandbox user may create tables in one schema and change data there. The app
does not enforce "only your own tables" — SQL Server does, because those writes
run under a login that can write nowhere else. So the property that matters
most here is not the permission check on its own. It is that an ALLOWED
sandbox write always goes out under the sandbox login and never under the
server's shared one. The router tests assert exactly that, by capturing the
connection string each endpoint actually executes with.

Needs the web deps; skipped cleanly when they aren't installed.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app import config  # noqa: E402
from app.auth import require_auth  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.models import Schedule, ServerConnection, TablePermission  # noqa: E402
from app.routers import export as export_router  # noqa: E402
from app.routers import query as query_router  # noqa: E402
from app.routers import schedules as schedules_router  # noqa: E402
from app.services import permissions  # noqa: E402

MASON = {"email": "mfriday@williamwarren.com"}
VIEWER = {"email": "someone.else@williamwarren.com"}
REVMAN = {"email": "chillyer@williamwarren.com"}

SANDBOX = config.SANDBOX_USERS["mfriday@williamwarren.com"]
SHARED_LOGIN = "williamwarren"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def db():
    """An in-memory app DB with MSSQL01, a second SQL Server, and a read-only one."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as s:
            common = dict(username=SHARED_LOGIN, password="shared-pw", from_config=True)
            s.add_all([
                ServerConnection(id=1, name="main-server", host=SANDBOX.host, port=SANDBOX.port, **common),
                ServerConnection(id=2, name="elsewhere", host="10.0.0.9", port=1433, **common),
                ServerConnection(
                    id=3, name="ro", host=SANDBOX.host, port=SANDBOX.port + 1,
                    write_policy="read_only", **common,
                ),
            ])
            await s.commit()

    _run(_setup())
    return session_maker


@pytest.fixture(autouse=True)
def _reset_password_cache(monkeypatch):
    # get_sandbox_passwords is lru_cached; clear it around every test so one
    # test's resolved passwords never leak into the next. Blank the SSM param
    # name by default so no test touches AWS (the Parameter Store tests set it
    # back explicitly).
    monkeypatch.setattr(config.get_settings(), "sandbox_passwords_param", "")
    config.get_sandbox_passwords.cache_clear()
    yield
    config.get_sandbox_passwords.cache_clear()


@pytest.fixture()
def password(monkeypatch):
    monkeypatch.setitem(config.get_settings().sandbox_passwords, SANDBOX.login, "sandbox-pw")


def _grant(session_maker, user, table, schema="dbo", database="Sites", server_id=1):
    async def _add():
        async with session_maker() as s:
            s.add(TablePermission(
                user_email=user["email"], server_id=server_id, database=database,
                schema_name=schema, table_name=table, granted_by="test",
            ))
            await s.commit()

    _run(_add())


def _authorize(session_maker, user, sql, server_id=1, database="Sites", surface="desktop"):
    async def _go():
        async with session_maker() as s:
            server = await s.get(ServerConnection, server_id)
            return await permissions.authorize_query(s, user, server, database, sql, surface)

    return _run(_go())


def _uid(handle) -> str:
    return re.search(r"UID=([^;]*);", handle.conn_str).group(1)


# ── config ──────────────────────────────────────────────────────────────────


def test_sandbox_lookup():
    assert config.sandbox_for("mfriday@williamwarren.com") == SANDBOX
    assert config.sandbox_for("MFriday@WilliamWarren.com") == SANDBOX
    assert config.sandbox_for(VIEWER["email"]) is None
    assert config.sandbox_for(None) is None


def test_revman_never_gets_a_sandbox(monkeypatch):
    # Routing a RevMan through a restricted login would only take access away.
    monkeypatch.setitem(config.SANDBOX_USERS, REVMAN["email"], SANDBOX)
    assert config.sandbox_for(REVMAN["email"]) is None


def test_passwords_parse_from_env_json(monkeypatch):
    monkeypatch.setenv("SANDBOX_PASSWORDS", '{"ssms_mfriday": "abc123"}')
    assert config.Settings(_env_file=None).sandbox_passwords == {"ssms_mfriday": "abc123"}


def _fake_boto3(monkeypatch, ssm):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: ssm))


def test_password_prefers_parameter_store(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "sandbox_passwords_param", "/sql-studio/sandbox_passwords")
    monkeypatch.setitem(settings.sandbox_passwords, SANDBOX.login, "env-value")  # must be ignored

    class SSM:
        def get_parameter(self, Name, WithDecryption):
            assert Name == "/sql-studio/sandbox_passwords" and WithDecryption is True
            return {"Parameter": {"Value": '{"ssms_mfriday": "from-ssm"}'}}

    _fake_boto3(monkeypatch, SSM())
    config.get_sandbox_passwords.cache_clear()
    assert config.sandbox_password(SANDBOX.login) == "from-ssm"


def test_parameter_store_failure_falls_back_to_env(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "sandbox_passwords_param", "/sql-studio/sandbox_passwords")
    monkeypatch.setitem(settings.sandbox_passwords, SANDBOX.login, "env-value")

    class SSM:
        def get_parameter(self, **k):
            raise RuntimeError("parameter not found / no permission")

    _fake_boto3(monkeypatch, SSM())
    config.get_sandbox_passwords.cache_clear()
    # A Parameter Store failure must never take the app down; it uses .env.
    assert config.sandbox_password(SANDBOX.login) == "env-value"


def test_no_source_fails_closed(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "sandbox_passwords_param", "")
    config.get_sandbox_passwords.cache_clear()
    assert config.sandbox_password(SANDBOX.login) == ""


def test_covers_pins_instance_and_database():
    class S:
        def __init__(self, host=SANDBOX.host, port=SANDBOX.port, dialect="mssql"):
            self.host, self.port, self.dialect = host, port, dialect

    assert SANDBOX.covers(S(), "Sites")
    assert SANDBOX.covers(S(), "sites")
    assert not SANDBOX.covers(S(), "Stortrack")
    assert not SANDBOX.covers(S(host="10.0.0.9"), "Sites")
    assert not SANDBOX.covers(S(port=1434), "Sites")
    assert not SANDBOX.covers(S(dialect="postgres"), "Sites")


# ── authorize_query ─────────────────────────────────────────────────────────


def test_write_in_sandbox_runs_as_sandbox_login(db, password):
    allowed, _, conn = _authorize(db, MASON, "CREATE TABLE sandbox_mfriday.t (id int)")
    assert allowed is True
    assert _uid(conn) == SANDBOX.login
    assert "PWD=sandbox-pw;" in conn.conn_str
    assert "DATABASE=Sites;" in conn.conn_str


def test_reads_keep_the_shared_login(db, password):
    # Reads are unchanged for a sandbox user, including reads of their own schema.
    allowed, _, conn = _authorize(db, MASON, "SELECT * FROM sandbox_mfriday.t")
    assert allowed is True
    assert _uid(conn) == SHARED_LOGIN


def test_own_schema_is_readable_without_a_grant(db, password):
    allowed, payload, _ = _authorize(db, MASON, "SELECT * FROM sandbox_mfriday.anything")
    assert allowed is True, payload


def test_implicit_grant_is_scoped_to_the_sandbox(db, password):
    # The implicit grant covers the sandbox schema on the sandbox server — not
    # dbo, not the same schema on another server.
    allowed, payload, _ = _authorize(db, MASON, "SELECT * FROM dbo.Units")
    assert allowed is False
    assert payload["missing_tables"]
    allowed, _, _ = _authorize(db, MASON, "SELECT * FROM sandbox_mfriday.t", server_id=2)
    assert allowed is False


def test_missing_password_refuses_rather_than_falling_back(db):
    allowed, payload, conn = _authorize(db, MASON, "INSERT INTO sandbox_mfriday.t VALUES (1)")
    assert allowed is False
    assert conn is None
    assert "isn't set up" in payload["detail"]


def test_writes_outside_the_sandbox_database_are_refused(db, password):
    allowed, payload, conn = _authorize(
        db, MASON, "CREATE TABLE sandbox_mfriday.t (id int)", database="Stortrack"
    )
    assert allowed is False and conn is None
    # Outside the sandbox DB he is a plain view-only user, so a CREATE is refused
    # by the SELECT-only rule.
    assert "SELECT" in payload["detail"]


def test_writes_on_another_instance_are_refused(db, password):
    allowed, _, conn = _authorize(db, MASON, "CREATE TABLE sandbox_mfriday.t (id int)", server_id=2)
    assert allowed is False and conn is None


def test_phone_is_still_read_only(db, password):
    allowed, payload, _ = _authorize(
        db, MASON, "INSERT INTO sandbox_mfriday.t VALUES (1)", surface="mobile"
    )
    assert allowed is False
    assert "phone or tablet" in payload["detail"]


def test_read_only_connection_still_refuses(db, password, monkeypatch):
    # Point the sandbox at the read-only server: the connection gate wins.
    ro = config.Sandbox(SANDBOX.host, SANDBOX.port + 1, "Sites", "sandbox_mfriday", SANDBOX.login)
    monkeypatch.setitem(config.SANDBOX_USERS, MASON["email"], ro)
    allowed, payload, _ = _authorize(db, MASON, "INSERT INTO sandbox_mfriday.t VALUES (1)", server_id=3)
    assert allowed is False
    assert "read-only" in payload["detail"]


def test_writes_that_read_prod_still_need_the_read_grant(db, password):
    sql = "INSERT INTO sandbox_mfriday.t SELECT * FROM dbo.Units"
    allowed, payload, _ = _authorize(db, MASON, sql)
    assert allowed is False
    assert payload["missing_tables"][0]["table"] == "Units"

    _grant(db, MASON, "Units")
    allowed, _, conn = _authorize(db, MASON, sql)
    assert allowed is True
    assert _uid(conn) == SANDBOX.login


def test_a_write_aimed_at_dbo_still_runs_as_the_sandbox_login(db, password):
    # The app lets this through on purpose (Mason can read dbo.Units) — SQL
    # Server refuses it, because the sandbox login has no DELETE on dbo. What
    # must never happen is this statement going out under the shared login.
    _grant(db, MASON, "Units")
    allowed, _, conn = _authorize(db, MASON, "DELETE FROM dbo.Units")
    assert allowed is True
    assert _uid(conn) == SANDBOX.login


def test_other_users_are_unchanged(db, password):
    allowed, _, conn = _authorize(db, VIEWER, "CREATE TABLE sandbox_mfriday.t (id int)")
    assert allowed is False and conn is None

    allowed, _, conn = _authorize(db, REVMAN, "CREATE TABLE dbo.t (id int)")
    assert allowed is True
    assert _uid(conn) == SHARED_LOGIN


# ── every endpoint that runs user SQL uses the sandbox login ────────────────


@pytest.fixture()
def client(db, password, monkeypatch):
    executed: list = []

    async def fake_execute(conn, sql, params=None, query_id=None):
        executed.append((conn, sql))
        return {
            "columns": ["x"], "rows": [[1]], "row_count": 1, "result_sets": [],
            "execution_time_ms": 1.0, "error": None,
        }

    for module in (query_router, export_router, schedules_router):
        monkeypatch.setattr(module, "execute_query_async", fake_execute)
    monkeypatch.setattr(schedules_router, "get_scheduler_token", lambda: "tok")

    app = FastAPI()
    for module in (query_router, export_router, schedules_router):
        app.include_router(module.router)

    async def _get_db():
        async with db() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[require_auth] = lambda: MASON

    with TestClient(app) as c:
        c.executed = executed  # type: ignore[attr-defined]
        yield c


WRITE = "CREATE TABLE sandbox_mfriday.t (id int)"


def test_query_execute_uses_sandbox_login(client):
    r = client.post("/api/query/execute", json={"server_id": 1, "database": "Sites", "sql": WRITE})
    assert r.status_code == 200, r.text
    assert _uid(client.executed[-1][0]) == SANDBOX.login


def test_query_plan_uses_sandbox_login(client):
    r = client.post("/api/query/plan", json={"server_id": 1, "database": "Sites", "sql": WRITE})
    assert r.status_code == 200, r.text
    assert _uid(client.executed[-1][0]) == SANDBOX.login


def test_export_uses_sandbox_login(client):
    r = client.post(
        "/api/export/download",
        json={"server_id": 1, "database": "Sites", "sql": WRITE, "format": "csv"},
    )
    assert r.status_code == 200, r.text
    assert _uid(client.executed[-1][0]) == SANDBOX.login


def test_schedule_runner_uses_sandbox_login(client, db):
    async def _add():
        async with db() as s:
            s.add(Schedule(id=7, owner_email=MASON["email"], name="n", server_id=1, database="Sites", sql=WRITE))
            await s.commit()

    _run(_add())
    r = client.post("/api/schedules/runner/7/execute", headers={"x-scheduler-token": "tok"})
    assert r.status_code == 200, r.text
    assert r.json()["ran"] is True
    assert _uid(client.executed[-1][0]) == SANDBOX.login


def test_seed_grants_all_of_sites_read_to_a_new_sandbox_user(db):
    from app import main

    async def _go():
        # main.async_session is bound to the real settings DB; point it at ours.
        orig = main.async_session
        main.async_session = db
        try:
            await main.seed_sandbox_read_grants()
            async with db() as s:
                rows = (await s.execute(
                    select(TablePermission).where(TablePermission.user_email == MASON["email"])
                )).scalars().all()
            return rows
        finally:
            main.async_session = orig

    rows = _run(_go())
    # One db-wide read grant on Sites, on the sandbox instance (id=1), not the
    # other server (id=2) or the read-only one (id=3).
    assert [(r.server_id, r.database, r.schema_name, r.table_name) for r in rows] == [
        (1, "Sites", "*", "*")
    ]


def test_seed_leaves_an_existing_user_alone(db):
    from app import main

    _grant(db, MASON, "Units")  # an approver already narrowed him to one table

    async def _go():
        orig = main.async_session
        main.async_session = db
        try:
            await main.seed_sandbox_read_grants()
            async with db() as s:
                return (await s.execute(
                    select(TablePermission).where(TablePermission.user_email == MASON["email"])
                )).scalars().all()
        finally:
            main.async_session = orig

    rows = _run(_go())
    # Untouched: still just the single dbo.Units grant, no db-wide row added.
    assert [(r.database, r.schema_name, r.table_name) for r in rows] == [("Sites", "dbo", "Units")]


def test_no_router_checks_sql_without_choosing_its_connection():
    # The invariant behind all of the above: routers never call
    # check_query_permissions directly, because only authorize_query ties the
    # permission decision to the credentials the statement runs under.
    routers = Path(__file__).resolve().parent.parent / "app" / "routers"
    offenders = [p.name for p in routers.glob("*.py") if "check_query_permissions" in p.read_text(encoding="utf-8")]
    assert offenders == []
