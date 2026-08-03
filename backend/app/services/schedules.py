"""Alert-condition evaluation for scheduled queries.

The condition is written by a user and decides whether an email goes out. It is
deliberately NOT eval()'d: a closed grammar of four comparisons over two known
left-hand sides is enough for every case the handoff describes, and it cannot be
turned into code execution no matter what someone types.

Accepted forms (case-insensitive, whitespace-tolerant):

    rowcount > 0        rowcount >= 10      rowcount = 0
    rowcount < 5        rowcount <= 5       rowcount != 0
    duration_ms > 30000

An empty condition means "always alert" — the schedule mails its result every
run, which is the common "send me this report each morning" case.
"""

from __future__ import annotations

import re

_CONDITION_RE = re.compile(
    r"^\s*(rowcount|duration_ms)\s*(>=|<=|!=|<>|=|==|>|<)\s*(\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)

_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "=": lambda a, b: a == b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<>": lambda a, b: a != b,
}


def is_valid_condition(condition: str) -> bool:
    """Whether a condition will be understood. Empty is valid (always alert)."""
    if not (condition or "").strip():
        return True
    return _CONDITION_RE.match(condition) is not None


def describe_condition(condition: str) -> str:
    """A plain-language gloss for the UI, so nobody has to guess what fires."""
    if not (condition or "").strip():
        return "Sends the result every run."
    m = _CONDITION_RE.match(condition)
    if not m:
        return "Not a condition this app understands — it will never alert."
    left, op, right = m.group(1).lower(), m.group(2), m.group(3)
    subject = "the row count" if left == "rowcount" else "the run time in milliseconds"
    words = {
        ">": "is more than",
        "<": "is less than",
        ">=": "is at least",
        "<=": "is at most",
        "=": "equals",
        "==": "equals",
        "!=": "is anything but",
        "<>": "is anything but",
    }[op]
    return f"Alerts when {subject} {words} {right}."


def evaluate_condition(condition: str, *, row_count: int, duration_ms: float) -> bool:
    """Whether this run should alert.

    An unparseable condition returns False rather than True: a schedule that
    silently mails on every run because its condition was malformed is worse
    than one that never mails, because the first trains people to ignore it.
    """
    if not (condition or "").strip():
        return True
    m = _CONDITION_RE.match(condition)
    if not m:
        return False
    left, op, right = m.group(1).lower(), m.group(2), float(m.group(3))
    actual = float(row_count) if left == "rowcount" else float(duration_ms)
    return bool(_OPS[op](actual, right))
