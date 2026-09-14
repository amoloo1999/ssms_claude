from dataclasses import dataclass
from pydantic_settings import BaseSettings
from functools import lru_cache


# Hardcoded role assignments. These can move to a DB-backed roles table later
# when we wire up team/employees-table integration.
REVMAN_EMAILS: frozenset[str] = frozenset({
    "amoloo@williamwarren.com",
    "chillyer@williamwarren.com",
    "jwille@williamwarren.com",
    "wfan@williamwarren.com",
    "chporter@williamwarren.com",
    "cpj@williamwarren.com",
})

# Approvers can review access requests + manage user grants.
APPROVER_EMAILS: frozenset[str] = frozenset({
    "amoloo@williamwarren.com",
    "cpj@williamwarren.com",
    "wfan@williamwarren.com",
})

# Users exempt from a connection's read_only policy.
#
# A read_only connection (Aurora) refuses writes from everyone, RevMan
# included — that is the point of it. These addresses are the named exception:
# they may write even there. The exemption lifts the CONNECTION gate only, not
# the role gate, so an address here that isn't also a RevMan still cannot write.
#
# Keep this list as short as it can be. Every entry is a person who can write to
# a database the rest of the team is deliberately prevented from writing to, and
# it is easy to forget an exemption is here once it has been added.
WRITE_ANYWHERE_EMAILS: frozenset[str] = frozenset({
    "cpj@williamwarren.com",
})

# Who may write from the phone / tablet surface.
#
# The small screen is for reading. These two addresses keep whatever write
# access they already have when on a phone or tablet; everyone else is read-only
# there regardless of role.
#
# This list can only NARROW what a user may do — it is intersected with the
# normal permission checks, never substituted for them. So amoloo, who is on
# this list, still cannot write to a read_only connection like Aurora, because
# that gate is separate and still applies.
MOBILE_WRITE_EMAILS: frozenset[str] = frozenset({
    "cpj@williamwarren.com",
    "amoloo@williamwarren.com",
})

# External collaborators allowed past the ALLOWED_DOMAIN check. Deliberately
# per-address, not per-domain: adding "getuniti.com" to ALLOWED_DOMAIN would let
# any employee of that company log in. Guests get role='user' like any
# non-RevMan — view-only, `gp` servers hidden, every table hidden until an
# approver grants it. Remove the address here when the engagement ends;
# revoking their grants alone still leaves them able to sign in.
GUEST_EMAILS: frozenset[str] = frozenset({
    "george@getuniti.com",
    "ryna@getuniti.com",
})


@dataclass(frozen=True)
class Sandbox:
    """A schema one non-RevMan user may create tables in and change data in.

    The app does NOT enforce the "only your own tables" part — SQL Server does.
    Writes from a sandbox user run under ``login``, a SQL login that owns
    ``schema``, holds CREATE TABLE, and can write nowhere else. The regex
    denylist in services/permissions.py is not a boundary anyone should trust
    with prod writes; the login is. See ops/sql/sandbox_mfriday.sql for the
    server side, and its password lives in SANDBOX_PASSWORDS in .env.

    ``host`` and ``port`` pin the sandbox to the SQL Server instance the login
    was created on, so a server row pointing somewhere else never tries it.
    """

    host: str
    port: int
    database: str
    schema: str
    login: str

    def covers_server(self, server) -> bool:
        return (
            (getattr(server, "dialect", None) or "mssql") == "mssql"
            and (server.host or "").lower() == self.host.lower()
            and (server.port or 1433) == self.port
        )

    def covers(self, server, database: str | None) -> bool:
        return self.covers_server(server) and (database or "").lower() == self.database.lower()


# Users with a sandbox. Everything else about them is unchanged: view-only,
# tables hidden until granted. Row edits in the table browser stay RevMan-only.
SANDBOX_USERS: dict[str, Sandbox] = {
    "mfriday@williamwarren.com": Sandbox(
        host="13.57.123.119",
        port=1433,
        database="Sites",
        schema="sandbox_mfriday",
        login="ssms_mfriday",
    ),
}


def is_revman(email: str | None) -> bool:
    return bool(email) and email.lower() in {e.lower() for e in REVMAN_EMAILS}


def is_approver(email: str | None) -> bool:
    return bool(email) and email.lower() in {e.lower() for e in APPROVER_EMAILS}


def is_guest(email: str | None) -> bool:
    return bool(email) and email.lower() in {e.lower() for e in GUEST_EMAILS}


def can_write_anywhere(email: str | None) -> bool:
    """Exempt from a connection's read_only policy. See WRITE_ANYWHERE_EMAILS."""
    return bool(email) and email.lower() in {e.lower() for e in WRITE_ANYWHERE_EMAILS}


def can_write_on_mobile(email: str | None) -> bool:
    """May write from the phone/tablet surface. See MOBILE_WRITE_EMAILS."""
    return bool(email) and email.lower() in {e.lower() for e in MOBILE_WRITE_EMAILS}


def sandbox_for(email: str | None) -> Sandbox | None:
    """The user's sandbox, if they have one. RevMan never does — they already
    write through the shared login, and routing them through a restricted one
    would only take access away."""
    if not email or is_revman(email):
        return None
    return {e.lower(): s for e, s in SANDBOX_USERS.items()}.get(email.lower())


def sandbox_password(login: str) -> str:
    """"" when unset, and callers must then refuse the write — never fall back
    to the shared login."""
    return (get_sandbox_passwords().get(login) or "").strip()


class Settings(BaseSettings):
    app_name: str = "SQL Studio"
    secret_key: str = "change-me-in-production-use-a-real-secret-key"
    database_url: str = "sqlite+aiosqlite:///./sql_studio.db"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/callback"
    allowed_domain: str = ""  # e.g. "yourcompany.com" — empty allows all

    # Frontend URL for CORS and redirects
    frontend_url: str = "http://localhost:5173"

    # Config file path for seeding servers
    config_file: str = "config.yaml"

    # Shared secret the Airflow scheduler DAG presents on /api/schedules/runner/*.
    # Empty means the runner endpoints refuse every request — an unset secret
    # fails closed, never open.
    #
    # Prefer scheduler_token_param (an SSM Parameter Store SecureString) over
    # putting the value in .env: a parameter is encrypted at rest, and changing
    # it never has to travel through an SSM command, whose parameters are
    # retained in AWS command history for 30 days.
    scheduler_token: str = ""
    scheduler_token_param: str = ""
    aws_region: str = "us-west-1"

    # Sandbox login passwords, keyed by login name.
    #
    # Two sources, in order (see get_sandbox_passwords):
    #   1. sandbox_passwords_param — an SSM SecureString whose value is the JSON
    #      map {"ssms_mfriday": "..."}. Preferred: the secret is encrypted at
    #      rest, never written to disk on the box, and never passes through an
    #      SSM RunCommand (whose parameters are logged for 30 days). It has a
    #      default name so no .env edit is needed to switch a box onto it.
    #   2. sandbox_passwords — the same JSON inline in .env, the fallback.
    sandbox_passwords: dict[str, str] = {}
    # NB: an SSM parameter name may NOT start with "ssm" or "aws" (reserved,
    # case-insensitive) — so this is /sql-studio/..., not /ssms/....
    sandbox_passwords_param: str = "/sql-studio/sandbox_passwords"

    # Anthropic / Claude AI assistant
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_scheduler_token() -> str:
    """The scheduler shared secret, preferring SSM Parameter Store.

    Resolution order:
      1. ``SCHEDULER_TOKEN_PARAM`` — the name of a SecureString parameter. Read
         with the instance role, so the value is never written to disk on this
         box and never passes through an SSM command's parameters.
      2. ``SCHEDULER_TOKEN`` in .env — the fallback, and what shipped first.

    A Parameter Store failure falls back to .env rather than raising: losing the
    scheduler is bad, but taking the whole app down with it is worse. The result
    is cached, so this costs one API call per process.

    Returns "" when neither source yields a value, which makes the runner
    endpoints refuse every request — unset fails closed.
    """
    settings = get_settings()
    name = (settings.scheduler_token_param or "").strip()

    if name:
        try:
            import boto3  # imported lazily so the app runs without it installed

            client = boto3.client("ssm", region_name=settings.aws_region)
            value = client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
            if value:
                return value.strip()
        except Exception as exc:  # noqa: BLE001 — any failure means fall back
            # Printed, not raised: worth seeing in the service log, not worth an
            # outage. If .env still holds the token the app keeps working.
            print(f"[config] could not read {name} from Parameter Store: {exc}")

    return (settings.scheduler_token or "").strip()


@lru_cache
def get_sandbox_passwords() -> dict[str, str]:
    """Sandbox login passwords, preferring an SSM SecureString.

    Resolution order (mirrors get_scheduler_token):
      1. ``SANDBOX_PASSWORDS_PARAM`` — the name of a SecureString whose value is
         the JSON map ``{"ssms_mfriday": "..."}``. Read with the instance role,
         so no secret is written to the box's .env and none travels through a
         logged SSM RunCommand. Has a default name, so switching a box onto
         Parameter Store needs only the parameter to exist — no .env edit.
      2. ``SANDBOX_PASSWORDS`` inline in .env — the fallback.

    A Parameter Store failure (missing parameter, no permission, boto3 absent)
    falls back to .env rather than raising. Cached: one API call per process.
    Returns {} when neither yields anything, so sandbox_password() comes back ""
    and the write is refused — unset fails closed, never onto the shared login.
    """
    settings = get_settings()
    name = (settings.sandbox_passwords_param or "").strip()

    if name:
        try:
            import json
            import boto3  # imported lazily so the app runs without it installed

            client = boto3.client("ssm", region_name=settings.aws_region)
            value = client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
            parsed = json.loads(value) if value else {}
            if isinstance(parsed, dict) and parsed:
                return {str(k): str(v) for k, v in parsed.items()}
        except Exception as exc:  # noqa: BLE001 — any failure means fall back
            print(f"[config] could not read {name} from Parameter Store: {exc}")

    return dict(settings.sandbox_passwords or {})
