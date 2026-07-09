"""Engine-agnostic connection layer.

This is a thin facade over ``app.services.drivers``. It keeps the public names
the routers already import — ``get_connection_string``, ``execute_query``,
``execute_query_async``, ``cancel_query``, ``get_sql_connection``,
``build_connection_string`` — but every engine-specific detail (driver/library,
identifier quoting, batch splitting, value serialization, cancellation, the
introspection SQL) now lives in a ``DatabaseDriver``.

What stays here is the engine-agnostic plumbing:
- the connection pool (keyed by ``(dialect, conn_str)``),
- the in-flight cancel registry behind the Stop button,
- the multi-result-set drain loop in ``execute_query`` (DBAPI cursor interface),
- the ``asyncio.to_thread`` wrapper that keeps the event loop unblocked.

A ``ConnHandle`` (dialect + conn_str) flows through instead of a bare string. For
back-compat, a raw string handed to ``execute_query``/``get_sql_connection`` is
treated as a SQL Server DSN.
"""

import asyncio
import time
import threading
from typing import Optional, Union
from contextlib import contextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import ServerConnection
from app.services.drivers import ConnHandle, DatabaseDriver, get_driver


# Connection pool: keyed by (dialect, connection string). Each entry holds a
# list of idle connections; the global lock guards the dict, never a probe.
_pools: dict[tuple[str, str], list] = {}
_pool_lock = threading.Lock()
_MAX_POOL_SIZE = 5


def _as_handle(conn: Union[ConnHandle, str]) -> ConnHandle:
    """Accept a ConnHandle or a legacy raw string (assumed to be an mssql DSN)."""
    if isinstance(conn, ConnHandle):
        return conn
    return ConnHandle("mssql", conn)


def build_connection_string(
    host: str,
    port: int,
    username: str,
    password: str,
    database: Optional[str] = None,
    dialect: str = "mssql",
) -> ConnHandle:
    """Build a ConnHandle directly from credentials. Defaults to SQL Server so
    existing callers keep working unchanged."""
    driver = get_driver(dialect)
    conn_str = driver.build_connection_string(host, port, username, password, database)
    return ConnHandle(dialect, conn_str)


async def get_connection_string(
    db: AsyncSession, server_id: int, database: str = "master"
) -> ConnHandle:
    result = await db.execute(
        select(ServerConnection).where(ServerConnection.id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise ValueError(f"Server with id {server_id} not found")

    driver = get_driver(server.dialect)
    # Single-database engines (Postgres/MySQL/Snowflake) always bind to the
    # server's configured database — the per-request `database` arg only varies
    # for SQL Server, which switches databases via the connection string.
    conn_db = (server.database or database) if driver.single_database else database
    conn_str = driver.build_connection_string(
        server.host, server.port, server.username, server.password, conn_db
    )
    return ConnHandle(server.dialect, conn_str)


def _pool_key(handle: ConnHandle) -> tuple[str, str]:
    return (handle.dialect, handle.conn_str)


def _get_pooled_connection(handle: ConnHandle):
    """Get a live connection from the pool, or open a new one.

    The liveness probe runs OUTSIDE ``_pool_lock``: probing while holding the
    global lock means a single hung/half-dead pooled connection would block
    acquisition for *every* tab and server until its probe returned. So we pop
    one candidate under the lock, then test it lock-free, looping to the next
    candidate if it's dead."""
    driver = handle.driver
    key = _pool_key(handle)
    while True:
        with _pool_lock:
            pool = _pools.get(key)
            conn = pool.pop() if pool else None
        if conn is None:
            break
        try:
            driver.probe(conn)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            # dead connection — discard and try the next pooled candidate

    return driver.connect(handle.conn_str)


def _return_to_pool(handle: ConnHandle, conn):
    """Return a connection to the pool for reuse.

    Reset transaction state before pooling. A connection can come back with an
    open transaction — the liveness ``probe()``'s ``SELECT 1`` or an
    introspection/schema read on an autocommit-off driver (SQL Server) opens a
    transaction that only ``execute_query`` commits. Left unreset, that idle
    pooled connection holds the transaction open for hours, pinning locks and,
    under RCSI, the tempdb version store. ``rollback()`` is a no-op for work
    ``execute_query`` already committed and clears any leftover read
    transaction; if it can't reset, we discard rather than pool a connection in
    an unknown state.
    """
    try:
        conn.rollback()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return
    key = _pool_key(handle)
    with _pool_lock:
        pool = _pools.setdefault(key, [])
        if len(pool) < _MAX_POOL_SIZE:
            pool.append(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass


@contextmanager
def get_sql_connection(conn: Union[ConnHandle, str]):
    handle = _as_handle(conn)
    conn_obj = _get_pooled_connection(handle)
    try:
        yield conn_obj
        _return_to_pool(handle, conn_obj)
    except Exception:
        try:
            conn_obj.close()
        except Exception:
            pass
        raise


# ── Query cancellation ──────────────────────────────────────────────────────
# Registry of in-flight, cancellable connections keyed by a client-supplied
# query_id. This lets a separate HTTP request (the Stop button) reach the
# connection running a long query and ask its driver to abort it. Stores the
# driver alongside the connection because each engine cancels differently.
_running: dict[str, tuple[DatabaseDriver, object]] = {}
_running_lock = threading.Lock()
# query_ids the user asked to cancel — used so the worker reports "cancelled"
# rather than a raw driver abort error.
_cancelled: set[str] = set()


def cancel_query(query_id: str) -> bool:
    """Abort the in-flight statement associated with query_id.

    Delegates to the driver's ``cancel`` (called from a different thread than
    the one running the query). Returns True if a running query was found and a
    cancel was issued. Engines without thread-safe cancel return False. This is
    purely user-initiated — it never caps, truncates, or times out a query on
    its own."""
    with _running_lock:
        entry = _running.get(query_id)
        if entry is None:
            return False
        _cancelled.add(query_id)
    # cancel is issued outside the lock so a slow attention round-trip can't
    # stall other acquisitions.
    driver, conn = entry
    try:
        return driver.cancel(conn)
    except Exception:
        return False


def _safe_nextset(cursor) -> bool:
    """Advance to the next result set. Tolerates drivers that don't implement
    nextset() (e.g. Snowflake) by reporting "no more sets"."""
    nextset = getattr(cursor, "nextset", None)
    if nextset is None:
        return False
    try:
        return bool(nextset())
    except Exception:
        return False


def execute_query(
    conn: Union[ConnHandle, str],
    sql: str,
    params: Optional[tuple] = None,
    query_id: Optional[str] = None,
) -> dict:
    handle = _as_handle(conn)
    driver = handle.driver
    start = time.time()
    try:
        # Parameterized queries always run as a single batch — splitting would
        # break parameter binding.
        if params:
            batches = [sql]
        else:
            batches = driver.split_batches(sql) or [sql]

        result_sets: list[dict] = []
        total_affected = 0

        with get_sql_connection(handle) as conn_obj:
            # Register this connection so the Stop button can reach it from
            # another thread. A cancelled connection raises below and is closed
            # (not pooled) by get_sql_connection's except path.
            if query_id:
                with _running_lock:
                    _running[query_id] = (driver, conn_obj)
            try:
                cursor = conn_obj.cursor()
                # Per-engine preamble (SQL Server: SET NOCOUNT ON).
                driver.prepare_cursor(cursor)

                for batch in batches:
                    if params:
                        cursor.execute(batch, params)
                    else:
                        cursor.execute(batch)

                    while True:
                        if cursor.description:
                            columns = [col[0] for col in cursor.description]
                            rows = [
                                [driver.serialize_value(v) for v in row]
                                for row in cursor.fetchall()
                            ]
                            result_sets.append({
                                "columns": columns,
                                "rows": rows,
                                "row_count": len(rows),
                            })
                        else:
                            if cursor.rowcount and cursor.rowcount > 0:
                                total_affected += cursor.rowcount
                        if not _safe_nextset(cursor):
                            break

                # Commit any non-result statements (INSERT/UPDATE/DELETE) and
                # end the read transaction before the connection is pooled.
                if driver.requires_commit():
                    try:
                        conn_obj.commit()
                    except Exception:
                        pass
            finally:
                # Deregister BEFORE get_sql_connection hands the connection back
                # to the pool, so a late Stop can't cancel a reused connection.
                if query_id:
                    with _running_lock:
                        _running.pop(query_id, None)

            elapsed = (time.time() - start) * 1000

            # Backwards-compat: first set's columns/rows mirror result_sets[0]
            if result_sets:
                first = result_sets[0]
                return {
                    "columns": first["columns"],
                    "rows": first["rows"],
                    "row_count": first["row_count"],
                    "result_sets": result_sets,
                    "execution_time_ms": round(elapsed, 2),
                    "error": None,
                }
            return {
                "columns": [],
                "rows": [],
                "row_count": total_affected,
                "result_sets": [],
                "execution_time_ms": round(elapsed, 2),
                "error": None,
            }
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        # If the user hit Stop, report a clean cancellation rather than the raw
        # driver abort error.
        was_cancelled = False
        if query_id:
            with _running_lock:
                was_cancelled = query_id in _cancelled
                _cancelled.discard(query_id)
                _running.pop(query_id, None)
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "result_sets": [],
            "execution_time_ms": round(elapsed, 2),
            "error": "Query cancelled." if was_cancelled else str(e),
        }


async def execute_query_async(
    conn: Union[ConnHandle, str],
    sql: str,
    params: Optional[tuple] = None,
    query_id: Optional[str] = None,
) -> dict:
    """Run execute_query in a worker thread so the FastAPI event loop is not
    blocked while a long-running SQL statement is in flight. Without this, a
    slow query stalls every other in-flight request — including unrelated tabs,
    health checks, and the explorer panel — because the DBAPI driver's blocking
    I/O ties up the single asyncio loop thread."""
    return await asyncio.to_thread(execute_query, conn, sql, params, query_id)
