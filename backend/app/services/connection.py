import asyncio
import pyodbc
import re
import time
import threading
from typing import Optional
from contextlib import contextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import ServerConnection


# Match `GO` on its own line (optional whitespace, optional repeat count) — the
# T-SQL batch separator. SSMS treats this as a client-side directive; pyodbc
# does not understand it, so we split user SQL on these markers ourselves.
_GO_SPLIT_RE = re.compile(r"(?im)^[ \t]*GO(?:[ \t]+\d+)?[ \t]*$")


# Connection pool: keyed by connection string
# Each entry holds a list of idle connections and a lock
_pools: dict[str, list[pyodbc.Connection]] = {}
_pool_lock = threading.Lock()
_MAX_POOL_SIZE = 5


def build_connection_string(host: str, port: int, username: str, password: str, database: str = "master") -> str:
    return (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={host},{port};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
        f"Connection Timeout=10;"
    )


async def get_connection_string(db: AsyncSession, server_id: int, database: str = "master") -> str:
    result = await db.execute(
        select(ServerConnection).where(ServerConnection.id == server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise ValueError(f"Server with id {server_id} not found")

    return build_connection_string(server.host, server.port, server.username, server.password, database)


def _get_pooled_connection(connection_string: str) -> pyodbc.Connection:
    """Get a live connection from the pool, or create a new one.

    The liveness probe (SELECT 1) is run OUTSIDE `_pool_lock`. Probing while
    holding the global lock means a single hung or half-dead pooled connection
    would block connection acquisition for *every* tab and server until its
    probe returned — re-creating the app-wide freeze symptom. So we pop one
    candidate under the lock, then test it lock-free, looping to the next
    candidate if it's dead."""
    while True:
        with _pool_lock:
            pool = _pools.get(connection_string)
            conn = pool.pop() if pool else None
        if conn is None:
            break
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            # dead connection — discard and try the next pooled candidate

    return pyodbc.connect(connection_string, timeout=10)


def _return_to_pool(connection_string: str, conn: pyodbc.Connection):
    """Return a connection to the pool for reuse."""
    with _pool_lock:
        pool = _pools.setdefault(connection_string, [])
        if len(pool) < _MAX_POOL_SIZE:
            pool.append(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass


@contextmanager
def get_sql_connection(connection_string: str):
    conn = _get_pooled_connection(connection_string)
    try:
        yield conn
        _return_to_pool(connection_string, conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        raise


# ── Query cancellation ──────────────────────────────────────────────────────
# Registry of in-flight, cancellable pyodbc connections keyed by a
# client-supplied query_id. This lets a separate HTTP request (the Stop button)
# reach the connection running a long query and send it an attention signal.
_running: dict[str, pyodbc.Connection] = {}
_running_lock = threading.Lock()
# query_ids the user asked to cancel — used so the worker reports "cancelled"
# rather than a raw ODBC abort error.
_cancelled: set[str] = set()


def cancel_query(query_id: str) -> bool:
    """Abort the in-flight statement associated with query_id.

    pyodbc's `Connection.cancel()` is explicitly designed to be called from a
    different thread than the one running the query: it sends an attention
    signal to SQL Server, which abandons the running statement and raises an
    error in the executing thread. Returns True if a running query was found
    and a cancel was issued. This is purely user-initiated — it never caps,
    truncates, or times out a query on its own."""
    with _running_lock:
        conn = _running.get(query_id)
        if conn is None:
            return False
        _cancelled.add(query_id)
    # cancel() is issued outside the lock so a slow attention round-trip can't
    # stall other acquisitions. Narrow race: if the query finished in the
    # microseconds since we read it, we may cancel a reused connection — sub-ms
    # window, worst case one unlucky query restarts.
    try:
        conn.cancel()
        return True
    except Exception:
        return False


def _split_batches(sql: str) -> list[str]:
    """Split SQL on GO batch separators. If params are passed (single-batch
    parameterized query), the caller skips this and runs as one batch."""
    parts = [p.strip() for p in _GO_SPLIT_RE.split(sql or "")]
    return [p for p in parts if p]


def execute_query(
    connection_string: str,
    sql: str,
    params: Optional[tuple] = None,
    query_id: Optional[str] = None,
) -> dict:
    start = time.time()
    try:
        # Parameterized queries always run as a single batch — splitting on GO
        # would break parameter binding.
        if params:
            batches = [sql]
        else:
            batches = _split_batches(sql) or [sql]

        result_sets: list[dict] = []
        total_affected = 0

        with get_sql_connection(connection_string) as conn:
            # Register this connection so the Stop button can reach it from
            # another thread. A connection that gets cancelled raises below and
            # is closed (not pooled) by get_sql_connection's except path.
            if query_id:
                with _running_lock:
                    _running[query_id] = conn
            try:
                cursor = conn.cursor()
                # SET NOCOUNT ON prevents per-statement DONE_IN_PROC rowcount
                # messages from masquerading as empty result sets when iterating
                # multi-statement queries with cursor.nextset().
                cursor.execute("SET NOCOUNT ON")
                cursor.nextset()  # drain

                for batch in batches:
                    if params:
                        cursor.execute(batch, params)
                    else:
                        cursor.execute(batch)

                    while True:
                        if cursor.description:
                            columns = [col[0] for col in cursor.description]
                            rows = [
                                [_serialize_value(v) for v in row]
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
                        if not cursor.nextset():
                            break

                # Commit any non-result statements (INSERT/UPDATE/DELETE)
                try:
                    conn.commit()
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
        # ODBC abort error ("Operation canceled" / HY008).
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
    connection_string: str,
    sql: str,
    params: Optional[tuple] = None,
    query_id: Optional[str] = None,
) -> dict:
    """Run execute_query in a worker thread so the FastAPI event loop is not
    blocked while a long-running SQL statement is in flight. Without this, a
    slow query stalls every other in-flight request — including unrelated tabs,
    health checks, and the explorer panel — because pyodbc's blocking I/O ties
    up the single asyncio loop thread."""
    return await asyncio.to_thread(execute_query, connection_string, sql, params, query_id)


def _serialize_value(value):
    """Convert SQL Server values to JSON-safe types."""
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
