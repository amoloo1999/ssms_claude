"""SQL Studio scheduled queries.

Execution for SQL Studio's saved schedules. Definitions live in the app (the UI
edits them); this DAG decides WHEN each one runs and mails the results.

Why here rather than in the app: SQL Studio is a single uvicorn service under
nssm, and every deploy runs `nssm restart ssms-claude`. An in-process scheduler
would silently drop any run due during a deploy. Airflow already runs on this
box, already has SMTP configured and already alerts on failure.

DEPLOYMENT
    This file is the source of truth and lives with the app it drives, but
    Airflow loads DAGs from the gitflow repo. Copy it to gitflow's dags/ and
    set two Airflow Variables:

        sql_studio_base_url    e.g. http://13-57-123-119.sslip.io:8080
        sql_studio_scheduler_token   must match SCHEDULER_TOKEN in the app's .env

    The token is a shared secret, so set it over SSH into the .env rather than
    through a workflow that logs its inputs.

The app owns permissions and pausing: this DAG asks which schedules are active,
tells the app to run each one, and mails whatever comes back. It deliberately
holds no database credentials of its own.
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

DEFAULT_ARGS = {
    "owner": "revenue-management",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": True,
}

# Every 15 minutes: fine enough for the cadences people actually set (hourly,
# daily, weekday mornings) without polling the app constantly.
POLL_CADENCE = "*/15 * * * *"


def _base_url() -> str:
    return Variable.get("sql_studio_base_url").rstrip("/")


def _headers() -> dict:
    return {"X-Scheduler-Token": Variable.get("sql_studio_scheduler_token")}


def _is_due(cadence: str, tz_name: str, window_minutes: int = 15) -> bool:
    """Whether this schedule's cron fired within the last window.

    Matching happens here because Airflow already has a cron implementation and
    a clock; doing it in the app too would give us two that can disagree.
    """
    try:
        tz = pendulum.timezone(tz_name or "America/Los_Angeles")
    except Exception:
        tz = LOCAL_TZ
    now = pendulum.now(tz)
    try:
        previous = croniter(cadence, now).get_prev(datetime)
    except Exception:
        # A cadence we cannot parse never fires, and says so in the log rather
        # than firing every poll.
        print(f"  unparseable cadence {cadence!r} — skipping")
        return False
    delta = (now.naive() - previous).total_seconds() / 60.0
    return 0 <= delta < window_minutes


def _send(schedule_name: str, payload: dict) -> None:
    recipients = payload.get("notify") or []
    if not recipients:
        return

    rows = payload.get("rows") or []
    columns = payload.get("columns") or []
    count = payload.get("row_count", 0)

    if payload.get("error"):
        subject = f"[SQL Studio] {schedule_name} failed"
        body = (
            f"<p>The scheduled query <b>{schedule_name}</b> failed.</p>"
            f"<pre>{payload['error']}</pre>"
        )
        send_email(to=recipients, subject=subject, html_content=body)
        return

    subject = f"[SQL Studio] {schedule_name} — {count} rows"
    body = (
        f"<p><b>{schedule_name}</b> returned {count} row(s) "
        f"in {int(payload.get('duration_ms', 0))} ms.</p>"
    )

    files = None
    if payload.get("attach_csv") and rows:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        writer.writerows(rows)
        # Airflow's send_email takes file paths, so the CSV is written to the
        # worker's tmp and handed over by path.
        path = f"/tmp/{schedule_name.replace(' ', '_')}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
        files = [path]

    send_email(to=recipients, subject=subject, html_content=body, files=files)


def run_due_schedules(**_context) -> None:
    base, headers = _base_url(), _headers()

    resp = requests.get(f"{base}/api/schedules/runner/due", headers=headers, timeout=30)
    resp.raise_for_status()
    schedules = resp.json()
    print(f"{len(schedules)} active schedule(s)")

    ran = alerted = failed = 0
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
            # One schedule's failure must not stop the rest of the batch.
            failed += 1
            print(f"  ERROR calling the app for {s['name']}: {exc}")
            continue

        if not payload.get("ran"):
            print(f"  skipped: {payload.get('reason')}")
            if payload.get("paused"):
                # The app pauses a schedule whose owner lost access. Surface it
                # rather than letting it disappear quietly.
                print(f"  PAUSED — {s['name']} owner access revoked")
            continue

        ran += 1
        if payload.get("error"):
            failed += 1
            print(f"  failed: {payload['error']}")
        if payload.get("alert") or payload.get("error"):
            alerted += 1
            _send(s["name"], payload)

    print(f"ran={ran} alerted={alerted} failed={failed}")
    # Fail the task when any schedule errored, so Airflow's own alerting fires
    # rather than this going unnoticed in a log nobody reads.
    if failed:
        raise RuntimeError(f"{failed} scheduled query/queries failed — see the log above")


with DAG(
    dag_id="sql_studio_schedules",
    description="Runs SQL Studio's saved scheduled queries and mails the results",
    default_args=DEFAULT_ARGS,
    schedule=POLL_CADENCE,
    start_date=datetime(2026, 8, 1, tzinfo=LOCAL_TZ),
    catchup=False,
    max_active_runs=1,
    tags=["sql-studio", "reporting"],
) as dag:
    PythonOperator(
        task_id="run_due_schedules",
        python_callable=run_due_schedules,
    )
