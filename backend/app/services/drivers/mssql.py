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


def _num(value: Optional[str]) -> float:
    """SHOWPLAN attributes are strings and frequently absent."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _pct(value: Optional[str]) -> float:
    """Placeholder until the second pass computes the share of total cost."""
    return _num(value)

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
    # SQL Server is the first engine to get these; the other drivers inherit
    # the base class's False and the UI hides the feature there.
    supports_foreign_keys = True
    supports_execution_plan = True
    supports_session_monitor = True

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

    # ── foreign keys ─────────────────────────────────────────────────────────

    def foreign_keys_sql(self, schema: str, table: str) -> Optional[tuple[str, tuple]]:
        """Both directions in one query.

        The UNION's second arm finds constraints pointing AT this table, which
        is what makes the diagram show a table's whole neighbourhood rather than
        only what it references.
        """
        sql = """
        SELECT
            fk.name              AS constraint_name,
            ps.name              AS parent_schema,
            pt.name              AS parent_table,
            pc.name              AS parent_column,
            rs.name              AS referenced_schema,
            rt.name              AS referenced_table,
            rc.name              AS referenced_column
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        JOIN sys.tables  pt ON fk.parent_object_id = pt.object_id
        JOIN sys.schemas ps ON pt.schema_id = ps.schema_id
        JOIN sys.columns pc ON pc.object_id = pt.object_id AND pc.column_id = fkc.parent_column_id
        JOIN sys.tables  rt ON fk.referenced_object_id = rt.object_id
        JOIN sys.schemas rs ON rt.schema_id = rs.schema_id
        JOIN sys.columns rc ON rc.object_id = rt.object_id AND rc.column_id = fkc.referenced_column_id
        WHERE (ps.name = ? AND pt.name = ?)
           OR (rs.name = ? AND rt.name = ?)
        ORDER BY fk.name
        """
        return sql, (schema, table, schema, table)

    # ── session / lock monitor ───────────────────────────────────────────────

    def sessions_sql(self) -> Optional[str]:
        """One row per user session, with the statement currently in flight.

        Left joins to dm_exec_requests because most sessions are idle and have
        no request; an inner join would show only the busy ones and hide the
        idle-with-open-transaction case, which is exactly the one worth seeing.
        """
        return """
        SELECT
            s.session_id,
            s.login_name,
            s.host_name,
            s.program_name,
            DB_NAME(COALESCE(r.database_id, s.database_id))  AS database_name,
            COALESCE(r.status, s.status)                     AS status,
            COALESCE(r.blocking_session_id, 0)               AS blocked_by,
            COALESCE(r.wait_type, '')                        AS wait_type,
            COALESCE(r.total_elapsed_time, 0)                AS elapsed_ms,
            s.open_transaction_count                         AS open_transactions,
            SUBSTRING(
                t.text,
                (COALESCE(r.statement_start_offset, 0) / 2) + 1,
                CASE COALESCE(r.statement_end_offset, -1)
                    WHEN -1 THEN DATALENGTH(t.text)
                    ELSE (r.statement_end_offset - r.statement_start_offset) / 2 + 1
                END
            )                                                AS current_statement
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
        OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
        WHERE s.is_user_process = 1
        ORDER BY
            CASE WHEN COALESCE(r.blocking_session_id, 0) <> 0 THEN 0 ELSE 1 END,
            r.total_elapsed_time DESC
        """

    def kill_session_sql(self, session_id: int) -> Optional[str]:
        # KILL takes no parameters, so the id is coerced to int by the caller
        # and formatted here — that coercion is what keeps this safe.
        return f"KILL {int(session_id)}"

    # ── execution plan ───────────────────────────────────────────────────────

    def explain_statements(self, sql: str) -> Optional[tuple[str, str, str]]:
        """Estimated plan via SHOWPLAN_XML.

        SHOWPLAN_XML makes the server RETURN the plan instead of executing the
        statement, which is what makes this safe to run against production: the
        query never touches a row. The SET statements must be their own batches.
        """
        return ("SET SHOWPLAN_XML ON", sql, "SET SHOWPLAN_XML OFF")

    def parse_plan(self, raw: str) -> Optional[dict]:
        """Flatten SHOWPLAN_XML into the operator tree the UI draws."""
        if not raw:
            return None
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(raw)
        except Exception:
            return None

        # SHOWPLAN_XML is namespaced; strip it so the walk stays readable.
        def tag(el) -> str:
            return el.tag.split("}")[-1]

        nodes: list[dict] = []
        warnings: list[str] = []
        missing: list[dict] = []

        def walk(el, depth: int) -> None:
            for child in el:
                name = tag(child)
                if name == "RelOp":
                    a = child.attrib
                    nodes.append(
                        {
                            "depth": depth,
                            "operator": a.get("PhysicalOp", "?"),
                            "detail": a.get("LogicalOp", ""),
                            # The operator's OWN cost, which is what SSMS shows
                            # as "Cost: N%". EstimatedTotalSubtreeCost is
                            # cumulative, so using it would make the root
                            # operator 100% on every plan and the highlight
                            # would always land on the same node.
                            "own_cost": _num(a.get("EstimateIO")) + _num(a.get("EstimateCPU")),
                            "rows": _num(a.get("EstimateRows")),
                        }
                    )
                    walk(child, depth + 1)
                    continue
                if name == "Warnings":
                    for w in child:
                        wa = w.attrib
                        if wa.get("ConvertIssue"):
                            warnings.append(f"Implicit conversion: {wa['ConvertIssue']}")
                        elif wa.get("NoJoinPredicate"):
                            warnings.append("No join predicate")
                        else:
                            warnings.append(tag(w))
                elif name == "MissingIndexGroup":
                    for mi in child.iter():
                        if tag(mi) == "MissingIndex":
                            missing.append(
                                {
                                    "database": mi.attrib.get("Database", ""),
                                    "schema": mi.attrib.get("Schema", ""),
                                    "table": mi.attrib.get("Table", ""),
                                    "impact": _num(child.attrib.get("Impact")),
                                    "columns": [
                                        c.attrib.get("Name", "")
                                        for c in mi.iter()
                                        if tag(c) == "Column"
                                    ],
                                }
                            )
                walk(child, depth)

        walk(root, 0)
        if not nodes:
            return None

        # Each operator's share of the plan's total cost — the number that
        # actually points at where the time goes.
        total = sum(n["own_cost"] for n in nodes) or 1
        for n in nodes:
            n["cost_pct"] = round((n["own_cost"] / total) * 100, 1)
            n.pop("own_cost", None)

        return {"nodes": nodes, "warnings": warnings, "missing_indexes": missing}
