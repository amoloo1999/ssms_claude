import { useEffect, useState } from 'react';
import { getAudit } from '../services/api';
import './OpsPages.css';

interface AuditRow {
  id: number;
  at: string;
  actor: string;
  actor_kind: string;
  event_type: string;
  server_name: string;
  database: string;
  detail: string;
  reason: string;
  result: string;
}

const TYPES = ['', 'write', 'export', 'grant', 'deny', 'kill', 'denied'];

const TYPE_LABEL: Record<string, string> = {
  '': 'All events',
  write: 'Writes',
  export: 'Exports',
  grant: 'Grants',
  deny: 'Denials',
  kill: 'Kills',
  denied: 'Refused queries',
};

const fmtWhen = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

/**
 * Audit log — handoff screen 4F.
 *
 * Approver-only and read-only: there is no delete endpoint, so the log is
 * append-only by construction rather than by convention.
 */
function AuditPage() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [type, setType] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    const t = setTimeout(
      () => {
        setLoading(true);
        getAudit({ event_type: type || undefined, search: search || undefined })
          .then(setRows)
          .catch(() => setRows([]))
          .finally(() => setLoading(false));
      },
      search ? 250 : 0
    );
    return () => clearTimeout(t);
  }, [type, search]);

  const count = (t: string) => rows.filter((r) => r.event_type === t).length;

  return (
    <div className="ops-page">
      <div className="ops-header">
        <h2>Audit log</h2>
        <div className="ops-header-actions">
          <input
            className="input ops-search"
            value={search}
            placeholder="Search actor or detail…"
            onChange={(e) => setSearch(e.target.value)}
          />
          <select className="ops-select" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="ops-stats">
        <div className="ops-stat">
          <span className="ops-stat-label">Events</span>
          <span className="ops-stat-value mono">{rows.length}</span>
        </div>
        <div className="ops-stat">
          <span className="ops-stat-label">Writes</span>
          <span className="ops-stat-value mono">{count('write')}</span>
        </div>
        <div className="ops-stat">
          <span className="ops-stat-label">Exports</span>
          <span className="ops-stat-value mono">{count('export')}</span>
        </div>
        <div className="ops-stat">
          <span className="ops-stat-label">Kills</span>
          <span className={`ops-stat-value mono ${count('kill') ? 'danger' : ''}`}>
            {count('kill')}
          </span>
        </div>
        <div className="ops-stat">
          <span className="ops-stat-label">Refused</span>
          <span className={`ops-stat-value mono ${count('denied') ? 'danger' : ''}`}>
            {count('denied')}
          </span>
        </div>
      </div>

      <div className="ops-table">
        <div className="ops-row audit-row ops-head">
          <span>When</span>
          <span>Actor</span>
          <span>Event</span>
          <span>Detail</span>
          <span>Result</span>
        </div>

        {loading && <div className="ops-empty">Loading…</div>}
        {!loading && rows.length === 0 && (
          <div className="ops-empty">
            No events recorded yet. Writes, exports, grants, denials and kills
            appear here as they happen.
          </div>
        )}

        {rows.map((r) => {
          const bad = r.result !== 'ok' || r.event_type === 'kill' || r.event_type === 'denied';
          return (
            <div
              key={r.id}
              className={`ops-row audit-row ${bad ? 'blocker' : ''} ${
                expanded === r.id ? 'expanded' : ''
              }`}
              onClick={() => setExpanded(expanded === r.id ? null : r.id)}
            >
              <span className="mono ops-muted">{fmtWhen(r.at)}</span>
              <span>
                {r.actor}
                {r.actor_kind !== 'user' && (
                  <span className="tag tag-neutral audit-kind">{r.actor_kind}</span>
                )}
              </span>
              <span className={`audit-type ${bad ? 'danger' : ''}`}>{r.event_type}</span>
              <span className={`mono audit-detail ${expanded === r.id ? 'full' : ''}`}>
                {r.detail}
                {r.reason && expanded === r.id && (
                  <span className="audit-reason">Reason: {r.reason}</span>
                )}
              </span>
              <span className={r.result === 'ok' ? 'ops-muted' : 'ops-danger'}>{r.result}</span>
            </div>
          );
        })}
      </div>

      <div className="ops-footer">
        Click a row to see the full statement and reason. The log is append-only —
        there is no way to edit or delete an entry from this app.
      </div>
    </div>
  );
}

export default AuditPage;
