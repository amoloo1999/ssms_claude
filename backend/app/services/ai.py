import re
from anthropic import Anthropic
from app.config import get_settings
from app.services.connection import execute_query, build_connection_string

# Stop-words we strip from "find data" prompts before keyword matching.
_FIND_STOPWORDS = {
    "where", "what", "which", "find", "the", "for", "and", "data", "table",
    "tables", "column", "columns", "live", "lives", "stored", "store", "store's",
    "can", "show", "tell", "info", "information", "about", "have", "has", "any",
    "with", "from", "this", "that", "give", "list", "all", "some", "are", "you",
    "please", "would", "should", "could", "look", "looking", "need", "want",
}

_settings = get_settings()
_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not _settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=_settings.anthropic_api_key)
    return _client


SYSTEM_PROMPT = """You are an expert T-SQL (Microsoft SQL Server) assistant embedded in a SQL editor.

Rules:
- Always target Microsoft SQL Server (T-SQL) syntax.
- Prefer SELECT queries unless the user clearly asks for a write operation. Warn before destructive statements.
- Use only tables and columns that are present in the schema context the user provides. If a needed object is missing, say so instead of guessing.
- When you return SQL, wrap it in a single ```sql code fence. Put any explanation outside the fence and keep it short.
"""


def _fetch_schema_context(connection_string: str, prompt_text: str, max_tables: int = 40) -> str:
    """Pull a compact schema snapshot for the active database.

    Tries to bias toward tables whose names appear in the prompt; otherwise
    returns the first ``max_tables`` user tables.
    """
    tables_res = execute_query(
        connection_string,
        """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """,
    )
    if tables_res.get("error"):
        return f"(schema unavailable: {tables_res['error']})"

    all_tables = [(r[0], r[1]) for r in tables_res["rows"]]
    if not all_tables:
        return "(no user tables found)"

    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", prompt_text or "")}
    scored = sorted(
        all_tables,
        key=lambda t: (0 if t[1].lower() in words or any(w in t[1].lower() for w in words) else 1, t[0], t[1]),
    )
    picked = scored[:max_tables]

    cols_res = execute_query(
        connection_string,
        """
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """,
    )
    cols_by_tbl: dict[tuple[str, str], list[str]] = {}
    if not cols_res.get("error"):
        for r in cols_res["rows"]:
            cols_by_tbl.setdefault((r[0], r[1]), []).append(f"{r[2]} {r[3]}")

    lines = []
    for schema, name in picked:
        cols = cols_by_tbl.get((schema, name), [])
        lines.append(f"{schema}.{name}({', '.join(cols)})")
    omitted = len(all_tables) - len(picked)
    if omitted > 0:
        lines.append(f"-- and {omitted} more tables not shown")
    return "\n".join(lines)


def _extract_sql(text: str) -> str | None:
    m = re.search(r"```(?:sql)?\s*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _call_claude(user_message: str) -> str:
    client = _get_client()
    msg = client.messages.create(
        model=_settings.anthropic_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()


def generate_sql(connection_string: str, database: str, prompt: str, current_sql: str | None) -> dict:
    schema_ctx = _fetch_schema_context(connection_string, prompt)
    user_msg = (
        f"Database: {database}\n\n"
        f"Schema (subset):\n{schema_ctx}\n\n"
    )
    if current_sql:
        user_msg += f"Current SQL in editor:\n```sql\n{current_sql}\n```\n\n"
    user_msg += f"Request: {prompt}"

    text = _call_claude(user_msg)
    return {"sql": _extract_sql(text), "explanation": text, "error": None}


def _extract_keywords(prompt: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", prompt or "")
    seen: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in _FIND_STOPWORDS:
            continue
        if lw not in seen:
            seen.append(lw)
    return seen[:10]


def find_data(
    host: str,
    port: int,
    username: str,
    password: str,
    prompt: str,
    max_databases: int = 25,
    max_matches: int = 200,
) -> dict:
    """Search every user database on a server for tables/columns relevant to the prompt.

    Returns an AIResponse-shaped dict with a plain-English answer plus a starter
    SELECT query the user can refine.
    """
    keywords = _extract_keywords(prompt)
    if not keywords:
        return {
            "sql": None,
            "explanation": "",
            "error": "Could not pull any keywords out of your question. Try naming the data you're looking for (e.g. 'occupancy', 'employees').",
        }

    master_conn = build_connection_string(host, port, username, password, "master")
    db_res = execute_query(
        master_conn,
        """
        SELECT name FROM sys.databases
        WHERE database_id > 4 AND state = 0
        ORDER BY name
        """,
    )
    if db_res.get("error"):
        return {"sql": None, "explanation": "", "error": f"Could not list databases: {db_res['error']}"}

    databases = [r[0] for r in db_res["rows"]][:max_databases]
    if not databases:
        return {"sql": None, "explanation": "", "error": "No user databases found on this server."}

    # Build a parameterized LIKE clause for table_name OR column_name matches.
    like_clauses = " OR ".join(
        ["LOWER(TABLE_NAME) LIKE ?" for _ in keywords]
        + ["LOWER(COLUMN_NAME) LIKE ?" for _ in keywords]
    )
    params = tuple([f"%{k}%" for k in keywords] * 2)

    matches: list[tuple[str, str, str, str, str]] = []  # (db, schema, table, column, type)
    errors: list[str] = []
    for db_name in databases:
        if len(matches) >= max_matches:
            break
        conn_str = build_connection_string(host, port, username, password, db_name)
        sql = f"""
            SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE {like_clauses}
            ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """
        res = execute_query(conn_str, sql, params)
        if res.get("error"):
            errors.append(f"{db_name}: {res['error']}")
            continue
        for row in res["rows"]:
            matches.append((db_name, row[0], row[1], row[2], row[3]))
            if len(matches) >= max_matches:
                break

    if not matches:
        detail = f" ({'; '.join(errors)})" if errors else ""
        return {
            "sql": None,
            "explanation": "",
            "error": f"No tables or columns matched keywords {keywords} across {len(databases)} databases.{detail}",
        }

    # Group matches by (db, schema, table) for a compact context.
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for db_name, schema, table, col, dtype in matches:
        grouped.setdefault((db_name, schema, table), []).append(f"{col} {dtype}")

    catalog_lines = []
    for (db_name, schema, table), cols in grouped.items():
        catalog_lines.append(f"[{db_name}].{schema}.{table} -> {', '.join(cols)}")
    catalog = "\n".join(catalog_lines)

    user_msg = (
        f"User question: {prompt}\n\n"
        f"I searched every user database on this SQL Server for tables and columns "
        f"matching the keywords {keywords}. Below is every match — each line is "
        f"`[database].schema.table -> matching columns`.\n\n"
        f"{catalog}\n\n"
        "Based on these matches, do two things:\n"
        "1. In one short paragraph, tell the user where this data most likely lives "
        "(name the database, schema, and table). If multiple candidates look plausible, "
        "list the top 2-3 and say how to tell them apart.\n"
        "2. Provide a starter `SELECT TOP 100` query against the best candidate, using "
        "fully-qualified `[database].[schema].[table]` naming so the user can run it from any database context.\n"
    )
    text = _call_claude(user_msg)
    return {"sql": _extract_sql(text), "explanation": text, "error": None}


def fix_sql(connection_string: str, database: str, sql: str, error: str) -> dict:
    schema_ctx = _fetch_schema_context(connection_string, sql)
    user_msg = (
        f"Database: {database}\n\n"
        f"Schema (subset):\n{schema_ctx}\n\n"
        f"This T-SQL query failed:\n```sql\n{sql}\n```\n\n"
        f"Error from SQL Server:\n{error}\n\n"
        f"Diagnose the problem and return a corrected query."
    )
    text = _call_claude(user_msg)
    return {"sql": _extract_sql(text), "explanation": text, "error": None}
