import pyodbc
import time
import threading
from typing import Optional
from contextlib import contextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import ServerConnection


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


def execute_query(connection_string: str, sql: str, params: Optional[tuple] = None) -> dict:
    start = time.time()
    try:
        with get_sql_connection(connection_string) as conn:
            cursor = conn.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)

            if cursor.description:
                columns = [col[0] for col in cursor.description]
                rows = []
                for row in cursor.fetchall():
                    rows.append([_serialize_value(v) for v in row])
                elapsed = (time.time() - start) * 1000
                return {
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "execution_time_ms": round(elapsed, 2),
                    "error": None,
                }
            else:
                conn.commit()
                elapsed = (time.time() - start) * 1000
                return {
                    "columns": [],
                    "rows": [],
                    "row_count": cursor.rowcount,
                    "execution_time_ms": round(elapsed, 2),
                    "error": None,
                }
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": round(elapsed, 2),
            "error": str(e),
        }


def _serialize_value(value):
    """Convert SQL Server values to JSON-safe types."""
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
