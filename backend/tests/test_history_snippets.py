"""End-to-end tests for query history and the snippet library.

The security-relevant claim is the scoping: history is per-user and must never
show one person another's SQL, because statement text names schemas, tables and
columns the reader may hold no grant for. Snippets are the deliberate opposite —
shared ones are readable by all — but only the owner may change one, so nobody
can quietly rewrite the SQL behind a query their colleagues run.

Needs the web deps (fastapi/httpx); skipped cleanly when they aren't installed,
so this file never breaks a bare `pytest` run.
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

from app.auth import require_auth  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers.history import router as history_router  # noqa: E402

ALICE = {"email": "alice@williamwarren.com", "name": "Alice"}
BOB = {"email": "bob@williamwarren.com", "name": "Bob"}


@pytest.fixture()
def client():
    """A test app over a fresh in-memory database.

    The session maker is module-level in app.database and bound to the real
    SQLite file, so get_db is overridden rather than reusing it.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(history_router)

    async def _get_db():
        async with session_maker() as session:
            yield session

    current = {"user": ALICE}

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[require_auth] = lambda: current["user"]

    with TestClient(app) as c:
        # create_all needs the same connection the sessions use; :memory: is
        # per-connection, so the engine is kept alive for the test's duration.
        import asyncio

        async def _create():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_create())
        c.as_user = lambda u: current.__setitem__("user", u)  # type: ignore[attr-defined]
        c.session_maker = session_maker  # type: ignore[attr-defined]
        yield c


def _add_history(client, email, sql, status="ok", error=None):
    """History rows are written by the query router rather than by an endpoint,
    so seed them through the same session maker the API reads from."""
    import asyncio

    from app.models import QueryHistory

    async def _insert():
        async with client.session_maker() as session:
            session.add(
                QueryHistory(
                    user_email=email,
                    server_id=1,
                    server_name="PROD-MAIN",
                    database="Sites",
                    sql=sql,
                    status=status,
                    row_count=3,
                    duration_ms=12.0,
                    error=error,
                )
            )
            await session.commit()

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_insert())


# ── History: per-user scoping ───────────────────────────────────────────────


def test_history_is_scoped_to_the_calling_user(client):
    # The security-critical case. SQL text names objects, so Bob seeing Alice's
    # history would leak the existence and shape of tables he has no grant for.
    _add_history(client, ALICE["email"], "SELECT * FROM dbo.SecretRates")
    _add_history(client, BOB["email"], "SELECT * FROM dbo.Public")

    client.as_user(ALICE)
    sqls = [h["sql"] for h in client.get("/api/history").json()]
    assert sqls == ["SELECT * FROM dbo.SecretRates"]

    client.as_user(BOB)
    sqls = [h["sql"] for h in client.get("/api/history").json()]
    assert sqls == ["SELECT * FROM dbo.Public"]
    assert not any("SecretRates" in s for s in sqls)


def test_failed_runs_are_kept(client):
    _add_history(
        client, ALICE["email"], "SELECT * FROM nope", status="error", error="Invalid object name"
    )
    client.as_user(ALICE)
    rows = client.get("/api/history").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert "Invalid object name" in rows[0]["error"]


def test_history_search_filters_within_own_rows_only(client):
    _add_history(client, ALICE["email"], "SELECT * FROM dbo.Sites")
    _add_history(client, BOB["email"], "SELECT * FROM dbo.Sites")

    client.as_user(ALICE)
    assert len(client.get("/api/history", params={"search": "Sites"}).json()) == 1


def test_clearing_history_only_clears_your_own(client):
    _add_history(client, ALICE["email"], "SELECT 1")
    _add_history(client, BOB["email"], "SELECT 2")

    client.as_user(ALICE)
    client.delete("/api/history")
    assert client.get("/api/history").json() == []

    client.as_user(BOB)
    assert len(client.get("/api/history").json()) == 1


# ── Snippets: sharing and ownership ─────────────────────────────────────────


def test_snippet_visible_to_owner_only_until_shared(client):
    client.as_user(ALICE)
    created = client.post(
        "/api/snippets", json={"name": "Alice private", "sql": "SELECT 1"}
    ).json()
    assert created["owner_email"] == ALICE["email"]
    assert created["is_shared"] is False

    client.as_user(BOB)
    assert client.get("/api/snippets").json() == []

    client.as_user(ALICE)
    client.put(f"/api/snippets/{created['id']}", json={"is_shared": True})

    client.as_user(BOB)
    names = [s["name"] for s in client.get("/api/snippets").json()]
    assert "Alice private" in names


def test_only_owner_can_edit_a_shared_snippet(client):
    client.as_user(ALICE)
    created = client.post(
        "/api/snippets",
        json={"name": "Team query", "sql": "SELECT 1", "is_shared": True},
    ).json()

    # Bob can read it but must not be able to rewrite what the team runs.
    client.as_user(BOB)
    assert client.put(f"/api/snippets/{created['id']}", json={"sql": "DROP TABLE x"}).status_code == 403
    assert client.delete(f"/api/snippets/{created['id']}").status_code == 403

    client.as_user(ALICE)
    assert client.put(f"/api/snippets/{created['id']}", json={"sql": "SELECT 2"}).status_code == 200


def test_use_count_bumps_and_is_open_to_readers(client):
    client.as_user(ALICE)
    created = client.post(
        "/api/snippets",
        json={"name": "Shared", "sql": "SELECT 1", "is_shared": True},
    ).json()

    client.as_user(BOB)
    bumped = client.post(f"/api/snippets/{created['id']}/used").json()
    assert bumped["use_count"] == 1


def test_private_snippet_use_count_hidden_from_others(client):
    client.as_user(ALICE)
    created = client.post("/api/snippets", json={"name": "Private", "sql": "SELECT 1"}).json()

    client.as_user(BOB)
    # 404 rather than 403 — Bob should not learn that this snippet exists.
    assert client.post(f"/api/snippets/{created['id']}/used").status_code == 404


def test_owner_can_delete_own_snippet(client):
    client.as_user(ALICE)
    created = client.post("/api/snippets", json={"name": "Temp", "sql": "SELECT 1"}).json()
    assert client.delete(f"/api/snippets/{created['id']}").status_code == 200
    assert client.get("/api/snippets").json() == []
