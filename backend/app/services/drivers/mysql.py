"""MySQL / Aurora-MySQL / MariaDB driver (PyMySQL).

MySQL has no schema layer: a "database" *is* a schema. So throughout, the
``schema`` field carries the database name (which is what
``information_schema`` reports in ``table_schema``), and grants are stored with
``schema_name`` == database name.

PyMySQL has no connection-string format, so ``build_connection_string`` encodes
the connect kwargs as JSON and ``connect`` decodes them. The JSON is also the
pool key — stable and unique per credential set.
"""

from __future__ import annotations

import json
from typing import Optional

from app.services.drivers.base import DatabaseDriver


class MysqlDriver(DatabaseDriver):
    dialect = "mysql"
    display_name = "MySQL"
    default_port = 3306
    paramstyle = "format"  # %s
    default_schema = "mysql"
    supports_cancel = False
    cross_database_supported = False
    single_database = True

    def default_schema_for(self, database: Optional[str] = None) -> str:
        # No schema layer — unqualified tables live in the connected database.
        return database or self.default_schema

    # ── connection lifecycle ─────────────────────────────────────────────────
    def build_connection_string(
        self, host, port, username, password, database: Optional[str] = None
    ) -> str:
        return json.dumps(
            {
                "host": host,
                "port": int(port),
                "user": username,
                "password": password,
                "database": database,
                "connect_timeout": 10,
                "charset": "utf8mb4",
                "autocommit": False,
            },
            sort_keys=True,
        )

    def connect(self, conn_str: str):
        import pymysql

        return pymysql.connect(**json.loads(conn_str))

    # ── SQL shaping ──────────────────────────────────────────────────────────
    def quote_ident(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    def placeholder(self, index: int = 0) -> str:
        return "%s"

    # ── introspection ────────────────────────────────────────────────────────
    def list_databases_sql(self) -> Optional[str]:
        return None  # single-database engine

    def list_tables_sql(self) -> str:
        return """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE' AND table_schema = DATABASE()
        ORDER BY table_schema, table_name
        """

    def list_views_sql(self) -> str:
        return """
        SELECT table_schema, table_name
        FROM information_schema.views
        WHERE table_schema = DATABASE()
        ORDER BY table_schema, table_name
        """

    def list_procedures_sql(self) -> Optional[str]:
        return """
        SELECT routine_schema, routine_name
        FROM information_schema.routines
        WHERE routine_type = 'PROCEDURE' AND routine_schema = DATABASE()
        ORDER BY routine_schema, routine_name
        """

    def list_functions_sql(self) -> Optional[str]:
        return """
        SELECT routine_schema, routine_name
        FROM information_schema.routines
        WHERE routine_type = 'FUNCTION' AND routine_schema = DATABASE()
        ORDER BY routine_schema, routine_name
        """

    def schema_snapshot_tables_sql(self) -> str:
        return """
        SELECT table_schema, table_name,
               CASE WHEN table_type = 'VIEW' THEN 'VIEW' ELSE 'BASE TABLE' END
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_type IN ('BASE TABLE', 'VIEW')
        ORDER BY table_schema, table_name
        """

    def schema_snapshot_columns_sql(self) -> str:
        return """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
        ORDER BY table_schema, table_name, ordinal_position
        """

    def columns_sql(self, schema: str, table: str) -> tuple[str, tuple]:
        sql = """
        SELECT
            column_name,
            data_type,
            character_maximum_length,
            is_nullable,
            column_default,
            CASE WHEN column_key = 'PRI' THEN 1 ELSE 0 END AS is_primary_key,
            ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """
        return sql, (schema, table)

    def indexes_sql(self, schema: str, table: str) -> Optional[tuple[str, tuple]]:
        # information_schema.statistics is one row per index column; aggregate to
        # the (index_name, type, is_unique, is_pk, columns_csv) shape the
        # explorer expects.
        sql = """
        SELECT
            index_name,
            index_type AS type_desc,
            CASE WHEN MAX(non_unique) = 0 THEN 1 ELSE 0 END AS is_unique,
            CASE WHEN index_name = 'PRIMARY' THEN 1 ELSE 0 END AS is_primary_key,
            GROUP_CONCAT(column_name ORDER BY seq_in_index SEPARATOR ', ') AS columns
        FROM information_schema.statistics
        WHERE table_schema = %s AND table_name = %s
        GROUP BY index_name, index_type
        ORDER BY index_name
        """
        return sql, (schema, table)
