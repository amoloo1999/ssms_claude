"""Pure-function tests for the database-driver abstraction.

These exercise the engine-specific SQL shaping (quoting, placeholders,
pagination, value serialization, connection-string building, batch splitting)
without touching a real database — so they catch the highest-risk refactor bug
class (a leftover ``?`` against a ``%s`` engine, or wrong quote chars) before
deploy.

Runnable either with pytest or directly:  python tests/test_drivers.py
"""

import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.drivers import get_driver, supported_dialects


def test_registry_and_aliases():
    assert set(supported_dialects()) == {"mssql", "postgres", "mysql", "snowflake"}
    assert get_driver("mssql").dialect == "mssql"
    assert get_driver(None).dialect == "mssql"  # default
    assert get_driver("postgresql").dialect == "postgres"  # alias
    assert get_driver("pg").dialect == "postgres"
    assert get_driver("aurora-mysql").dialect == "mysql"
    # cached instance
    assert get_driver("mssql") is get_driver("mssql")
    try:
        get_driver("oracle")
        assert False, "expected ValueError for unsupported dialect"
    except ValueError:
        pass


def test_quote_ident():
    assert get_driver("mssql").quote_ident("Order") == "[Order]"
    assert get_driver("mssql").quote_ident("a]b") == "[a]]b]"  # bracket escaped
    assert get_driver("postgres").quote_ident("Order") == '"Order"'
    assert get_driver("postgres").quote_ident('a"b') == '"a""b"'  # quote escaped
    assert get_driver("mysql").quote_ident("Order") == "`Order`"
    assert get_driver("mysql").quote_ident("a`b") == "`a``b`"
    assert get_driver("snowflake").quote_ident("Order") == '"Order"'


def test_quote_qualified_skips_empty():
    d = get_driver("postgres")
    assert d.quote_qualified("gold", "fact_sales") == '"gold"."fact_sales"'
    assert d.quote_qualified("", "fact_sales") == '"fact_sales"'


def test_placeholder():
    assert get_driver("mssql").placeholder() == "?"
    assert get_driver("postgres").placeholder() == "%s"
    assert get_driver("mysql").placeholder() == "%s"
    assert get_driver("snowflake").placeholder() == "%s"


def test_paginate():
    mssql = get_driver("mssql").paginate("SELECT * FROM [dbo].[t]", 100, 200)
    assert "OFFSET 200 ROWS FETCH NEXT 100 ROWS ONLY" in mssql
    assert "ORDER BY (SELECT NULL)" in mssql  # T-SQL requires an ORDER BY

    mssql_sorted = get_driver("mssql").paginate("SELECT * FROM [dbo].[t]", 50, 0, "[name] ASC")
    assert "ORDER BY [name] ASC" in mssql_sorted

    pg = get_driver("postgres").paginate('SELECT * FROM "gold"."t"', 100, 200)
    assert pg.endswith("LIMIT 100 OFFSET 200")
    assert "FETCH NEXT" not in pg

    my = get_driver("mysql").paginate("SELECT * FROM `t`", 10, 5, "`name` DESC")
    assert "ORDER BY `name` DESC LIMIT 10 OFFSET 5" in my


def test_serialize_value():
    mssql = get_driver("mssql")
    assert mssql.serialize_value(b"\x00\x01") == "0001"
    assert mssql.serialize_value(None) is None
    assert mssql.serialize_value(5) == 5

    pg = get_driver("postgres")
    assert pg.serialize_value(memoryview(b"\xde\xad")) == "dead"
    assert pg.serialize_value({"a": 1}) == json.dumps({"a": 1})
    assert pg.serialize_value([1, 2]) == json.dumps([1, 2])
    assert pg.serialize_value(Decimal("1.50")) == "1.50"
    assert pg.serialize_value(True) is True


def test_split_batches():
    mssql = get_driver("mssql")
    assert mssql.split_batches("SELECT 1\nGO\nSELECT 2") == ["SELECT 1", "SELECT 2"]
    assert mssql.split_batches("SELECT 1") == ["SELECT 1"]
    # non-mssql never splits on GO
    assert get_driver("postgres").split_batches("SELECT 1\nGO\nSELECT 2") == ["SELECT 1\nGO\nSELECT 2"]


def test_default_schema_for():
    assert get_driver("mssql").default_schema_for("AnyDb") == "dbo"
    assert get_driver("postgres").default_schema_for("anydb") == "public"
    # MySQL: schema == the connected database
    assert get_driver("mysql").default_schema_for("reports") == "reports"
    assert get_driver("mysql").default_schema_for(None) == "mysql"


def test_capability_flags():
    assert get_driver("mssql").cross_database_supported is True
    assert get_driver("mssql").single_database is False
    for d in ("postgres", "mysql", "snowflake"):
        assert get_driver(d).cross_database_supported is False
        assert get_driver(d).single_database is True
    assert get_driver("mssql").supports_cancel is True
    assert get_driver("postgres").supports_cancel is True
    assert get_driver("mysql").supports_cancel is False
    assert get_driver("snowflake").supports_cancel is False


def test_build_connection_string_shapes():
    mssql = get_driver("mssql").build_connection_string("h", 1433, "u", "p", "db")
    assert "DRIVER={ODBC Driver 17 for SQL Server}" in mssql
    assert "SERVER=h,1433" in mssql
    assert "DATABASE=db" in mssql

    # MySQL / Snowflake encode connect kwargs as JSON (also the pool key).
    my = json.loads(get_driver("mysql").build_connection_string("h", 3306, "u", "p w", "rep"))
    assert my["host"] == "h" and my["user"] == "u" and my["password"] == "p w"
    assert my["database"] == "rep" and my["port"] == 3306

    sf = json.loads(get_driver("snowflake").build_connection_string("acct?warehouse=WH", 443, "u", "p", "ANALYTICS"))
    assert sf["account"] == "acct" and sf["warehouse"] == "WH"
    assert sf["database"] == "ANALYTICS"


def test_introspection_sql_contracts():
    # Single-database engines expose no list-databases SQL.
    assert get_driver("mssql").list_databases_sql() is not None
    for d in ("postgres", "mysql", "snowflake"):
        assert get_driver(d).list_databases_sql() is None

    # columns_sql returns (sql, params) with the engine's placeholder.
    for dialect, ph in (("mssql", "?"), ("postgres", "%s"), ("mysql", "%s"), ("snowflake", "%s")):
        sql, params = get_driver(dialect).columns_sql("s", "t")
        assert ph in sql
        assert isinstance(params, tuple) and "s" in params and "t" in params

    # Snowflake has no indexes; others do.
    assert get_driver("snowflake").indexes_sql("s", "t") is None
    for d in ("mssql", "postgres", "mysql"):
        spec = get_driver(d).indexes_sql("s", "t")
        assert spec is not None and isinstance(spec[0], str)


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
