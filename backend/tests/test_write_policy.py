"""Tests for the per-server write policy.

Security-critical, and the failure mode is asymmetric: a miss here lets a write
reach a connection that is supposed to refuse them (Aurora), which is the one
thing the policy exists to prevent. The RevMan cases matter most, because
RevMan is exactly the role that bypasses every other check.

Runnable with pytest or directly:  python tests/test_write_policy.py
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.permissions import (  # noqa: E402
    can_write,
    check_query_permissions,
    client_surface,
    server_allows_writes,
    surface_allows_writes,
)

REVMAN = {"email": "amoloo@williamwarren.com"}
VIEWER = {"email": "someone.else@williamwarren.com"}
# On WRITE_ANYWHERE_EMAILS: exempt from a connection's read_only policy.
EXEMPT = {"email": "cpj@williamwarren.com"}
# A RevMan on NEITHER special list — writes on the desktop, read-only on a phone.
REVMAN_OTHER = {"email": "chillyer@williamwarren.com"}

WRITE_SQL = "UPDATE dbo.Sites SET Name = 'x' WHERE SiteId = 1"
READ_SQL = "SELECT * FROM dbo.Sites"


def _server(write_policy="read_write", name="TEST"):
    return types.SimpleNamespace(write_policy=write_policy, name=name)


def _check(user, sql, write_policy, surface="desktop"):
    """check_query_permissions is async and hits the DB only for non-RevMan
    grant lookups; the read-only path short-circuits before that, so a None
    session is safe for the cases asserted here."""
    return asyncio.run(
        check_query_permissions(None, user, 1, "Sites", sql, "dbo", write_policy, surface)
    )


# ── server_allows_writes ────────────────────────────────────────────────────


def test_allows_writes_by_policy():
    assert server_allows_writes(_server("read_write")) is True
    assert server_allows_writes(_server("read_only")) is False


def test_missing_policy_defaults_to_read_write():
    # Rows migrated from before the column existed come back with None; they
    # must keep working exactly as they did.
    assert server_allows_writes(_server(None)) is True
    assert server_allows_writes(types.SimpleNamespace(name="legacy")) is True


def test_no_server_denies():
    assert server_allows_writes(None) is False


# ── can_write ───────────────────────────────────────────────────────────────


def test_revman_can_write_on_read_write_server():
    assert can_write(REVMAN, _server("read_write")) is True


def test_revman_cannot_write_on_read_only_server():
    # The whole point: read-only is a property of the connection, and RevMan
    # does not get an exemption.
    assert can_write(REVMAN, _server("read_only")) is False


def test_viewer_cannot_write_anywhere():
    assert can_write(VIEWER, _server("read_write")) is False
    assert can_write(VIEWER, _server("read_only")) is False


# ── check_query_permissions ─────────────────────────────────────────────────


def test_read_only_server_blocks_revman_write():
    allowed, payload = _check(REVMAN, WRITE_SQL, "read_only")
    assert allowed is False
    assert "read-only" in payload["detail"]


def test_read_only_server_still_allows_reads():
    allowed, _ = _check(REVMAN, READ_SQL, "read_only")
    assert allowed is True


def test_read_write_server_allows_revman_write():
    allowed, _ = _check(REVMAN, WRITE_SQL, "read_write")
    assert allowed is True


def test_policy_check_precedes_role_check():
    # A viewer on a read-only server is refused for the connection's reason,
    # not the role's — the connection gate runs first.
    allowed, payload = _check(VIEWER, WRITE_SQL, "read_only")
    assert allowed is False
    assert "read-only" in payload["detail"]


# ── the named exemption (WRITE_ANYWHERE_EMAILS) ─────────────────────────────


def test_exempt_user_may_write_on_read_only_server():
    assert server_allows_writes(_server("read_only"), EXEMPT) is True
    assert can_write(EXEMPT, _server("read_only")) is True
    allowed, _ = _check(EXEMPT, WRITE_SQL, "read_only")
    assert allowed is True


def test_exemption_is_per_user_not_global():
    # The exemption must not leak: another RevMan on the same connection is
    # still refused. This is the test that would catch an exemption
    # accidentally implemented as a property of the server.
    assert server_allows_writes(_server("read_only"), REVMAN) is False
    assert can_write(REVMAN, _server("read_only")) is False


def test_exemption_without_user_denies():
    # No caller supplied — fall back to the connection's own policy.
    assert server_allows_writes(_server("read_only")) is False


# ── the phone / tablet surface (MOBILE_WRITE_EMAILS) ────────────────────────


class _Req:
    """Minimal stand-in for a Starlette request."""

    def __init__(self, headers):
        self.headers = headers


def test_surface_is_read_from_the_header_and_defaults_closed_to_desktop():
    assert client_surface(_Req({"x-client-surface": "mobile"})) == "mobile"
    assert client_surface(_Req({"x-client-surface": "TABLET"})) == "tablet"
    # Anything unrecognised or absent is 'desktop' — the permissive value, which
    # is safe because this setting can only ever remove permission.
    assert client_surface(_Req({})) == "desktop"
    assert client_surface(_Req({"x-client-surface": "watch"})) == "desktop"


def test_mobile_blocks_writes_for_a_revman_not_on_the_list():
    # chillyer is a RevMan and may write on the desktop, but not from a phone.
    allowed, payload = _check(REVMAN_OTHER, WRITE_SQL, "read_write", surface="mobile")
    assert allowed is False
    assert "phone or tablet" in payload["detail"]

    allowed, _ = _check(REVMAN_OTHER, WRITE_SQL, "read_write", surface="desktop")
    assert allowed is True


def test_mobile_allows_writes_for_the_two_named_addresses():
    for user in (REVMAN, EXEMPT):  # amoloo, cpj
        allowed, _ = _check(user, WRITE_SQL, "read_write", surface="mobile")
        assert allowed is True, user["email"]


def test_mobile_still_allows_reads_for_everyone():
    for surface in ("mobile", "tablet"):
        allowed, _ = _check(REVMAN_OTHER, READ_SQL, "read_write", surface=surface)
        assert allowed is True, surface


def test_surface_narrows_but_never_widens():
    # The whole safety property. amoloo is on the mobile list, but Aurora is a
    # read_only connection and he is NOT on WRITE_ANYWHERE_EMAILS — so being
    # allowed on mobile must not hand him Aurora writes.
    allowed, payload = _check(REVMAN, WRITE_SQL, "read_only", surface="mobile")
    assert allowed is False
    assert "read-only" in payload["detail"]

    # And the same on the desktop, unchanged by any of this.
    allowed, _ = _check(REVMAN, WRITE_SQL, "read_only", surface="desktop")
    assert allowed is False


def test_cpj_keeps_aurora_writes_from_a_phone():
    # cpj is on both lists, so both gates pass — his permissions are unchanged.
    allowed, _ = _check(EXEMPT, WRITE_SQL, "read_only", surface="mobile")
    assert allowed is True


def test_surface_helper_matches_the_lists():
    assert surface_allows_writes("desktop", VIEWER["email"]) is True
    assert surface_allows_writes("mobile", VIEWER["email"]) is False
    assert surface_allows_writes("mobile", REVMAN["email"]) is True
    assert surface_allows_writes("tablet", EXEMPT["email"]) is True
    assert surface_allows_writes("tablet", REVMAN_OTHER["email"]) is False


def test_exemption_lifts_connection_gate_only_not_role_gate():
    # A viewer is still a viewer. If this ever passes, the exemption has been
    # wired as a role grant rather than a connection-gate lift.
    viewer_exempt = {"email": "someone.else@williamwarren.com"}
    assert can_write(viewer_exempt, _server("read_only")) is False


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
