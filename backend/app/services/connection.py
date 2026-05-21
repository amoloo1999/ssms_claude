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
    """Get a connection from the pool, or create a new one."""
    with _pool_lock:
        pool = _pools.get(connection_string, [])
        while pool:
            conn = pool.pop()
            try:
                # Test if connection is still alive
                conn.execute("SELECT 1")
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        _pools[connection_string] = pool

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


def _split_batches(sql: str) -> list[str]:
    """Split SQL on GO batch separators. If params are passed (single-batch
    parameterized query), the caller skips this and runs as one batch."""
    parts = [p.strip() for p in _GO_SPLIT_RE.split(sql or "")]
    return [p for p in parts if p]


def execute_query(connection_string: str, sql: str, params: Optional[tuple] = None) -> dict:
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
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "result_sets": [],
            "execution_time_ms": round(elapsed, 2),
            "error": str(e),
        }


async def execute_query_async(
    connection_string: str, sql: str, params: Optional[tuple] = None
) -> dict:
    """Run execute_query in a worker thread so the FastAPI event loop is not
    blocked while a long-running SQL statement is in flight. Without this, a
    slow query stalls every other in-flight request — including unrelated tabs,
    health checks, and the explorer panel — because pyodbc's blocking I/O ties
    up the single asyncio loop thread."""
    return await asyncio.to_thread(execute_query, connection_string, sql, params)


def _serialize_value(value):
    """Convert SQL Server values to JSON-safe types."""
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
