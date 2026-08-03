"""Tests for scheduled-query alert conditions.

The condition is written by a user and decides whether an email goes out, so
two failure modes matter and they are not symmetric:

- a condition that should alert but doesn't means a silent miss;
- a condition that alerts when it shouldn't trains people to ignore the alert,
  which is worse, because then the real one gets ignored too.

It is also user input that must never become code — hence a closed grammar
rather than eval().

Runnable with pytest or directly:  python tests/test_schedules.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.schedules import (  # noqa: E402
    describe_condition,
    evaluate_condition,
    is_valid_condition,
)


def _ev(cond, rows=0, ms=0):
    return evaluate_condition(cond, row_count=rows, duration_ms=ms)


# ── The common cases ────────────────────────────────────────────────────────


def test_empty_condition_always_alerts():
    # "Mail me this report every morning" is the common case.
    assert _ev("", rows=0) is True
    assert _ev("   ", rows=99) is True


def test_rowcount_greater_than():
    assert _ev("rowcount > 0", rows=1) is True
    assert _ev("rowcount > 0", rows=0) is False


def test_rowcount_equals_zero_catches_the_empty_result():
    # "Tell me when this returns nothing" — the reconciliation-check case.
    assert _ev("rowcount = 0", rows=0) is True
    assert _ev("rowcount = 0", rows=3) is False


def test_duration_threshold():
    assert _ev("duration_ms > 30000", ms=45000) is True
    assert _ev("duration_ms > 30000", ms=1200) is False


def test_all_operators():
    assert _ev("rowcount >= 10", rows=10) is True
    assert _ev("rowcount <= 10", rows=10) is True
    assert _ev("rowcount < 10", rows=10) is False
    assert _ev("rowcount != 0", rows=5) is True
    assert _ev("rowcount <> 0", rows=0) is False


def test_case_and_whitespace_tolerant():
    assert _ev("  ROWCOUNT   >   0 ", rows=4) is True


# ── The safety properties ───────────────────────────────────────────────────


def test_unparseable_condition_does_not_alert():
    # Fail quiet, not loud: a malformed condition that mailed on every run
    # would train people to ignore the alert.
    assert _ev("rowcount is big", rows=100) is False
    assert _ev("1 == 1", rows=100) is False


def test_condition_is_never_evaluated_as_code():
    # The grammar is closed, so none of this can execute or crash.
    for hostile in (
        "__import__('os').system('echo pwned')",
        "rowcount > 0 or True",
        "rowcount > 0; DROP TABLE schedules",
        "() or 1",
    ):
        assert _ev(hostile, rows=5) is False, hostile
        assert is_valid_condition(hostile) is False, hostile


def test_validity_matches_what_evaluates():
    for good in ("rowcount > 0", "duration_ms >= 1000", "", "rowcount != 3"):
        assert is_valid_condition(good) is True, good


# ── The gloss shown in the UI ───────────────────────────────────────────────


def test_description_is_plain_language():
    assert "every run" in describe_condition("")
    assert describe_condition("rowcount > 0") == "Alerts when the row count is more than 0."
    # An unparseable condition must say it will never fire, not stay silent.
    assert "never alert" in describe_condition("nonsense")


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
