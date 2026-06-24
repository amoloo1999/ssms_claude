"""PostgreSQL / Aurora-PostgreSQL driver (psycopg 3).

Aurora-PG speaks the vanilla PostgreSQL wire protocol, so this driver serves
both. A connection binds to a single database; namespacing is via schemas
(``public``, ``gold``, …), which surface in the explorer in place of SQL
Server's multiple-databases-per-server model.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.services.drivers.base import DatabaseDriver

# psycopg (v3) is imported lazily inside the methods that need it, so the app
# boots — and the pure SQL-shaping methods stay importable/testable — even when
# the wheel isn't installed. Only a server actually configured as Postgres
# triggers the import.

# System schemas hidden from the object explorer.
_SYS_SCHEMAS = "('pg_catalog', 'information_schema')"


class PostgresDriver(DatabaseDriver):
    dialect = "postgres"
    display_name = "PostgreSQL"
    default_port = 5432
    paramstyle = "format"  # %s
    default_schema = "public"
    supports_cancel = True
    cross_database_supported = False
    single_database = True

    # ── connection lifecycle ─────────────────────────────────────────────────
    def build_connection_string(
        self, host, port, username, password, database: Optional[str] = None
    ) -> str:
        from psycopg.conninfo import make_conninfo

        # make_conninfo escapes values safely (passwords with spaces/quotes).
        # sslmode=prefer matches libpq's default: try TLS (Aurora/RDS), fall
        # back to plaintext for a local dev Postgres.
        return make_conninfo(
            host=host,
            port=int(port),
            dbname=database or "postgres",
            user=username,
            password=password,
            connect_timeout=10,
            sslmode="prefer",
        )

    def connect(self, conn_str: str):
        import psycopg

        return psycopg.connect(conn_str)

    def cancel(self, conn) -> bool:
        # psycopg3 Connection.cancel() issues a libpq cancel request and is safe
        # to call from another thread than the one running the query.
        conn.cancel()
        return True

    # ── SQL shaping ──────────────────────────────────────────────────────────
    def placeholder(self, index: int = 0) -> str:
        return "%s"

    def serialize_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (int, float, str, bool)):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).hex()
        if isinstance(value, (dict, list)):
            # JSON/JSONB columns come back as Python structures.
            try:
                return json.dumps(value, default=str)
            except Exception:
                return str(value)
        # Decimal, datetime/date/time, UUID, etc.
        return str(value)

    # ── introspection ────────────────────────────────────────────────────────
    def list_databases_sql(self) -> Optional[str]:
        # Single-database engine: the explorer surfaces only the configured DB.
        return None

    def list_tables_sql(self) -> str:
        return f"""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN {_SYS_SCHEMAS}
        ORDER BY table_schema, table_name
        """

    def list_views_sql(self) -> str:
        return f"""
        SELECT table_schema, table_name
        FROM information_schema.views
        WHERE table_schema NOT IN {_SYS_SCHEMAS}
        ORDER BY table_schema, table_name
        """

    def list_procedures_sql(self) -> Optional[str]:
        return f"""
        SELECT routine_schema, routine_name
        FROM information_schema.routines
        WHERE routine_type = 'PROCEDURE'
          AND routine_schema NOT IN {_SYS_SCHEMAS}
        ORDER BY routine_schema, routine_name
        """

    def list_functions_sql(self) -> Optional[str]:
        return f"""
        SELECT routine_schema, routine_name
        FROM information_schema.routines
        WHERE routine_type = 'FUNCTION'
          AND routine_schema NOT IN {_SYS_SCHEMAS}
        ORDER BY routine_schema, routine_name
        """

    def schema_snapshot_tables_sql(self) -> str:
        return f"""
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_type IN ('BASE TABLE', 'VIEW')
          AND table_schema NOT IN {_SYS_SCHEMAS}
        ORDER BY table_schema, table_name
        """

    def schema_snapshot_columns_sql(self) -> str:
        return f"""
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN {_SYS_SCHEMAS}
        ORDER BY table_schema, table_name, ordinal_position
        """

    def columns_sql(self, schema: str, table: str) -> tuple[str, tuple]:
        sql = """
        SELECT
            c.column_name,
            c.data_type,
            c.character_maximum_length,
            c.is_nullable,
            c.column_default,
            CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key,
            c.ordinal_position
        FROM information_schema.columns c
        LEFT JOIN (
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
                AND tc.table_schema = %s
                AND tc.table_name = %s
        ) pk ON c.column_name = pk.column_name
        WHERE c.table_schema = %s AND c.table_name = %s
        ORDER BY c.ordinal_position
        """
        return sql, (schema, table, schema, table)

    def indexes_sql(self, schema: str, table: str) -> Optional[tuple[str, tuple]]:
        sql = """
        SELECT
            i.relname AS index_name,
            am.amname AS type_desc,
            CASE WHEN ix.indisunique THEN 1 ELSE 0 END AS is_unique,
            CASE WHEN ix.indisprimary THEN 1 ELSE 0 END AS is_primary_key,
            string_agg(a.attname, ', ' ORDER BY k.ord) AS columns
        FROM pg_index ix
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_am am ON am.oid = i.relam
        JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE n.nspname = %s AND t.relname = %s
        GROUP BY i.relname, am.amname, ix.indisunique, ix.indisprimary
        ORDER BY i.relname
        """
        return sql, (schema, table)
