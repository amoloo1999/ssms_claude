from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
from app.config import is_revman
from app.models import ServerConnection
from app.services.connection import get_connection_string, execute_query_async
from app.services.drivers import get_driver
from app.services.permissions import (
    can_access_server,
    get_user_grants,
    filter_visible_tables,
    grant_covers,
)

router = APIRouter(prefix="/api/explorer", tags=["explorer"])


async def _resolve_server(
    db: AsyncSession, user: dict, server_id: int
) -> ServerConnection:
    server = (
        await db.execute(select(ServerConnection).where(ServerConnection.id == server_id))
    ).scalar_one_or_none()
    if not server or not can_access_server(user, server):
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.get("/servers/{server_id}/databases")
async def list_databases(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = await _resolve_server(db, user, server_id)
    driver = get_driver(server.dialect)

    sql = driver.list_databases_sql()
    if sql is None:
        # Single-database engine (Postgres/MySQL/Snowflake): the only "database"
        # is the one the server is configured to connect to.
        dbs = [server.database] if server.database else []
    else:
        conn_str = await get_connection_string(db, server_id)
        result = await execute_query_async(conn_str, sql)
        if result["error"]:
            return {"error": result["error"], "databases": []}
        dbs = [row[0] for row in result["rows"]]

    if not is_revman(user.get("email", "")):
        # Show only databases the user has any grant in. Wildcard-aware:
        # a database-wide or server-wide grant matches via grant_covers.
        grants = await get_user_grants(db, user["email"])
        dbs = [d for d in dbs if grant_covers(grants, server_id, d)]
    return {"databases": dbs}


@router.get("/servers/{server_id}/databases/{database}/tables")
async def list_tables(
    server_id: int,
    database: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = await _resolve_server(db, user, server_id)
    driver = get_driver(server.dialect)
    conn_str = await get_connection_string(db, server_id, database)
    result = await execute_query_async(conn_str, driver.list_tables_sql())
    if result["error"]:
        return {"error": result["error"], "tables": []}
    tables = [{"schema": row[0], "name": row[1]} for row in result["rows"]]
    grants = await get_user_grants(db, user["email"])
    return {
        "tables": filter_visible_tables(user, grants, server_id, database, tables)
    }


@router.get("/servers/{server_id}/databases/{database}/schema-snapshot")
async def schema_snapshot(
    server_id: int,
    database: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Compact tables + columns dump used by the editor's autocomplete provider.

    For RevMan: full snapshot. For non-RevMan: only their granted tables (so
    autocomplete doesn't leak schema for tables they can't query)."""
    server = await _resolve_server(db, user, server_id)
    driver = get_driver(server.dialect)
    conn_str = await get_connection_string(db, server_id, database)
    tbl_res = await execute_query_async(conn_str, driver.schema_snapshot_tables_sql())
    if tbl_res["error"]:
        return {"error": tbl_res["error"], "tables": [], "columns": []}
    col_res = await execute_query_async(conn_str, driver.schema_snapshot_columns_sql())

    raw_tables = [
        {
            "schema": r[0],
            "name": r[1],
            "kind": "view" if r[2] == "VIEW" else "table",
        }
        for r in tbl_res["rows"]
    ]
    raw_cols = [
        {"schema": r[0], "table": r[1], "name": r[2], "type": r[3]}
        for r in (col_res["rows"] if not col_res["error"] else [])
    ]

    if not is_revman(user.get("email", "")):
        grants = await get_user_grants(db, user["email"])
        allowed = {
            (sch, tbl)
            for sid, dbn, sch, tbl in grants
            if sid == server_id and dbn == database.lower()
        }
        raw_tables = [t for t in raw_tables if (t["schema"].lower(), t["name"].lower()) in allowed]
        raw_cols = [c for c in raw_cols if (c["schema"].lower(), c["table"].lower()) in allowed]

    return {"tables": raw_tables, "columns": raw_cols}


@router.get("/servers/{server_id}/databases/{database}/views")
async def list_views(
    server_id: int,
    database: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = await _resolve_server(db, user, server_id)
    driver = get_driver(server.dialect)
    conn_str = await get_connection_string(db, server_id, database)
    result = await execute_query_async(conn_str, driver.list_views_sql())
    if result["error"]:
        return {"error": result["error"], "views": []}
    views = [{"schema": row[0], "name": row[1]} for row in result["rows"]]
    grants = await get_user_grants(db, user["email"])
    # Same TablePermission table covers views (Schema.Name pair).
    return {"views": filter_visible_tables(user, grants, server_id, database, views)}


@router.get("/servers/{server_id}/databases/{database}/procedures")
async def list_procedures(
    server_id: int,
    database: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = await _resolve_server(db, user, server_id)
    # Non-RevMan can't EXEC anyway — hide procs entirely.
    if not is_revman(user.get("email", "")):
        return {"procedures": []}
    driver = get_driver(server.dialect)
    sql = driver.list_procedures_sql()
    if sql is None:
        return {"procedures": []}
    conn_str = await get_connection_string(db, server_id, database)
    result = await execute_query_async(conn_str, sql)
    if result["error"]:
        return {"error": result["error"], "procedures": []}
    return {
        "procedures": [
            {"schema": row[0], "name": row[1]} for row in result["rows"]
        ]
    }


@router.get("/servers/{server_id}/databases/{database}/functions")
async def list_functions(
    server_id: int,
    database: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = await _resolve_server(db, user, server_id)
    if not is_revman(user.get("email", "")):
        return {"functions": []}
    driver = get_driver(server.dialect)
    sql = driver.list_functions_sql()
    if sql is None:
        return {"functions": []}
    conn_str = await get_connection_string(db, server_id, database)
    result = await execute_query_async(conn_str, sql)
    if result["error"]:
        return {"error": result["error"], "functions": []}
    return {
        "functions": [
            {"schema": row[0], "name": row[1]} for row in result["rows"]
        ]
    }


@router.get("/servers/{server_id}/databases/{database}/tables/{schema_name}.{table_name}/columns")
async def get_table_columns(
    server_id: int,
    database: str,
    schema_name: str,
    table_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = await _resolve_server(db, user, server_id)
    if not is_revman(user.get("email", "")):
        grants = await get_user_grants(db, user["email"])
        if not grant_covers(grants, server_id, database, schema_name, table_name):
            raise HTTPException(status_code=403, detail="No access to this table")
    driver = get_driver(server.dialect)
    conn_str = await get_connection_string(db, server_id, database)
    sql, params = driver.columns_sql(schema_name, table_name)
    result = await execute_query_async(conn_str, sql, params)
    if result["error"]:
        return {"error": result["error"], "columns": []}
    return {
        "columns": [
            {
                "name": row[0],
                "data_type": row[1],
                "max_length": row[2],
                "is_nullable": row[3] == "YES",
                "default_value": row[4],
                "is_primary_key": bool(row[5]),
                "ordinal_position": row[6],
            }
            for row in result["rows"]
        ]
    }


@router.get("/servers/{server_id}/databases/{database}/tables/{schema_name}.{table_name}/indexes")
async def get_table_indexes(
    server_id: int,
    database: str,
    schema_name: str,
    table_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    server = await _resolve_server(db, user, server_id)
    if not is_revman(user.get("email", "")):
        grants = await get_user_grants(db, user["email"])
        if not grant_covers(grants, server_id, database, schema_name, table_name):
            raise HTTPException(status_code=403, detail="No access to this table")
    driver = get_driver(server.dialect)
    spec = driver.indexes_sql(schema_name, table_name)
    if spec is None:
        # Engine without queryable indexes (e.g. Snowflake).
        return {"indexes": []}
    conn_str = await get_connection_string(db, server_id, database)
    sql, params = spec
    result = await execute_query_async(conn_str, sql, params)
    if result["error"]:
        return {"error": result["error"], "indexes": []}
    return {
        "indexes": [
            {
                "name": row[0],
                "type": row[1],
                "is_unique": bool(row[2]),
                "is_primary_key": bool(row[3]),
                "columns": row[4],
            }
            for row in result["rows"]
        ]
    }
