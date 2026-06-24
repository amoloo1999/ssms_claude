"""End-to-end test of the engine-agnostic connection facade.

Uses Python's built-in ``sqlite3`` (a real DBAPI engine) behind a throwaway
driver registered into the driver registry, so ``execute_query`` runs its actual
path — pool, cursor, result-set drain, per-cell serialization, commit, and the
cancel registry — without needing a live SQL Server/Postgres.

Runnable with pytest or directly:  python tests/test_connection_facade.py
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.drivers.base import DatabaseDriver
from app.services.drivers import registry
from app.services.connection import execute_query, cancel_query


class _SqliteDriver(DatabaseDriver):
    dialect = "sqlitetest"
    paramstyle = "qmark"
    default_schema = "main"

    def build_connection_string(self, host, port, username, password, database=None):
        return database  # the file path is the "connection string"

    def connect(self, conn_str):
        return sqlite3.connect(conn_str)

    def placeholder(self, index: int = 0) -> str:
        return "?"

    # Minimal introspection stubs (unused by these tests).
    def list_tables_sql(self):
        return "SELECT '' AS s, name FROM sqlite_master WHERE type='table'"

    def list_views_sql(self):
        return "SELECT '' AS s, name FROM sqlite_master WHERE type='view'"

    def schema_snapshot_tables_sql(self):
        return "SELECT '' AS s, name, 'BASE TABLE' FROM sqlite_master WHERE type='table'"

    def schema_snapshot_columns_sql(self):
        return "SELECT '' AS s, '' AS t, '' AS c, '' AS d WHERE 1=0"

    def columns_sql(self, schema, table):
        return "SELECT 1 WHERE 1=0", ()


# Register the test driver and a ConnHandle pointing at a temp DB file.
registry._FACTORIES["sqlitetest"] = lambda: _SqliteDriver()
registry._instances.pop("sqlitetest", None)
from app.services.drivers.base import ConnHandle  # noqa: E402

_DB_PATH = os.path.join(tempfile.gettempdir(), "ssms_facade_test.sqlite")
_HANDLE = ConnHandle("sqlitetest", _DB_PATH)


def _reset():
    # Reset via DROP/CREATE rather than deleting the file — the pool keeps the
    # sqlite connection open, which would lock the file on Windows.
    execute_query(_HANDLE, "DROP TABLE IF EXISTS t")
    execute_query(_HANDLE, "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, blob BLOB)")


def test_select_returns_rows_and_shape():
    _reset()
    execute_query(_HANDLE, "INSERT INTO t (id, name) VALUES (?, ?)", (1, "alice"))
    res = execute_query(_HANDLE, "SELECT id, name FROM t ORDER BY id")
    assert res["error"] is None
    assert res["columns"] == ["id", "name"]
    assert res["rows"] == [[1, "alice"]]
    assert res["row_count"] == 1
    assert len(res["result_sets"]) == 1


def test_insert_reports_affected_rows():
    _reset()
    res = execute_query(_HANDLE, "INSERT INTO t (id, name) VALUES (?, ?)", (2, "bob"))
    assert res["error"] is None
    assert res["row_count"] == 1  # affected rows, no result set
    assert res["result_sets"] == []
    # commit actually persisted it
    check = execute_query(_HANDLE, "SELECT COUNT(*) FROM t")
    assert check["rows"][0][0] == 1


def test_blob_serialized_to_hex():
    _reset()
    execute_query(_HANDLE, "INSERT INTO t (id, blob) VALUES (?, ?)", (3, b"\xde\xad"))
    res = execute_query(_HANDLE, "SELECT blob FROM t WHERE id=3")
    assert res["rows"][0][0] == "dead"


def test_error_is_returned_not_raised():
    _reset()
    res = execute_query(_HANDLE, "SELECT * FROM does_not_exist")
    assert res["error"] is not None
    assert res["rows"] == []


def test_cancel_unknown_id_is_false():
    assert cancel_query("nope-not-running") is False


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
