import re
from anthropic import Anthropic
from app.config import get_settings
from app.services.connection import execute_query

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
