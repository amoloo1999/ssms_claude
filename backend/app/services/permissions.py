"""Permission helpers used by routers to gate access to servers/tables/SQL.

Role model (current iteration — hardcoded in app.config):
- RevMan: full access to every server kind, can write (INSERT/UPDATE/DELETE/DDL).
- Approver: subset of RevMan that can review AccessRequests.
- User (any other @williamwarren.com address): view-only on servers with
  kind == 'main'; servers with kind == 'gp' are completely hidden from them.
  Tables are hidden until they have a matching TablePermission grant.
"""

from __future__ import annotations

import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import is_revman
from app.models import ServerConnection, TablePermission


def can_access_server(user: dict, server: ServerConnection) -> bool:
    """RevMan = both kinds. Non-RevMan = only servers with kind == 'main'."""
    if is_revman(user.get("email", "")):
        return True
    return (server.kind or "main") == "main"


def server_allows_writes(server: ServerConnection | None) -> bool:
    """Whether this connection accepts writes at all, regardless of who is asking.

    A server marked ``read_only`` refuses writes for EVERYONE, RevMan included.
    That's how "Aurora is read-only for all users" is expressed: it is a
    property of the connection, not of the caller's role.
    """
    if server is None:
        return False
    return (getattr(server, "write_policy", None) or "read_write") == "read_write"


def can_write(user: dict, server: ServerConnection | None = None) -> bool:
    """Both must hold: the role permits writing, and the server accepts them."""
    if not is_revman(user.get("email", "")):
        return False
    return server_allows_writes(server)


async def get_user_grants(
    db: AsyncSession, email: str
) -> set[tuple[int, str, str, str]]:
    """Return the user's grants as a lowercased set of
    (server_id, database, schema_name, table_name) tuples. Any of the string
    fields may be the literal '*' to mean "all" — that's how database-wide
    and server-wide grants are stored."""
    result = await db.execute(
        select(TablePermission).where(TablePermission.user_email == email)
    )
    grants = result.scalars().all()
    return {
        (g.server_id, g.database.lower(), g.schema_name.lower(), g.table_name.lower())
        for g in grants
    }


def _wc_match(grant_value: str, actual: str) -> bool:
    return grant_value == "*" or grant_value == actual.lower()


def grant_covers(
    grants: set[tuple[int, str, str, str]],
    server_id: int,
    database: str | None = None,
    schema_name: str | None = None,
    table_name: str | None = None,
) -> bool:
    """True if any grant in the set covers the requested target. Pass None for
    levels you don't care about (e.g. for `list_databases` you only check
    server_id + database)."""
    for sid, gdb, gsch, gtbl in grants:
        if sid != server_id:
            continue
        if database is not None and not _wc_match(gdb, database):
            continue
        if schema_name is not None and not _wc_match(gsch, schema_name):
            continue
        if table_name is not None and not _wc_match(gtbl, table_name):
            continue
        return True
    return False


def has_server_wide_grant(
    grants: set[tuple[int, str, str, str]], server_id: int
) -> bool:
    """User has a grant covering the entire server (db=schema=table='*')."""
    return any(
        sid == server_id and gdb == "*" and gsch == "*" and gtbl == "*"
        for sid, gdb, gsch, gtbl in grants
    )


# ── SQL safety / table extraction ────────────────────────────────────────────
#
# These run against user-submitted T-SQL before non-RevMan executions. The
# read-only check is a denylist — anything matching a forbidden verb is
# rejected. The table extractor pulls FROM/JOIN targets so we can verify each
# referenced table is in the user's grant set. CTEs are excluded so they
# aren't treated as physical tables to permission-check.

_FORBIDDEN_RE = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|"
    # EXEC/CALL: SQL Server EXEC + the Postgres/MySQL/Snowflake CALL equivalent.
    r"EXEC|EXECUTE|CALL|GRANT|REVOKE|DENY|BACKUP|RESTORE|"
    r"BULK\s+INSERT|"
    # Engine-specific writers: COPY (Postgres), LOAD DATA/XML and REPLACE INTO
    # (MySQL). REPLACE/LOAD are only forbidden in their write forms so the
    # common REPLACE() string function and identifiers stay allowed.
    r"COPY|LOAD\s+DATA|LOAD\s+XML|REPLACE\s+INTO|"
    # SELECT ... INTO new_table — creates a new table.
    r"SELECT\b[\s\S]*?\bINTO\b"
    r")\b",
    re.IGNORECASE,
)

# Identifier forms across engines: [bracketed] (T-SQL), "double" (Postgres/
# Snowflake/ANSI), `backtick` (MySQL), or a bare word.
_IDENT = r'(?:\[[^\]]+\]|"[^"]+"|`[^`]+`|[A-Za-z_][A-Za-z0-9_$#@]*)'
# Characters stripped to get the raw name out of any of the quoted forms.
_QUOTES = "[]\"`"
_FROM_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+((?:{_IDENT}\.){{0,2}}{_IDENT})", re.IGNORECASE
)
_CTE_RE = re.compile(
    rf"\b(?:WITH|,)\s+({_IDENT})\s*(?:\([^)]*\))?\s+AS\s*\(", re.IGNORECASE
)


def _strip_strings_and_comments(sql: str) -> str:
    cleaned = re.sub(r"'(?:''|[^'])*'", "''", sql)
    cleaned = re.sub(r"--[^\n]*", "", cleaned)
    cleaned = re.sub(r"/\*[\s\S]*?\*/", "", cleaned)
    return cleaned


def is_select_only(sql: str) -> tuple[bool, str | None]:
    """Return (ok, reason). Rejects any DDL/DML/EXEC for non-RevMan users."""
    cleaned = _strip_strings_and_comments(sql or "")
    m = _FORBIDDEN_RE.search(cleaned)
    if m:
        verb = m.group(1).upper().split()[0]
        return False, f"Statement type '{verb}' is not allowed for view-only users."
    return True, None


def extract_referenced_tables(
    sql: str, default_database: str, default_schema: str = "dbo"
) -> list[tuple[str, str, str]]:
    """Return a list of (database, schema, table) tuples referenced via FROM/JOIN.

    Uses the active database as the default when the reference is unqualified
    or only schema-qualified. CTE names are stripped so they're not treated
    as physical tables. ``default_schema`` is the engine's default (``dbo`` for
    SQL Server, ``public`` for Postgres, the database name for MySQL) — used
    when a reference is unqualified.
    """
    cleaned = _strip_strings_and_comments(sql or "")
    ctes = {m.group(1).strip(_QUOTES).lower() for m in _CTE_RE.finditer(cleaned)}

    refs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for m in _FROM_RE.finditer(cleaned):
        full = m.group(1)
        parts = [p.strip().strip(_QUOTES) for p in full.split(".")]
        if not parts or not parts[-1]:
            continue
        if len(parts) == 1:
            db, sch, tbl = default_database, default_schema, parts[0]
        elif len(parts) == 2:
            db, sch, tbl = default_database, parts[0], parts[1]
        elif len(parts) == 3:
            db, sch, tbl = parts[0], parts[1], parts[2]
        else:
            continue
        if tbl.lower() in ctes:
            continue
        key = (db.lower(), sch.lower(), tbl.lower())
        if key in seen:
            continue
        seen.add(key)
        refs.append((db, sch, tbl))
    return refs


async def check_query_permissions(
    db: AsyncSession,
    user: dict,
    server_id: int,
    database: str,
    sql: str,
    default_schema: str = "dbo",
    write_policy: str = "read_write",
) -> tuple[bool, dict]:
    """Validate a SQL string before it is executed.

    Two independent gates:

    1. ``write_policy`` — a property of the connection. When the server is
       read-only, writes are refused for everyone, RevMan included. This runs
       BEFORE the role check, which is the whole point: it is not a role
       exemption.
    2. The role. RevMan may write on a read_write server and skips the grant
       check; everyone else is restricted to SELECT over their granted tables.

    Returns (allowed, error_payload). error_payload is shaped for the frontend
    to surface inline `Request access` buttons:

        {"detail": "...", "missing_tables": [{"server_id", "database", "schema", "table"}, ...]}
    """
    if write_policy == "read_only":
        ok, _ = is_select_only(sql)
        if not ok:
            return False, {
                "detail": "This connection is read-only — writes are blocked for every user.",
                "missing_tables": [],
            }

    if is_revman(user.get("email", "")):
        return True, {}

    ok, reason = is_select_only(sql)
    if not ok:
        return False, {"detail": reason or "Write operations are not allowed for view-only users.", "missing_tables": []}

    grants = await get_user_grants(db, user["email"])
    refs = extract_referenced_tables(sql, database, default_schema)
    missing: list[dict] = []
    for ref_db, sch, tbl in refs:
        if not grant_covers(grants, server_id, ref_db, sch, tbl):
            missing.append(
                {
                    "server_id": server_id,
                    "database": ref_db,
                    "schema": sch,
                    "table": tbl,
                }
            )
    if missing:
        names = ", ".join(f"[{m['database']}].[{m['schema']}].[{m['table']}]" for m in missing)
        return False, {
            "detail": f"You don't have access to: {names}. Request access from the Admin tab or via the prompt below.",
            "missing_tables": missing,
        }
    return True, {}


def filter_visible_tables(
    user: dict,
    grants: set[tuple[int, str, str, str]],
    server_id: int,
    database: str,
    tables: list[dict],
) -> list[dict]:
    """Drop tables the (non-RevMan) user has no grant for. Wildcard-aware:
    a database-wide grant lets every table through; a server-wide grant
    short-circuits the loop. RevMan: pass-through."""
    if is_revman(user.get("email", "")):
        return tables
    out: list[dict] = []
    for t in tables:
        sch = t.get("schema", "dbo")
        nm = t.get("name", "")
        if grant_covers(grants, server_id, database, sch, nm):
            out.append(t)
    return out
