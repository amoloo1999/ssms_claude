from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
from app.config import is_revman
from app.models import ServerConnection
from app.services.connection import get_connection_string, execute_query
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
    conn_str = await get_connection_string(db, server_id)
    result = execute_query(
        conn_str,
        "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' ORDER BY name",
    )
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
    await _resolve_server(db, user, server_id)
    conn_str = await get_connection_string(db, server_id, database)
    result = execute_query(
        conn_str,
        """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """,
    )
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
    await _resolve_server(db, user, server_id)
    conn_str = await get_connection_string(db, server_id, database)
    tbl_res = execute_query(
        conn_str,
        """
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW')
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """,
    )
    if tbl_res["error"]:
        return {"error": tbl_res["error"], "tables": [], "columns": []}
    col_res = execute_query(
        conn_str,
        """
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """,
    )

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
    await _resolve_server(db, user, server_id)
    conn_str = await get_connection_string(db, server_id, database)
    result = execute_query(
        conn_str,
        """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.VIEWS
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """,
    )
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
    await _resolve_server(db, user, server_id)
    # Non-RevMan can't EXEC anyway — hide procs entirely.
    if not is_revman(user.get("email", "")):
        return {"procedures": []}
    conn_str = await get_connection_string(db, server_id, database)
    result = execute_query(
        conn_str,
        """
        SELECT ROUTINE_SCHEMA, ROUTINE_NAME
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE = 'PROCEDURE'
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """,
    )
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
    await _resolve_server(db, user, server_id)
    if not is_revman(user.get("email", "")):
        return {"functions": []}
    conn_str = await get_connection_string(db, server_id, database)
    result = execute_query(
        conn_str,
        """
        SELECT ROUTINE_SCHEMA, ROUTINE_NAME
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE = 'FUNCTION'
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
        """,
    )
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
    await _resolve_server(db, user, server_id)
    if not is_revman(user.get("email", "")):
        grants = await get_user_grants(db, user["email"])
        if not grant_covers(grants, server_id, database, schema_name, table_name):
            raise HTTPException(status_code=403, detail="No access to this table")
    conn_str = await get_connection_string(db, server_id, database)
    result = execute_query(
        conn_str,
        """
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
        """,
        (schema_name, table_name, schema_name, table_name),
    )
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
    await _resolve_server(db, user, server_id)
    if not is_revman(user.get("email", "")):
        grants = await get_user_grants(db, user["email"])
        if not grant_covers(grants, server_id, database, schema_name, table_name):
            raise HTTPException(status_code=403, detail="No access to this table")
    conn_str = await get_connection_string(db, server_id, database)
    result = execute_query(
        conn_str,
        """
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
        """,
        (schema_name, table_name),
    )
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
