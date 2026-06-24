"""Database-driver abstraction.

Each supported engine (SQL Server, PostgreSQL/Aurora-PG, MySQL/Aurora-MySQL,
Snowflake) implements ``DatabaseDriver``. The driver owns everything that
differs between engines: how a connection string is built, how a connection is
opened/probed/cancelled, identifier quoting, parameter placeholders, pagination
syntax, value serialization, batch splitting, and the introspection SQL the
object explorer runs. ``services.connection`` keeps the engine-agnostic plumbing
(pool, cancel registry, the result-set drain loop) and delegates the rest here.

A ``ConnHandle`` is what flows through the connection layer instead of a bare
string: it pairs the raw connection string with the dialect so the executor can
look the driver back up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ConnHandle:
    """A connection string plus the dialect that produced it."""

    dialect: str
    conn_str: str

    @property
    def driver(self) -> "DatabaseDriver":
        # Imported lazily to avoid a circular import (registry imports drivers,
        # drivers import base).
        from app.services.drivers import get_driver

        return get_driver(self.dialect)


class DatabaseDriver(ABC):
    """Per-engine behavior. Subclasses set the class attributes and implement
    the abstract methods. Methods with a default implementation here are the
    ANSI/standard form (e.g. double-quote identifiers, ``%s`` placeholders) so
    a new driver only overrides what actually differs."""

    # ── identity / capabilities ──────────────────────────────────────────────
    dialect: str = "base"
    display_name: str = "Database"
    default_port: int = 0
    # 'qmark' -> ?   |   'format' -> %s
    paramstyle: str = "format"
    default_schema: str = "public"

    def default_schema_for(self, database: Optional[str] = None) -> str:
        """The schema to assume for unqualified table references. Same as
        ``default_schema`` for most engines; MySQL overrides it to the connected
        database (MySQL has no schema layer — schema == database)."""
        return self.default_schema

    supports_cancel: bool = False
    # True only for engines where one connection can query across databases
    # (SQL Server). Postgres/MySQL/Snowflake connect to a single database.
    cross_database_supported: bool = False
    # Engines that connect to one database at a time (no per-query DB switch).
    single_database: bool = True

    # ── connection lifecycle ─────────────────────────────────────────────────
    @abstractmethod
    def build_connection_string(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        database: Optional[str] = None,
    ) -> str:
        ...

    @abstractmethod
    def connect(self, conn_str: str):
        """Open a new DBAPI connection."""

    def probe(self, conn) -> None:
        """Cheap liveness check used by the pool. Raise if the connection is
        dead. Default works for any DBAPI driver."""
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1")
            cur.fetchall()
        finally:
            cur.close()

    def cancel(self, conn) -> bool:
        """Abort the statement in flight on ``conn`` from another thread.
        Return True if a cancel was issued. Engines without thread-safe cancel
        leave ``supports_cancel = False`` and return False here."""
        return False

    def prepare_cursor(self, cursor) -> None:
        """Run any per-cursor preamble before the user's batches. No-op by
        default; SQL Server uses it for ``SET NOCOUNT ON``."""
        return None

    def requires_commit(self) -> bool:
        """Whether a successful execution should ``commit()`` to end the
        transaction before the connection returns to the pool."""
        return True

    # ── SQL shaping ──────────────────────────────────────────────────────────
    def split_batches(self, sql: str) -> list[str]:
        """Split a script into independently-executed batches. Only SQL Server
        has a client-side batch separator (``GO``); everything else runs as a
        single batch."""
        return [sql]

    def quote_ident(self, name: str) -> str:
        """Quote a single identifier. ANSI default: double quotes."""
        return '"' + name.replace('"', '""') + '"'

    def quote_qualified(self, *parts: str) -> str:
        """Quote a dotted identifier, skipping empty parts."""
        return ".".join(self.quote_ident(p) for p in parts if p)

    def placeholder(self, index: int = 0) -> str:
        """A bound-parameter placeholder. ``index`` is 0-based for engines that
        number them; positional engines ignore it."""
        return "?" if self.paramstyle == "qmark" else "%s"

    def paginate(
        self, inner_sql: str, limit: int, offset: int, order_by: Optional[str] = None
    ) -> str:
        """Wrap/append pagination. ANSI default: LIMIT/OFFSET."""
        order = f"ORDER BY {order_by}" if order_by else ""
        return f"{inner_sql} {order} LIMIT {int(limit)} OFFSET {int(offset)}".strip()

    def serialize_value(self, value: Any) -> Any:
        """Convert a driver-returned cell to a JSON-safe value."""
        if value is None or isinstance(value, (int, float, str, bool)):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).hex()
        return str(value)

    # ── introspection SQL builders ───────────────────────────────────────────
    # Each returns a SQL string (and, where parameterized, a params tuple). The
    # explorer router runs these and maps rows positionally, so every driver
    # must keep the documented column order.

    def list_databases_sql(self) -> Optional[str]:
        """SQL returning one column of database names, or None for
        single-database engines (the explorer then surfaces the configured
        database only)."""
        return None

    @abstractmethod
    def list_tables_sql(self) -> str:
        """Rows of (schema, name) for base tables in the connected database."""

    @abstractmethod
    def list_views_sql(self) -> str:
        """Rows of (schema, name) for views in the connected database."""

    def list_procedures_sql(self) -> Optional[str]:
        """Rows of (schema, name) for stored procedures, or None if N/A."""
        return None

    def list_functions_sql(self) -> Optional[str]:
        """Rows of (schema, name) for functions, or None if N/A."""
        return None

    @abstractmethod
    def schema_snapshot_tables_sql(self) -> str:
        """Rows of (schema, name, kind) where kind is 'BASE TABLE' or 'VIEW'."""

    @abstractmethod
    def schema_snapshot_columns_sql(self) -> str:
        """Rows of (schema, table, column, data_type) for every column."""

    @abstractmethod
    def columns_sql(self, schema: str, table: str) -> tuple[str, tuple]:
        """(sql, params) → rows of
        (name, data_type, char_max_len, is_nullable 'YES'/'NO',
         default, is_primary_key 0/1, ordinal_position)."""

    def indexes_sql(self, schema: str, table: str) -> Optional[tuple[str, tuple]]:
        """(sql, params) → rows of
        (index_name, type_desc, is_unique 0/1, is_primary_key 0/1, columns_csv),
        or None for engines without queryable indexes (Snowflake)."""
        return None
