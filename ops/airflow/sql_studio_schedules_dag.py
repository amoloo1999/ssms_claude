"""
Anam_SQLStudio_Schedules_DAG
============================
Runs SQL Studio's saved scheduled queries and mails the results.

SQL Studio (http://13-57-123-119.sslip.io:8080, repo amoloo1999/ssms_claude)
lets people save a query and a cadence. The definitions live in that app
because its UI edits them; EXECUTION lives here.

Why here: SQL Studio is a single uvicorn service under nssm on MSSQL01, and
every deploy runs `nssm restart ssms-claude`. An in-process scheduler would
silently drop any run due during a deploy. Airflow already runs continuously,
already has SMTP configured and already alerts on failure.

This DAG holds NO database credentials. It asks the app which schedules are
active, tells the app to run each one (the app re-checks the owner's
permissions and executes as them), and mails whatever comes back.

SETUP — this DAG is shipped PAUSED and will do nothing until all three are done:
  1. SQL Studio must be running a build that has /api/schedules/runner/*
     (PR #16 or later on amoloo1999/ssms_claude).
  2. SCHEDULER_TOKEN=<secret> appended to /c/ssms_claude/backend/.env on
     MSSQL01, then `nssm restart ssms-claude`.
  3. Two Airflow Variables set to match:
         sql_studio_base_url          http://13-57-123-119.sslip.io:8080
         sql_studio_scheduler_token   <the same secret>
Then unpause. Until the token is set the app refuses every runner request —
it fails closed, not open.

Canonical source: amoloo1999/ssms_claude → ops/airflow/sql_studio_schedules_dag.py
Keep the two in step when changing either.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import pendulum
import requests
from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.utils.email import send_email
from croniter import croniter

LOCAL_TZ = pendulum.timezone("America/Los_Angeles")

default_args = {
    "owner": "Anam",
    "depends_on_past": False,
    "start_date": datetime(2026, 8, 1),
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email": ["amoloo@williamwarren.com"],
}

# Every 15 minutes. Fine enough for the cadences people actually set (hourly,
# daily, weekday mornings) without polling the app constantly.
POLL_CADENCE = "*/15 * * * *"
POLL_WINDOW_MINUTES = 15


def _base_url() -> str:
    return Variable.get("sql_studio_base_url").rstrip("/")


def _headers() -> dict:
    return {"X-Scheduler-Token": Variable.get("sql_studio_scheduler_token")}


def _is_due(cadence: str, tz_name: str) -> bool:
    """Whether this schedule's cron fired within the last poll window.

    Cadence matching lives here because Airflow already ships croniter and has
    a clock; doing it in the app too would give us two implementations that can
    disagree about when 'daily at 7' is.
    """
    try:
        tz = pendulum.timezone(tz_name or "America/Los_Angeles")
    except Exception:
        tz = LOCAL_TZ
    now = pendulum.now(tz)
    try:
        previous = croniter(cadence, now).get_prev(datetime)
    except Exception:
        # An unparseable cadence never fires, and says so in the log rather
        # than firing on every poll.
        print(f"  unparseable cadence {cadence!r} — skipping")
        return False
    delta = (now.naive() - previous).total_seconds() / 60.0
    return 0 <= delta < POLL_WINDOW_MINUTES


def _send(schedule_name: str, payload: dict) -> None:
    recipients = payload.get("notify") or []
    if not recipients:
        print(f"  {schedule_name}: nobody to notify — skipping the email")
        return

    if payload.get("error"):
        send_email(
            to=recipients,
            subject=f"[SQL Studio] {schedule_name} failed",
            html_content=(
                f"<p>The scheduled query <b>{schedule_name}</b> failed.</p>"
                f"<pre>{payload['error']}</pre>"
            ),
        )
        return

    rows = payload.get("rows") or []
    columns = payload.get("columns") or []
    count = payload.get("row_count", 0)

    files = None
    if payload.get("attach_csv") and rows:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        writer.writerows(rows)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in schedule_name)
        path = f"/tmp/{safe}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
        files = [path]

    send_email(
        to=recipients,
        subject=f"[SQL Studio] {schedule_name} — {count} rows",
        html_content=(
            f"<p><b>{schedule_name}</b> returned {count} row(s) in "
            f"{int(payload.get('duration_ms', 0))} ms.</p>"
        ),
        files=files,
    )


def run_due_schedules(**_context) -> None:
    base, headers = _base_url(), _headers()

    resp = requests.get(f"{base}/api/schedules/runner/due", headers=headers, timeout=30)
    resp.raise_for_status()
    schedules = resp.json()
    print(f"{len(schedules)} active schedule(s)")

    ran = alerted = failed = 0
    paused: list[str] = []

    for s in schedules:
        if not _is_due(s["cadence"], s.get("timezone", "")):
            continue

        print(f"running {s['name']} (id={s['id']})")
        try:
            r = requests.post(
                f"{base}/api/schedules/runner/{s['id']}/execute",
                headers=headers,
                timeout=600,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            # One schedule failing must not stop the rest of the batch.
            failed += 1
            print(f"  ERROR calling the app for {s['name']}: {exc}")
            continue

        if not payload.get("ran"):
            print(f"  skipped: {payload.get('reason')}")
            if payload.get("paused"):
                # The app pauses a schedule whose owner lost access. Surface it
                # rather than letting it disappear into a log nobody reads.
                paused.append(s["name"])
            continue

        ran += 1
        if payload.get("error"):
            failed += 1
            print(f"  failed: {payload['error']}")
        if payload.get("alert") or payload.get("error"):
            alerted += 1
            _send(s["name"], payload)

    print(f"ran={ran} alerted={alerted} failed={failed} paused={len(paused)}")

    if paused:
        send_email(
            to=default_args["email"],
            subject=f"[SQL Studio] {len(paused)} schedule(s) paused — owner access revoked",
            html_content=(
                "<p>These scheduled queries paused because their owner no longer "
                "has access to what they read:</p><ul>"
                + "".join(f"<li>{name}</li>" for name in paused)
                + "</ul>"
            ),
        )

    # Fail the task when any schedule errored so Airflow's own alerting fires.
    if failed:
        raise RuntimeError(f"{failed} scheduled query/queries failed — see the log above")


dag = DAG(
    "Anam_SQLStudio_Schedules",
    default_args=default_args,
    description="Runs SQL Studio's saved scheduled queries and mails the results",
    schedule=POLL_CADENCE,
    catchup=False,
    max_active_runs=1,
    # Shipped paused on purpose: until SCHEDULER_TOKEN and the two Variables
    # are set, every run would fail and email about it every 15 minutes.
    is_paused_upon_creation=True,
    tags=["Anam", "SQL Studio", "Every-15-Min"],
)

PythonOperator(
    task_id="run_due_schedules",
    python_callable=run_due_schedules,
    execution_timeout=timedelta(minutes=20),
    dag=dag,
)
