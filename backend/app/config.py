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
