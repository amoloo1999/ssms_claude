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

from app.config import (
    Sandbox,
    can_write_anywhere,
    can_write_on_mobile,
    is_revman,
    sandbox_for,
    sandbox_password,
)

# The surfaces the frontend can declare. Anything unrecognised is treated as
# 'desktop', which is the permissive value — see surface_allows_writes for why
# that is safe.
MOBILE_SURFACES = frozenset({"mobile", "tablet"})


def client_surface(request) -> str:
    """Which surface the request came from, per the X-Client-Surface header.

    The frontend sets this from the viewport width. It is trivially forgeable —
    and that is fine, because this value can only ever REMOVE permission. A user
    who forges 'desktop' gets exactly the access they already have on a desktop;
    there is nothing to escalate to. It is a guard rail for the small screen, not
    an authentication boundary.
    """
    try:
        value = (request.headers.get("x-client-surface") or "").strip().lower()
    except Exception:
        return "desktop"
    return value if value in MOBILE_SURFACES else "desktop"


def surface_allows_writes(surface: str, email: str | None) -> bool:
    """Whether writes are permitted from this surface for this user.

    Intersected with every other check, never substituted for one.
    """
    if surface in MOBILE_SURFACES:
        return can_write_on_mobile(email)
    return True
from app.models import ServerConnection, TablePermission
from app.services.connection import get_connection_string
from app.services.drivers import ConnHandle, get_driver


def can_access_server(user: dict, server: ServerConnection) -> bool:
    """RevMan = both kinds. Non-RevMan = only servers with kind == 'main'."""
    if is_revman(user.get("email", "")):
        return True
    return (server.kind or "main") == "main"


def server_allows_writes(
    server: ServerConnection | None, user: dict | None = None
) -> bool:
    """Whether this connection accepts writes from this caller.

    A server marked ``read_only`` refuses writes for EVERYONE, RevMan included —
    it is a property of the connection, not of the caller's role. The one
    exception is the named exemption list in config (``WRITE_ANYWHERE_EMAILS``),
    which lifts this gate for specific people.
    """
    if server is None:
        return False
    if (getattr(server, "write_policy", None) or "read_write") == "read_write":
        return True
    return bool(user) and can_write_anywhere(user.get("email", ""))


def can_write(user: dict, server: ServerConnection | None = None) -> bool:
    """Both must hold: the role permits writing, and the connection accepts it.

    Note the exemption only lifts the connection gate — an exempt address that
    is not also a RevMan still cannot write.
    """
    if not is_revman(user.get("email", "")):
        return False
    return server_allows_writes(server, user)


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
    out = {
        (g.server_id, g.database.lower(), g.schema_name.lower(), g.table_name.lower())
        for g in grants
    }
    # A sandbox user can always see their own schema. Implicit rather than a
    # TablePermission row, so it can't be revoked by accident — and removing
    # them from SANDBOX_USERS removes it.
    sandbox = sandbox_for(email)
    if sandbox is not None:
        servers = (await db.execute(select(ServerConnection))).scalars().all()
        for s in servers:
            if sandbox.covers_server(s):
                out.add((s.id, sandbox.database.lower(), sandbox.schema.lower(), "*"))
    return out


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
# These run against user-submitted SQL before non-RevMan executions, and the
# stakes are high: the shared login these run under is a SQL Server sysadmin, so
# anything that slips through this check executes with full server rights.
#
# The read-only check is TWO layers, both applied per executed batch:
#
#   1. An allowlist on the FIRST token of each batch — it must be SELECT or WITH.
#      This is what a denylist alone cannot do. In T-SQL a stored procedure runs
#      WITHOUT the EXEC keyword when it is the first statement of a batch
#      (`sp_executesql N'DELETE ...'`), and as a non-first statement it is a
#      syntax error — so requiring the batch to START with a read keyword is
#      exactly what blocks a bare procedure call. Batches are split on GO the
#      same way execution splits them, or the check could pass on the first
#      batch while a later one runs a bare proc.
#   2. The verb denylist, still applied to each batch, because a statement that
#      begins SELECT can still write or exfiltrate: SELECT ... INTO, an embedded
#      EXEC, or OPENROWSET/OPENQUERY reaching another data source.
#
# The table extractor pulls FROM/JOIN targets so we can verify each referenced
# table is in the user's grant set. CTEs are excluded so they aren't treated as
# physical tables to permission-check.

# A read batch must open with one of these (an optional leading `(` allows a
# parenthesised SELECT / UNION). Anything else — EXEC, a bare proc name, DECLARE,
# DBCC, a write verb — is refused before it runs.
_READ_LEADING_RE = re.compile(r"^\s*\(*\s*(SELECT|WITH)\b", re.IGNORECASE)

_FORBIDDEN_RE = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|"
    # EXEC/CALL: SQL Server EXEC + the Postgres/MySQL/Snowflake CALL equivalent.
    r"EXEC|EXECUTE|CALL|GRANT|REVOKE|DENY|BACKUP|RESTORE|"
    r"BULK\s+INSERT|"
    # Server-control / DoS verbs. A sysadmin login makes these catastrophic, and
    # most are already blocked by the first-token rule; listed here so they are
    # also caught if embedded after a leading SELECT.
    r"DBCC|KILL|SHUTDOWN|RECONFIGURE|WAITFOR|DISABLE\s+TRIGGER|ENABLE\s+TRIGGER|"
    # Reaching another data source from inside a query (exfiltration / SSRF).
    r"OPENROWSET|OPENQUERY|OPENDATASOURCE|"
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


def _batch_is_read(batch: str) -> tuple[bool, str | None]:
    """Whether a single executed batch is a pure read. See _READ_LEADING_RE."""
    cleaned = _strip_strings_and_comments(batch or "")
    if not cleaned.strip():
        # Empty or comment-only batch: nothing executes.
        return True, None
    if not _READ_LEADING_RE.match(cleaned):
        m = re.match(r"\s*\(*\s*([A-Za-z_@#][\w@#$]*)", cleaned)
        verb = (m.group(1) if m else cleaned.split()[0]).upper()
        return False, (
            f"Only SELECT queries are allowed for view-only users (this starts with '{verb}'). "
            "A stored-procedure call or any statement that changes data is blocked."
        )
    m = _FORBIDDEN_RE.search(cleaned)
    if m:
        verb = m.group(1).upper().split()[0]
        return False, f"Statement type '{verb}' is not allowed for view-only users."
    return True, None


def is_select_only(sql: str, driver=None) -> tuple[bool, str | None]:
    """Return (ok, reason). A read must be a SELECT/WITH in EVERY executed batch.

    ``driver`` is threaded through wherever the answer gates real execution, so
    batches are split on the engine's separator (``GO``) exactly as they will be
    run. Without it the whole string is treated as one batch — safe for the
    convenience callers (audit classification, the mobile guard), but the
    security gate must pass the driver so a bare procedure call hiding after a
    ``GO`` can't ride in behind a leading SELECT.
    """
    batches = driver.split_batches(sql or "") if driver is not None else [sql or ""]
    for batch in batches:
        ok, reason = _batch_is_read(batch)
        if not ok:
            return False, reason
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
    surface: str = "desktop",
    sandbox: Sandbox | None = None,
    driver=None,
) -> tuple[bool, dict]:
    """Validate a SQL string before it is executed.

    ``sandbox`` lifts the role gate's SELECT-only rule and nothing else. Only
    ``authorize_query`` passes it, because it is only safe when the statement
    then runs under the sandbox login — which is what authorize_query guarantees.

    Two independent gates:

    1. ``surface`` — the phone and tablet are read-only except for the
       addresses in ``MOBILE_WRITE_EMAILS``.
    2. ``write_policy`` — a property of the connection. When the server is
       read-only, writes are refused for everyone, RevMan included, except the
       named addresses in ``WRITE_ANYWHERE_EMAILS``.
    3. The role. RevMan may write on a read_write server and skips the grant
       check; everyone else is restricted to SELECT over their granted tables.

    All three are intersected. Each can only remove permission, so ordering
    affects the message a user sees, not what they are ultimately allowed.

    Returns (allowed, error_payload). error_payload is shaped for the frontend
    to surface inline `Request access` buttons:

        {"detail": "...", "missing_tables": [{"server_id", "database", "schema", "table"}, ...]}
    """
    email = user.get("email", "")

    # Gate 1 — the surface. The phone and tablet are for reading; only the
    # addresses in MOBILE_WRITE_EMAILS keep their write access there. Runs first
    # because it is the narrowest and cheapest check.
    if not surface_allows_writes(surface, email):
        ok, _ = is_select_only(sql, driver)
        if not ok:
            return False, {
                "detail": (
                    "Writes aren't allowed from the phone or tablet view. "
                    "Open this on the desktop app instead."
                ),
                "missing_tables": [],
            }

    # Gate 2 — the connection. A read_only server refuses writes from everyone,
    # RevMan included, except the named exemption list.
    if write_policy == "read_only" and not can_write_anywhere(email):
        ok, _ = is_select_only(sql, driver)
        if not ok:
            return False, {
                "detail": "This connection is read-only — writes are blocked for every user.",
                "missing_tables": [],
            }

    if is_revman(user.get("email", "")):
        return True, {}

    ok, reason = is_select_only(sql, driver)
    if not ok and sandbox is None:
        return False, {"detail": reason or "Write operations are not allowed for view-only users.", "missing_tables": []}

    # Still runs for a sandbox write: `INSERT INTO sandbox.x SELECT ... FROM
    # dbo.Units` reads dbo.Units, and that needs a grant like any other read.
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


async def authorize_query(
    db: AsyncSession,
    user: dict,
    server: ServerConnection,
    database: str,
    sql: str,
    surface: str = "desktop",
) -> tuple[bool, dict, ConnHandle | None]:
    """Check a statement AND choose the credentials it runs under.

    Every path that executes user-supplied SQL goes through here. The two
    decisions are made together on purpose: a sandbox write is only safe
    because it runs as the sandbox login, and if the check and the connection
    were chosen in separate places, one call site that forgot to pass the
    sandbox along would send an allowed write out under the shared login.

    Returns (allowed, error_payload, connection). Reads — including a sandbox
    user's reads — use the server's shared login exactly as before. Only a
    sandbox user's writes switch to the sandbox login, and if its password is
    not configured the write is refused rather than falling back.
    """
    driver = get_driver(server.dialect)
    sandbox = sandbox_for(user.get("email", ""))
    if sandbox is not None and not sandbox.covers(server, database):
        sandbox = None

    allowed, payload = await check_query_permissions(
        db, user, server.id, database, sql,
        driver.default_schema_for(server.database),
        write_policy=server.write_policy or "read_write",
        surface=surface,
        sandbox=sandbox,
        driver=driver,
    )
    if not allowed:
        return False, payload, None

    # Reads use the shared login; only a sandbox WRITE switches. The read test
    # uses the driver so batches are split on GO — a bare procedure call after a
    # GO is not a read, so it can never be classified as one and sent out under
    # the shared sysadmin login.
    if sandbox is None or is_select_only(sql, driver)[0]:
        return True, {}, await get_connection_string(db, server.id, database)

    password = sandbox_password(sandbox.login)
    if not password:
        return False, {
            "detail": "Your sandbox isn't set up on the server yet. Ask an admin.",
            "missing_tables": [],
        }, None
    conn_str = driver.build_connection_string(
        server.host, server.port, sandbox.login, password, database
    )
    return True, {}, ConnHandle(server.dialect, conn_str)


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
