import { useEffect, useState } from 'react';
import { AppContext } from '../App';
import {
  getSchedules,
  updateSchedule,
  deleteSchedule,
  getScheduleRuns,
  createSchedule,
} from '../services/api';
import './OpsPages.css';

interface Props {
  ctx: AppContext;
}

interface Schedule {
  id: number;
  owner_email: string;
  name: string;
  server_id: number;
  database: string;
  sql: string;
  cadence: string;
  timezone: string;
  alert_condition: string;
  notify_emails: string;
  attach_csv: boolean;
  state: 'active' | 'paused';
  paused_reason: string;
  last_run_at: string | null;
  last_result: string;
}

interface Run {
  id: number;
  started_at: string;
  status: string;
  row_count: number;
  duration_ms: number;
  alerted: boolean;
  error: string | null;
}

/**
 * Mirrors backend services/schedules.describe_condition so the UI never claims
 * an alert fires differently from how the runner actually evaluates it.
 */
function describeCondition(condition: string): string {
  const c = (condition || '').trim();
  if (!c) return 'Sends the result every run.';
  const m = c.match(/^(rowcount|duration_ms)\s*(>=|<=|!=|<>|=|==|>|<)\s*(\d+(?:\.\d+)?)$/i);
  if (!m) return 'Not a condition the app understands — this will never alert.';
  const subject = m[1].toLowerCase() === 'rowcount' ? 'the row count' : 'the run time in ms';
  const words: Record<string, string> = {
    '>': 'is more than',
    '<': 'is less than',
    '>=': 'is at least',
    '<=': 'is at most',
    '=': 'equals',
    '==': 'equals',
    '!=': 'is anything but',
    '<>': 'is anything but',
  };
  return `Alerts when ${subject} ${words[m[2]]} ${m[3]}.`;
}

const fmtWhen = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : 'never';

/** Scheduled queries and alerts — handoff screen 4D. */
function SchedulesPage({ ctx }: Props) {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    getSchedules()
      .then(setSchedules)
      .catch(() => setSchedules([]))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  useEffect(() => {
    if (selected === null) return;
    getScheduleRuns(selected).then(setRuns).catch(() => setRuns([]));
  }, [selected]);

  const mine = ctx.user?.email;
  const current = schedules.find((s) => s.id === selected) || null;

  const togglePause = async (s: Schedule) => {
    try {
      await updateSchedule(s.id, { state: s.state === 'active' ? 'paused' : 'active' });
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const remove = async (s: Schedule) => {
    if (!confirm(`Delete the schedule "${s.name}"? Its run history goes too.`)) return;
    try {
      await deleteSchedule(s.id);
      setSelected(null);
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const addFromCurrentTab = async () => {
    const target = ctx.activeQuery;
    if (!target?.serverId || !target.database) {
      alert('Pick a server and database in the query editor first.');
      return;
    }
    const name = window.prompt('Name this schedule:');
    if (!name) return;
    const sql = window.prompt('SQL to run on a cadence:');
    if (!sql) return;
    const cadence = window.prompt('Cadence (cron, e.g. "0 7 * * *" for 7am daily):', '0 7 * * *');
    if (!cadence) return;
    try {
      await createSchedule({
        name,
        server_id: target.serverId,
        database: target.database,
        sql,
        cadence,
        notify_emails: mine || '',
      });
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const maxRows = Math.max(...runs.map((r) => r.row_count), 1);

  return (
    <div className="ops-page">
      <div className="ops-header">
        <h2>Scheduled queries</h2>
        <button className="btn btn-primary" onClick={addFromCurrentTab}>
          New schedule
        </button>
      </div>

      <div className="sched-split">
        <div className="ops-table sched-list">
          <div className="ops-row sched-row ops-head">
            <span>Name</span>
            <span>Cadence</span>
            <span>Last run</span>
            <span>Result</span>
            <span>State</span>
          </div>

          {loading && <div className="ops-empty">Loading…</div>}
          {!loading && schedules.length === 0 && (
            <div className="ops-empty">
              No schedules yet. A schedule runs a saved query on a cadence and
              mails the result — useful for the checks you would otherwise
              remember to run each morning.
            </div>
          )}

          {schedules.map((s) => (
            <div
              key={s.id}
              className={`ops-row sched-row ${selected === s.id ? 'selected' : ''} ${
                s.state === 'paused' ? 'idle' : ''
              } ${s.last_result && s.last_result.includes('Error') ? 'blocker' : ''}`}
              onClick={() => setSelected(s.id)}
            >
              <span className="ops-user">
                <span className="ops-login">{s.name}</span>
                <span className="ops-stmt">
                  {ctx.servers.find((x) => x.id === s.server_id)?.name || `server ${s.server_id}`}
                  {s.owner_email !== mine && ` · ${s.owner_email.split('@')[0]}`}
                </span>
              </span>
              <span className="mono">{s.cadence}</span>
              <span className="ops-muted">{fmtWhen(s.last_run_at)}</span>
              <span className="ops-muted">{s.last_result || '—'}</span>
              <span className={s.state === 'paused' ? 'ops-danger' : 'ops-muted'}>{s.state}</span>
            </div>
          ))}
        </div>

        {current && (
          <div className="sched-detail">
            <div className="sched-detail-head">{current.name}</div>

            {current.state === 'paused' && current.paused_reason && (
              <div className="sched-paused">
                <strong>Paused.</strong> {current.paused_reason}
              </div>
            )}

            <div className="sched-field">
              <span className="ops-stat-label">Runs</span>
              <span className="mono">
                {current.cadence} · {current.timezone}
              </span>
            </div>

            <div className="sched-field">
              <span className="ops-stat-label">Alert when</span>
              <span className="mono">{current.alert_condition || '(every run)'}</span>
            </div>
            <div className="sched-gloss">{describeCondition(current.alert_condition)}</div>

            <div className="sched-field">
              <span className="ops-stat-label">Notify</span>
              <span>{current.notify_emails || 'nobody — this will run silently'}</span>
            </div>

            <div className="sched-field">
              <span className="ops-stat-label">Last 20 runs</span>
            </div>
            {runs.length === 0 ? (
              <div className="ops-muted sched-gloss">No runs recorded yet.</div>
            ) : (
              <div className="sched-spark">
                {[...runs].reverse().map((r) => (
                  <span
                    key={r.id}
                    className={`sched-bar ${r.status !== 'ok' ? 'bad' : ''} ${
                      r.alerted ? 'alerted' : ''
                    }`}
                    style={{ height: `${Math.max((r.row_count / maxRows) * 100, 6)}%` }}
                    title={`${fmtWhen(r.started_at)} · ${r.status} · ${r.row_count} rows${
                      r.error ? ` · ${r.error}` : ''
                    }`}
                  />
                ))}
              </div>
            )}

            <div className="sched-actions">
              <button className="btn btn-secondary" onClick={() => togglePause(current)}>
                {current.state === 'active' ? 'Pause' : 'Resume'}
              </button>
              {current.owner_email === mine && (
                <button className="btn btn-danger" onClick={() => remove(current)}>
                  Delete
                </button>
              )}
            </div>

            <pre className="sched-sql mono">{current.sql}</pre>
          </div>
        )}
      </div>

      <div className="ops-footer">
        Schedules run as their owner and are permission-checked on every run. If
        the owner loses access the schedule pauses and says so, rather than
        failing silently. Execution is handled by Airflow, so a deploy of this
        app never drops a run.
      </div>
    </div>
  );
}

export default SchedulesPage;
