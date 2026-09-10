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

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

# Statements that reconfigure the session rather than just reading or writing
# rows. Anchored to the start of a statement so the ``SET`` of an
# ``UPDATE ... SET`` clause on one line is not mistaken for a session option.
# Kept here, self-contained, so the driver layer stays free of app imports --
# ``services.permissions`` has a similar strip for a different purpose.
_SESSION_STATE_RE = re.compile(r"(?:^|;)\s*(USE|SET)\b", re.IGNORECASE | re.MULTILINE)


def _strip_sql_literals(sql: str) -> str:
    """Blank out string literals and comments so keywords inside them don't match."""
    cleaned = re.sub(r"'(?:''|[^'])*'", "''", sql)
    cleaned = re.sub(r"--[^\n]*", "", cleaned)
    return re.sub(r"/\*[\s\S]*?\*/", "", cleaned)


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

    # ── optional capabilities ────────────────────────────────────────────────
    #
    # Features that are genuinely engine-specific (execution plans, foreign-key
    # introspection) are declared here rather than assumed. A driver opts in by
    # setting the flag AND implementing the matching method; everything else
    # reports False and the UI hides the feature instead of showing a control
    # that errors.
    #
    # This is what keeps a new capability from costing four implementations
    # before it can ship: MSSQL covers PROD-MAIN and REPORTING-GP, and the other
    # engines follow when someone actually needs them there.
    supports_foreign_keys: bool = False
    supports_execution_plan: bool = False
    supports_session_monitor: bool = False

    def supports(self, capability: str) -> bool:
        """Whether this driver implements an optional capability.

        Unknown capability names return False rather than raising: a caller
        asking about something no driver has yet should get 'no', not a crash.
        """
        return bool(getattr(self, f"supports_{capability}", False))

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

    # Client-side batch separator, if the engine has one. Only T-SQL does.
    batch_separator: Optional[str] = None

    def prepare_cursor(self, cursor) -> None:
        """Run any per-cursor preamble before the user's batches. No-op by
        default; SQL Server uses it for ``SET NOCOUNT ON``."""
        return None

    def requires_commit(self) -> bool:
        """Whether a successful execution should ``commit()`` to end the
        transaction before the connection returns to the pool."""
        return True

    def alters_session_state(self, sql: str) -> bool:
        """Whether running this SQL leaves state behind on the connection.

        A pooled connection is shared by every user of that server, but the
        pool key is only (dialect, connection string) -- it says nothing about
        what the session has been *told* to do. ``USE Sites`` or
        ``SET ROWCOUNT 10`` therefore rides along on the pooled connection and
        silently changes the NEXT person's query: unqualified names resolve in
        the wrong database, or their result set is quietly truncated. Both are
        legal statements in SSMS and neither is a write, so nothing else in the
        stack stops them.

        Rather than try to enumerate and undo every session option, the caller
        simply declines to pool a connection this returns True for. The cost of
        a false positive is one reconnect, so the match is deliberately
        conservative -- a multi-line ``UPDATE`` with ``SET`` on its own line
        trips it too, and that is fine.
        """
        cleaned = _strip_sql_literals(sql or "")
        return bool(_SESSION_STATE_RE.search(cleaned))

    # ── SQL shaping ──────────────────────────────────────────────────────────
    def split_batches(self, sql: str) -> list[str]:
        """Split a script into independently-executed batches. Only SQL Server
        has a client-side batch separator (``GO``); everything else runs as a
        single batch."""
        return [sql]

    def explain_script(self, sql: str) -> Optional[str]:
        """The whole plan request as ONE script, to run on ONE connection.

        ``explain_statements`` returns the preamble/statement/epilogue as
        separate batches because the engine requires that. They are joined here
        rather than executed as three separate calls, because each call checks
        a connection out of the shared pool: nothing guarantees the middle
        statement lands on the same session that was told to return a plan. If
        it does not, the "estimated plan" silently EXECUTES the user's query
        against the live database instead of describing it.

        Sent as one script, the preamble, statement and epilogue share a cursor,
        and the connection is retired afterwards (the preamble is a ``SET``, so
        ``alters_session_state`` is True) -- which also means a statement that
        errors part-way through can never leave SHOWPLAN on for the next user.
        """
        stmts = self.explain_statements(sql)
        if stmts is None:
            return None
        sep = self.batch_separator
        joiner = f"\n{sep}\n" if sep else "\n"
        return joiner.join(stmts)

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

    # ── optional: foreign keys (supports_foreign_keys) ───────────────────────

    def foreign_keys_sql(self, schema: str, table: str) -> Optional[tuple[str, tuple]]:
        """(sql, params) → rows of
        (constraint_name,
         parent_schema, parent_table, parent_column,
         referenced_schema, referenced_table, referenced_column)

        Both directions: constraints where the given table is the parent
        (outgoing) and where it is the referenced table (incoming). The diagram
        needs both to draw a table's neighbourhood.
        """
        return None

    # ── optional: execution plan (supports_execution_plan) ───────────────────

    def explain_statements(self, sql: str) -> Optional[tuple[str, str, str]]:
        """(preamble, statement, epilogue) to capture a plan without running the
        query for real, or None when unsupported.

        Returned as three separate statements because SQL Server's SHOWPLAN
        settings must be their own batch — they cannot share one with the
        statement they apply to.
        """
        return None

    # ── optional: session / lock monitor (supports_session_monitor) ──────────

    def sessions_sql(self) -> Optional[str]:
        """SQL returning one row per session:
        (session_id, login_name, host_name, program_name, database_name,
         status, blocked_by, wait_type, elapsed_ms, open_transactions,
         current_statement)

        ``blocked_by`` is 0/NULL when the session is not blocked.
        """
        return None

    def kill_session_sql(self, session_id: int) -> Optional[str]:
        """Statement that terminates a session. Returns None when unsupported.

        Deliberately takes an int rather than a string — this value is
        interpolated into SQL, and the caller coercing it to an integer is what
        keeps it from being an injection point.
        """
        return None

    def parse_plan(self, raw: str) -> Optional[dict]:
        """Turn whatever ``explain_statements`` produced into
        ``{"nodes": [{depth, operator, detail, cost_pct, rows, ...}],
           "warnings": [...], "missing_indexes": [...]}``.
        """
        return None
