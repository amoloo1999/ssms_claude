"""Snowflake driver (snowflake-connector-python).

The connector is heavy (pulls pyarrow), so it is imported lazily inside
``connect`` — the app boots fine without the wheel installed, and only a server
actually configured as Snowflake triggers the import.

Mapping notes:
- The ServerConnection ``host`` holds the Snowflake **account identifier**
  (e.g. ``ab12345.us-west-1``); ``port`` is unused. ``database`` is required.
  An optional warehouse can be appended to the account as
  ``<account>?warehouse=<wh>`` (parsed out here) for accounts without a default.
- Snowflake has no indexes (constraints are informational only), so
  ``indexes_sql`` returns None and primary keys report as 0.

Status: wired through the abstraction, pending validation against a live
Snowflake account (we don't have one yet).
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from app.services.drivers.base import DatabaseDriver


class SnowflakeDriver(DatabaseDriver):
    dialect = "snowflake"
    display_name = "Snowflake"
    default_port = 443
    paramstyle = "format"  # connector default paramstyle is pyformat (%s)
    default_schema = "PUBLIC"
    supports_cancel = False
    cross_database_supported = False
    single_database = True

    # ── connection lifecycle ─────────────────────────────────────────────────
    def build_connection_string(
        self, host, port, username, password, database: Optional[str] = None
    ) -> str:
        # Allow "<account>?warehouse=<wh>&role=<role>" in the host field.
        account = host
        warehouse = None
        role = None
        if "?" in (host or ""):
            split = urlsplit("//" + host)
            account = split.netloc or host.split("?", 1)[0]
            qs = parse_qs(split.query)
            warehouse = (qs.get("warehouse") or [None])[0]
            role = (qs.get("role") or [None])[0]
        kw = {
            "account": account,
            "user": username,
            "password": password,
            "database": database,
            "warehouse": warehouse,
            "role": role,
        }
        return json.dumps({k: v for k, v in kw.items() if v is not None}, sort_keys=True)

    def connect(self, conn_str: str):
        import snowflake.connector

        return snowflake.connector.connect(**json.loads(conn_str))

    def requires_commit(self) -> bool:
        # Snowflake autocommits by default; an explicit commit is a harmless
        # no-op but unnecessary.
        return False

    # ── SQL shaping ──────────────────────────────────────────────────────────
    def placeholder(self, index: int = 0) -> str:
        return "%s"

    # ── introspection ────────────────────────────────────────────────────────
    def list_databases_sql(self) -> Optional[str]:
        return None  # single-database engine

    def list_tables_sql(self) -> str:
        return """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE' AND table_schema <> 'INFORMATION_SCHEMA'
        ORDER BY table_schema, table_name
        """

    def list_views_sql(self) -> str:
        return """
        SELECT table_schema, table_name
        FROM information_schema.views
        WHERE table_schema <> 'INFORMATION_SCHEMA'
        ORDER BY table_schema, table_name
        """

    def list_functions_sql(self) -> Optional[str]:
        return """
        SELECT function_schema, function_name
        FROM information_schema.functions
        WHERE function_schema <> 'INFORMATION_SCHEMA'
        ORDER BY function_schema, function_name
        """

    def schema_snapshot_tables_sql(self) -> str:
        return """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_type IN ('BASE TABLE', 'VIEW')
          AND table_schema <> 'INFORMATION_SCHEMA'
        ORDER BY table_schema, table_name
        """

    def schema_snapshot_columns_sql(self) -> str:
        return """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema <> 'INFORMATION_SCHEMA'
        ORDER BY table_schema, table_name, ordinal_position
        """

    def columns_sql(self, schema: str, table: str) -> tuple[str, tuple]:
        # Snowflake PK constraints are informational and not in a simple
        # information_schema join, so is_primary_key is reported as 0.
        sql = """
        SELECT
            column_name,
            data_type,
            character_maximum_length,
            is_nullable,
            column_default,
            0 AS is_primary_key,
            ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """
        return sql, (schema, table)

    def indexes_sql(self, schema: str, table: str) -> Optional[tuple[str, tuple]]:
        return None  # Snowflake has no indexes
