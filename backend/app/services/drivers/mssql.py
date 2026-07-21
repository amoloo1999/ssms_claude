"""Microsoft SQL Server driver (pyodbc + ODBC Driver 17).

This is the original engine — the logic here was lifted verbatim from the old
``services.connection`` and ``routers.explorer`` so SQL Server behavior is
byte-identical after the multi-provider refactor.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import pyodbc

from app.services.drivers.base import DatabaseDriver

# Match `GO` on its own line (optional whitespace, optional repeat count) — the
# T-SQL batch separator. SSMS treats this as a client-side directive; pyodbc
# does not understand it, so we split user SQL on these markers ourselves.
_GO_SPLIT_RE = re.compile(r"(?im)^[ \t]*GO(?:[ \t]+\d+)?[ \t]*$")


class MssqlDriver(DatabaseDriver):
    dialect = "mssql"
    display_name = "SQL Server"
    default_port = 1433
    paramstyle = "qmark"
    default_schema = "dbo"
    supports_cancel = True
    cross_database_supported = True
    single_database = False

    # ── connection lifecycle ─────────────────────────────────────────────────
    def build_connection_string(
        self, host, port, username, password, database: Optional[str] = None
    ) -> str:
        return (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={host},{port};"
            f"DATABASE={database or 'master'};"
            f"UID={username};"
            f"PWD={password};"
            f"TrustServerCertificate=yes;"
            f"Connection Timeout=10;"
        )

    def connect(self, conn_str: str):
        # autocommit=True is REQUIRED, not a preference. With pyodbc's default
        # (autocommit off) the SQL Server ODBC driver wraps every statement --
        # including the pool's `SELECT 1` liveness probe and every
        # INFORMATION_SCHEMA read -- in an implicit transaction that only an
        # explicit COMMIT/ROLLBACK closes. A pooled connection then sits idle
        # holding that transaction open for hours, pinning locks and the RCSI
        # tempdb version store. That is the 2026-07-07 lock-storm mechanism, and
        # it recurred (26 sessions idle ~22h, one per database) because the only
        # reset was on pool RETURN (_return_to_pool) while the probe reopens a
        # transaction on pool CHECKOUT -- the wrong end of the lifecycle.
        # Autocommit removes the implicit transaction entirely, so there is
        # nothing to leak regardless of checkout/return bookkeeping.
        # execute_query still calls commit() explicitly; under autocommit that is
        # a harmless no-op, and the app exposes no BEGIN TRAN / rollback-on-error
        # semantics that would need autocommit off.
        return pyodbc.connect(conn_str, timeout=10, autocommit=True)

    def probe(self, conn) -> None:
        # pyodbc connections expose .execute() directly (matches the original
        # pool liveness probe).
        conn.execute("SELECT 1")

    def cancel(self, conn) -> bool:
        # pyodbc Connection.cancel() sends an attention signal to SQL Server
        # from another thread, abandoning the running statement.
        conn.cancel()
        return True

    def prepare_cursor(self, cursor) -> None:
        # SET NOCOUNT ON prevents per-statement DONE_IN_PROC rowcount messages
        # from masquerading as empty result sets when iterating multi-statement
        # queries with cursor.nextset().
        cursor.execute("SET NOCOUNT ON")
        cursor.nextset()  # drain

    # ── SQL shaping ──────────────────────────────────────────────────────────
    def split_batches(self, sql: str) -> list[str]:
        parts = [p.strip() for p in _GO_SPLIT_RE.split(sql or "")]
        return [p for p in parts if p] or [sql]

    def quote_ident(self, name: str) -> str:
        return "[" + name.replace("]", "]]") + "]"

    def placeholder(self, index: int = 0) -> str:
        return "?"

    def paginate(
        self, inner_sql, limit, offset, order_by: Optional[str] = None
    ) -> str:
        # T-SQL OFFSET/FETCH requires an ORDER BY; (SELECT NULL) is the no-op
        # ordering SSMS-style tools use when the caller didn't pick a column.
        order = order_by or "(SELECT NULL)"
        return (
            f"{inner_sql} ORDER BY {order} "
            f"OFFSET {int(offset)} ROWS FETCH NEXT {int(limit)} ROWS ONLY"
        )

    def serialize_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float, str, bool)):
            return value
        if isinstance(value, bytes):
            return value.hex()
        return str(value)

    # ── introspection ────────────────────────────────────────────────────────
    def list_databases_sql(self) -> Optional[str]:
        return "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' ORDER BY name"

    def list_tables_sql(self) -> str:
        return """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """

    def list_views_sql(self) -> str:
        return """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.VIEWS
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """

    def list_procedures_sql(self) -> Optional[str]:
        return """
        SELECT ROUTINE_SCHEMA, ROUTINE_NAME
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE = 'PROCEDURE'
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """

    def list_functions_sql(self) -> Optional[str]:
        return """
        SELECT ROUTINE_SCHEMA, ROUTINE_NAME
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE = 'FUNCTION'
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """

    def schema_snapshot_tables_sql(self) -> str:
        return """
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW')
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """

    def schema_snapshot_columns_sql(self) -> str:
        return """
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """

    def columns_sql(self, schema: str, table: str) -> tuple[str, tuple]:
        sql = """
        SELECT
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.CHARACTER_MAXIMUM_LENGTH,
            c.IS_NULLABLE,
            c.COLUMN_DEFAULT,
            CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END AS IS_PRIMARY_KEY,
            c.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS c
        LEFT JOIN (
            SELECT ku.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                AND tc.TABLE_SCHEMA = ?
                AND tc.TABLE_NAME = ?
        ) pk ON c.COLUMN_NAME = pk.COLUMN_NAME
        WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ?
        ORDER BY c.ORDINAL_POSITION
        """
        return sql, (schema, table, schema, table)

    def indexes_sql(self, schema: str, table: str) -> Optional[tuple[str, tuple]]:
        sql = """
        SELECT
            i.name AS index_name,
            i.type_desc,
            i.is_unique,
            i.is_primary_key,
            STRING_AGG(c.name, ', ') WITHIN GROUP (ORDER BY ic.key_ordinal) AS columns
        FROM sys.indexes i
        JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
        JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
        JOIN sys.tables t ON i.object_id = t.object_id
        JOIN sys.schemas s ON t.schema_id = s.schema_id
        WHERE s.name = ? AND t.name = ?
        GROUP BY i.name, i.type_desc, i.is_unique, i.is_primary_key
        ORDER BY i.name
        """
        return sql, (schema, table)
