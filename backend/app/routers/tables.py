from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from app.database import get_db
from app.auth import require_auth
from app.models import TableEditRequest
from app.services.connection import get_connection_string, execute_query

router = APIRouter(prefix="/api/tables", tags=["tables"])


@router.get("/servers/{server_id}/databases/{database}/{schema_name}.{table_name}/data")
async def get_table_data(
    server_id: int,
    database: str,
    schema_name: str,
    table_name: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    sort_column: str = Query(None),
    sort_direction: str = Query("ASC", regex="^(ASC|DESC)$"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, server_id, database)

    # Get total count
    count_result = execute_query(
        conn_str,
        f"SELECT COUNT(*) FROM [{schema_name}].[{table_name}]",
    )
    total_rows = count_result["rows"][0][0] if count_result["rows"] else 0

    # Build query with pagination
    offset = (page - 1) * page_size
    order_by = f"[{sort_column}] {sort_direction}" if sort_column else "(SELECT NULL)"

    sql = f"""
        SELECT *
        FROM [{schema_name}].[{table_name}]
        ORDER BY {order_by}
        OFFSET {offset} ROWS
        FETCH NEXT {page_size} ROWS ONLY
    """

    result = execute_query(conn_str, sql)

    return {
        **result,
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows,
        "total_pages": (total_rows + page_size - 1) // page_size if total_rows > 0 else 0,
    }


@router.put("/edit")
async def edit_cell(
    edit: TableEditRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, edit.server_id, edit.database)

    # Build WHERE clause from primary keys
    where_parts = []
    params = [edit.new_value]
    for col, val in zip(edit.primary_key_columns, edit.primary_key_values):
        where_parts.append(f"[{col}] = ?")
        params.append(val)

    where_clause = " AND ".join(where_parts)

    sql = f"""
        UPDATE [{edit.schema_name}].[{edit.table}]
        SET [{edit.column}] = ?
        WHERE {where_clause}
    """

    result = execute_query(conn_str, sql, tuple(params))

    if result["error"]:
        return {"success": False, "error": result["error"]}
    return {"success": True, "rows_affected": result["row_count"]}


@router.post("/servers/{server_id}/databases/{database}/{schema_name}.{table_name}/row")
async def insert_row(
    server_id: int,
    database: str,
    schema_name: str,
    table_name: str,
    row_data: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, server_id, database)

    columns = list(row_data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join([f"[{c}]" for c in columns])
    values = tuple(row_data.values())

    sql = f"INSERT INTO [{schema_name}].[{table_name}] ({col_names}) VALUES ({placeholders})"
    result = execute_query(conn_str, sql, values)

    if result["error"]:
        return {"success": False, "error": result["error"]}
    return {"success": True}


@router.delete("/servers/{server_id}/databases/{database}/{schema_name}.{table_name}/row")
async def delete_row(
    server_id: int,
    database: str,
    schema_name: str,
    table_name: str,
    primary_key_columns: list[str] = Query(...),
    primary_key_values: list[str] = Query(...),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, server_id, database)

    where_parts = []
    params = []
    for col, val in zip(primary_key_columns, primary_key_values):
        where_parts.append(f"[{col}] = ?")
        params.append(val)

    where_clause = " AND ".join(where_parts)
    sql = f"DELETE FROM [{schema_name}].[{table_name}] WHERE {where_clause}"
    result = execute_query(conn_str, sql, tuple(params))

    if result["error"]:
        return {"success": False, "error": result["error"]}
    return {"success": True, "rows_affected": result["row_count"]}
