import re
from anthropic import Anthropic
from app.config import get_settings
from app.services import lineage
from app.services.connection import execute_query, build_connection_string
from app.services.drivers import ConnHandle, DatabaseDriver, get_driver

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


# Short dialect name used in the prompt so Claude targets the right SQL grammar.
_SYNTAX_HINT = {
    "mssql": "T-SQL / Microsoft SQL Server",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "snowflake": "Snowflake SQL",
}


def _system_prompt(driver: DatabaseDriver) -> str:
    syntax = _SYNTAX_HINT.get(driver.dialect, driver.display_name)
    return f"""You are an expert {driver.display_name} assistant embedded in a SQL editor.

Rules:
- Always target {syntax} syntax. Use that engine's quoting, pagination (e.g. \
LIMIT/OFFSET vs TOP/OFFSET-FETCH), and functions — do not mix dialects.
- Prefer SELECT queries unless the user clearly asks for a write operation. Warn before destructive statements.
- Use only tables and columns that are present in the schema context the user provides. If a needed object is missing, say so instead of guessing.
- When you return SQL, wrap it in a single ```sql code fence. Put any explanation outside the fence and keep it short.
"""


def _connect_database(driver: DatabaseDriver, active_database: str, database: str | None) -> str:
    """Pick the database to connect to: single-connection engines always bind to
    the server's configured database; SQL Server uses the per-query active one."""
    if driver.single_database and database:
        return database
    return active_database


def _fetch_schema_context(
    conn: ConnHandle, driver: DatabaseDriver, prompt_text: str, max_tables: int = 40
) -> str:
    """Pull a compact schema snapshot for the connected database.

    Tries to bias toward tables whose names appear in the prompt; otherwise
    returns the first ``max_tables`` user tables.
    """
    tables_res = execute_query(conn, driver.list_tables_sql())
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

    cols_res = execute_query(conn, driver.schema_snapshot_columns_sql())
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


def _call_claude(system_prompt: str, user_message: str) -> str:
    client = _get_client()
    msg = client.messages.create(
        model=_settings.anthropic_model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()


def generate_sql(
    host: str,
    port: int,
    username: str,
    password: str,
    active_database: str,
    prompt: str,
    current_sql: str | None,
    dialect: str = "mssql",
    database: str | None = None,
    lineage_conn: ConnHandle | None = None,
) -> dict:
    """Generate SQL in the server's dialect, with schema context.

    For SQL Server, context is drawn from EVERY user database on the server (the
    active database is the default execution context; other databases are
    referenced with fully-qualified names). For single-connection engines
    (Postgres/MySQL/Snowflake), only the connected database's schema is offered.

    Cross-database candidates carry their lineage standing, because picking a
    table is the same decision here as in ``find_data`` — a query written
    against a retired table is wrong in exactly the same way whether the user
    asked "where does this live" or "write me the query".
    """
    driver = get_driver(dialect)
    connect_db = _connect_database(driver, active_database, database)
    active_conn = build_connection_string(host, port, username, password, connect_db, dialect)
    active_ctx = _fetch_schema_context(active_conn, driver, prompt)

    if driver.cross_database_supported:
        snapshot = lineage.load_snapshot(lineage_conn) if lineage_conn else None
        cross_db_ctx = _fetch_cross_db_schema(
            host, port, username, password, prompt, snapshot=snapshot
        )
        user_msg = (
            f"Active database (default execution context): {active_database}\n\n"
            f"Schema in the active database (subset):\n{active_ctx}\n\n"
            f"Schema matches from OTHER databases on this same server:\n{cross_db_ctx}\n\n"
            "When you reference a table that lives in the active database, you may use "
            "`schema.table`. When you reference a table in any OTHER database listed above, "
            "you MUST use the fully-qualified `[database].[schema].[table]` form so the query "
            "runs from the active database context.\n\n"
        )
        if snapshot:
            user_msg += (
                "Cross-database matches are annotated with what maintains them. Prefer a "
                "table a pipeline writes over a similarly-named one nothing writes, and "
                "never use one marked MISSING FROM CATALOG or a retired lifecycle.\n\n"
            )
    else:
        user_msg = (
            f"Connected database: {connect_db}\n\n"
            f"Schema (subset):\n{active_ctx}\n\n"
            "Reference tables as `schema.table` (this engine connects to one database "
            "at a time — there is no cross-database querying).\n\n"
        )
    if current_sql:
        user_msg += f"Current SQL in editor:\n```sql\n{current_sql}\n```\n\n"
    user_msg += f"Request: {prompt}"

    text = _call_claude(_system_prompt(driver), user_msg)
    return {"sql": _extract_sql(text), "explanation": text, "error": None}


def _fetch_cross_db_schema(
    host: str,
    port: int,
    username: str,
    password: str,
    prompt: str,
    max_databases: int = 25,
    max_matches: int = 150,
    snapshot=None,
) -> str:
    """Search every user database on a SQL Server for tables/columns matching
    prompt keywords. SQL Server only — single-connection engines never call this.

    Returns a compact textual catalog like `[db].schema.table -> col1 type, col2 type`,
    or a placeholder string if there's nothing useful to report. When a lineage
    snapshot is supplied, each line also carries that table's standing and the
    list is ordered with maintained tables first.
    """
    keywords = _extract_keywords(prompt)
    if not keywords:
        return "(no keywords extracted from prompt — only the active database schema is shown above)"

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
        return f"(cross-db schema unavailable: {db_res['error']})"

    databases = [r[0] for r in db_res["rows"]][:max_databases]
    if not databases:
        return "(no other user databases found on this server)"

    like_clauses = " OR ".join(
        ["LOWER(TABLE_NAME) LIKE ?" for _ in keywords]
        + ["LOWER(COLUMN_NAME) LIKE ?" for _ in keywords]
    )
    params = tuple([f"%{k}%" for k in keywords] * 2)

    grouped: dict[tuple[str, str, str], list[str]] = {}
    total = 0
    for db_name in databases:
        if total >= max_matches:
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
            continue
        for row in res["rows"]:
            grouped.setdefault((db_name, row[0], row[1]), []).append(f"{row[2]} {row[3]}")
            total += 1
            if total >= max_matches:
                break

    if not grouped:
        return f"(no tables/columns matched keywords {keywords} in any database on this server)"

    if snapshot is None:
        return "\n".join(
            f"[{db}].{schema}.{table}({', '.join(cols)})"
            for (db, schema, table), cols in grouped.items()
        )

    # No live write stats on this path — generate_sql already runs several
    # queries before the model is called, and a per-database DMV sweep for
    # context the model mostly uses to pick a JOIN target isn't worth the
    # latency. The structural lineage facts are the ones that change the answer.
    return "\n".join(
        f"[{db}].{schema}.{table}({', '.join(cols)})  [{lineage.describe(facts)}]"
        for (db, schema, table), cols, facts in _annotate(grouped, snapshot, {})
    )


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


def _collect_write_stats(
    host: str, port: int, username: str, password: str, databases: list[str]
) -> dict[tuple[str, str, str], tuple[int | None, str | None]]:
    """Row counts and last-write times for the databases that produced matches.

    One query per database, and only for databases that actually matched — a
    keyword hitting two databases costs two queries, not one per database on the
    server. Any database that refuses the DMVs is simply absent from the result.
    """
    stats: dict[tuple[str, str, str], tuple[int | None, str | None]] = {}
    for db_name in databases:
        conn = build_connection_string(host, port, username, password, db_name)
        for (schema, table), value in lineage.fetch_write_stats(conn).items():
            stats[(db_name.lower(), schema, table)] = value
    return stats


def _annotate(
    grouped: dict[tuple[str, str, str], list[str]],
    snapshot,
    stats: dict[tuple[str, str, str], tuple[int | None, str | None]],
) -> list[tuple[tuple[str, str, str], list[str], "lineage.TableFacts | None"]]:
    """Attach lineage facts and live write stats to each candidate, best first.

    Candidates with no lineage record keep their place in the list rather than
    being dropped: an unknown table is not a dead one, and the model is told to
    read a missing record as absence of evidence.
    """
    annotated = []
    for key, cols in grouped.items():
        db_name, schema, table = key
        facts = snapshot.lookup(db_name, schema, table) if snapshot else None
        stat = stats.get((db_name.lower(), schema.lower(), table.lower()))
        if stat is not None:
            if facts is None:
                facts = lineage.TableFacts()
            facts.row_count, facts.last_write = stat
        annotated.append((key, cols, facts))

    annotated.sort(key=lambda item: item[2].rank() if item[2] else (1, 0, 0, 0, 0))
    return annotated


def find_data(
    host: str,
    port: int,
    username: str,
    password: str,
    prompt: str,
    dialect: str = "mssql",
    database: str | None = None,
    max_databases: int = 25,
    max_matches: int = 200,
    lineage_conn: ConnHandle | None = None,
) -> dict:
    """Search for tables/columns relevant to the prompt and suggest where the
    data lives, plus a starter SELECT.

    SQL Server searches every user database on the server; single-connection
    engines search only the connected database.

    Text matching only ever proposes candidates. Which one gets recommended is
    decided from the lineage knowledge base — what a pipeline maintains, what is
    retired, what has quietly stopped being written. ``lineage_conn`` points at
    the server holding the lineage snapshot; without it the answer falls back to
    name similarity alone, which is what this used to do.
    """
    keywords = _extract_keywords(prompt)
    if not keywords:
        return {
            "sql": None,
            "explanation": "",
            "error": "Could not pull any keywords out of your question. Try naming the data you're looking for (e.g. 'occupancy', 'employees').",
        }

    driver = get_driver(dialect)
    if driver.cross_database_supported:
        matches, errors, searched = _find_matches_cross_db(
            host, port, username, password, keywords, max_databases, max_matches
        )
    else:
        matches, errors, searched = _find_matches_single_db(
            host, port, username, password, keywords, dialect, database, max_matches
        )

    if isinstance(searched, str):  # an error string short-circuits
        return {"sql": None, "explanation": "", "error": searched}

    if not matches:
        detail = f" ({'; '.join(errors)})" if errors else ""
        return {
            "sql": None,
            "explanation": "",
            "error": f"No tables or columns matched keywords {keywords} across {searched} database(s).{detail}",
        }

    # Group matches by (db, schema, table) for a compact context.
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for db_name, schema, table, col, dtype in matches:
        grouped.setdefault((db_name, schema, table), []).append(f"{col} {dtype}")

    snapshot = lineage.load_snapshot(lineage_conn) if lineage_conn else None
    stats: dict[tuple[str, str, str], tuple[int | None, str | None]] = {}
    if driver.cross_database_supported:
        stats = _collect_write_stats(
            host, port, username, password, sorted({k[0] for k in grouped})
        )

    annotated = _annotate(grouped, snapshot, stats)
    catalog_lines = []
    for (db_name, schema, table), cols, facts in annotated:
        catalog_lines.append(
            f"[{db_name}].{schema}.{table} -> {', '.join(cols)}\n"
            f"    [{lineage.describe(facts)}]"
        )
    catalog = "\n".join(catalog_lines)

    if driver.cross_database_supported:
        scope_note = (
            "I searched every user database on this SQL Server for tables and columns "
            f"matching the keywords {keywords}. Below is every match, already ordered "
            "with the maintained tables first — each entry is "
            "`[database].schema.table -> matching columns`, then a bracketed line of "
            "evidence about whether anything maintains it."
        )
        qualify_note = (
            "Provide a starter `SELECT TOP 100` query against the best candidate, using "
            "fully-qualified `[database].[schema].[table]` naming so the user can run it from any database context."
        )
    else:
        scope_note = (
            f"I searched the connected database for tables and columns matching the "
            f"keywords {keywords}. Below is every match, already ordered with the "
            "maintained tables first — each entry is "
            "`[database].schema.table -> matching columns`, then a bracketed line of "
            "evidence about whether anything maintains it."
        )
        qualify_note = (
            "Provide a starter query that returns the first 100 rows against the best "
            "candidate, referencing it as `schema.table`."
        )

    evidence_note = lineage.GUIDANCE if snapshot else (
        "The lineage knowledge base is unavailable right now, so these candidates are "
        "ranked by name similarity only. Say so if you are choosing between several "
        "similar names — do not imply you know which one is maintained."
    )

    user_msg = (
        f"User question: {prompt}\n\n"
        f"{scope_note}\n\n"
        f"{evidence_note}\n\n"
        f"{catalog}\n\n"
        "Based on these matches, do two things:\n"
        "1. In one short paragraph, tell the user where this data most likely lives "
        "(name the database, schema, and table) and what maintains it. If multiple "
        "candidates look plausible, list the top 2-3 and say how to tell them apart. "
        "If you are passing over a closer name match because nothing maintains it, say "
        "which one and why.\n"
        f"2. {qualify_note}\n"
    )
    text = _call_claude(_system_prompt(driver), user_msg)
    return {"sql": _extract_sql(text), "explanation": text, "error": None}


def _find_matches_cross_db(host, port, username, password, keywords, max_databases, max_matches):
    """SQL Server: search every user database via sys.databases + LIKE."""
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
        return [], [], f"Could not list databases: {db_res['error']}"

    databases = [r[0] for r in db_res["rows"]][:max_databases]
    if not databases:
        return [], [], "No user databases found on this server."

    like_clauses = " OR ".join(
        ["LOWER(TABLE_NAME) LIKE ?" for _ in keywords]
        + ["LOWER(COLUMN_NAME) LIKE ?" for _ in keywords]
    )
    params = tuple([f"%{k}%" for k in keywords] * 2)

    matches: list[tuple[str, str, str, str, str]] = []
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
    return matches, errors, len(databases)


def _find_matches_single_db(host, port, username, password, keywords, dialect, database, max_matches):
    """Single-connection engines: pull the connected database's columns and
    filter by keyword in Python (no cross-database querying available)."""
    driver = get_driver(dialect)
    if not database:
        return [], [], "This server has no configured database to search."
    conn = build_connection_string(host, port, username, password, database, dialect)
    res = execute_query(conn, driver.schema_snapshot_columns_sql())
    if res.get("error"):
        return [], [], f"Could not read schema: {res['error']}"

    matches: list[tuple[str, str, str, str, str]] = []
    for schema, table, col, dtype in res["rows"]:
        hay = f"{table} {col}".lower()
        if any(k in hay for k in keywords):
            matches.append((database, schema, table, col, dtype))
            if len(matches) >= max_matches:
                break
    return matches, [], 1


def fix_sql(conn: ConnHandle, database: str, sql: str, error: str) -> dict:
    driver = conn.driver if isinstance(conn, ConnHandle) else get_driver("mssql")
    schema_ctx = _fetch_schema_context(conn, driver, sql)
    user_msg = (
        f"Database: {database}\n\n"
        f"Schema (subset):\n{schema_ctx}\n\n"
        f"This {_SYNTAX_HINT.get(driver.dialect, driver.display_name)} query failed:\n```sql\n{sql}\n```\n\n"
        f"Error from the database:\n{error}\n\n"
        f"Diagnose the problem and return a corrected query."
    )
    text = _call_claude(_system_prompt(driver), user_msg)
    return {"sql": _extract_sql(text), "explanation": text, "error": None}
