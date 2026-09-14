"""Tests for the SQL-safety layer that gates non-RevMan executions.

Security-critical: a miss here either lets a view-only user write (false
negative) or blocks legitimate reads (false positive). Covers the multi-dialect
hardening — new write verbs (CALL/COPY/LOAD/REPLACE INTO) and identifier
extraction for [bracket]/"double"/`backtick` quoting.

Runnable with pytest or directly:  python tests/test_permissions.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.permissions import is_select_only, extract_referenced_tables
from app.services.drivers import get_driver

MSSQL = get_driver("mssql")


def test_blocks_classic_writes():
    for sql in ("INSERT INTO t VALUES (1)", "UPDATE t SET x=1", "DELETE FROM t",
                "DROP TABLE t", "EXEC sp_who", "SELECT * INTO t2 FROM t"):
        ok, _ = is_select_only(sql)
        assert ok is False, sql


def test_blocks_bare_procedure_call():
    # The hole: a stored proc runs without EXEC when it is the first statement
    # of a batch, so a verb denylist never sees a write. The shared login is a
    # sysadmin, so this must be refused.
    for sql in (
        "sp_executesql N'DELETE FROM dbo.Units'",
        "sp_rename 'dbo.Units', 'Units_old'",
        "sp_who",
        "xp_cmdshell 'dir'",
    ):
        ok, reason = is_select_only(sql)
        assert ok is False, sql
        assert "SELECT" in (reason or "")


def test_bare_proc_hidden_after_go_is_blocked():
    # First batch is a clean read; the write hides in the SECOND batch. Only a
    # driver-aware, per-batch check catches this.
    sql = "SELECT 1\nGO\nsp_executesql N'DROP TABLE dbo.x'"
    # Without the driver the two batches are seen as one that opens with SELECT,
    # and the DROP is inside a string literal — so it passes. This is exactly why
    # the security gate MUST pass the driver; with it, the bare proc is caught.
    assert is_select_only(sql)[0] is True
    assert is_select_only(sql, MSSQL)[0] is False


def test_blocks_server_control_and_exfiltration():
    for sql in (
        "DBCC FREEPROCCACHE",
        "KILL 53",
        "SHUTDOWN",
        "WAITFOR DELAY '00:00:10'",
        "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1')",
    ):
        assert is_select_only(sql)[0] is False, sql


def test_declare_prefixed_batch_is_refused():
    # DECLARE could precede anything, so a batch that opens with it is not a
    # read for a view-only user (they can wrap it in a SELECT if needed).
    assert is_select_only("DECLARE @x INT = 1; SELECT @x")[0] is False


def test_blocks_new_dialect_writes():
    for sql in ("CALL my_proc()", "COPY t FROM '/tmp/x.csv'",
                "LOAD DATA INFILE 'x' INTO TABLE t", "REPLACE INTO t VALUES (1)"):
        ok, _ = is_select_only(sql)
        assert ok is False, sql


def test_allows_legitimate_selects():
    # REPLACE() the string function must NOT be mistaken for REPLACE INTO.
    ok, _ = is_select_only("SELECT REPLACE(name, 'a', 'b') FROM gold.dim_site")
    assert ok is True
    # A column/identifier containing a verb substring is fine.
    ok, _ = is_select_only("SELECT call_id, copy_count FROM events")
    assert ok is True
    ok, _ = is_select_only("SELECT * FROM gold.fact_sales WHERE qty > 0")
    assert ok is True
    # A leading CTE is a read.
    ok, _ = is_select_only("WITH c AS (SELECT 1 AS x) SELECT * FROM c")
    assert ok is True
    # A parenthesised / UNION read.
    ok, _ = is_select_only("(SELECT 1) UNION (SELECT 2)")
    assert ok is True
    # Leading whitespace / comment before the SELECT.
    ok, _ = is_select_only("  -- a comment\n  SELECT 1")
    assert ok is True
    # Multi-batch, all reads.
    ok, _ = is_select_only("SELECT 1\nGO\nWITH c AS (SELECT 2 AS y) SELECT * FROM c", MSSQL)
    assert ok is True


def test_extract_default_schema_per_dialect():
    # Unqualified table picks up the engine's default schema.
    assert extract_referenced_tables("SELECT * FROM sales", "db", "dbo") == [("db", "dbo", "sales")]
    assert extract_referenced_tables("SELECT * FROM sales", "rep", "public") == [("rep", "public", "sales")]


def test_extract_handles_quote_styles():
    # Postgres double quotes
    refs = extract_referenced_tables('SELECT * FROM "gold"."fact_sales"', "rep", "public")
    assert refs == [("rep", "gold", "fact_sales")]
    # MySQL backticks
    refs = extract_referenced_tables("SELECT * FROM `gold`.`fact_sales`", "rep", "rep")
    assert refs == [("rep", "gold", "fact_sales")]
    # SQL Server brackets, three-part
    refs = extract_referenced_tables("SELECT * FROM [Sites].[dbo].[Sites]", "master", "dbo")
    assert refs == [("Sites", "dbo", "Sites")]


def test_cte_not_treated_as_table():
    sql = "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte JOIN gold.dim_site d ON d.x = cte.x"
    refs = extract_referenced_tables(sql, "rep", "public")
    # cte excluded; only the real table remains
    assert ("rep", "public", "cte") not in refs
    assert ("rep", "gold", "dim_site") in refs


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
