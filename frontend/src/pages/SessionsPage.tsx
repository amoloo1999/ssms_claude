import { useEffect, useState } from 'react';
import { AppContext } from '../App';
import { getSessions, killSession } from '../services/api';
import { connectionColor } from '../utils/connectionColor';
import './OpsPages.css';

interface Props {
  ctx: AppContext;
}

interface Session {
  session_id: number;
  login: string;
  host: string;
  program: string;
  database: string;
  status: string;
  blocked_by: number;
  wait_type: string;
  elapsed_ms: number;
  open_transactions: number;
  statement: string;
  blocking_count: number;
  is_blocked: boolean;
}

const fmtElapsed = (ms: number): string => {
  if (!ms) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
};

/**
 * Sessions and locks — handoff screen 4E.
 *
 * RevMan-only, enforced server-side: the statement column is other people's SQL
 * and on production can contain customer data in literals.
 */
function SessionsPage({ ctx }: Props) {
  const [serverId, setServerId] = useState<number | null>(ctx.activeQuery?.serverId ?? null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [supported, setSupported] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  const server = ctx.servers.find((s) => s.id === serverId) || null;

  const load = () => {
    if (!serverId) return;
    setLoading(true);
    setError(null);
    getSessions(serverId)
      .then((res) => {
        setSupported(res.supported !== false);
        setSessions(res.sessions || []);
        if (res.error) setError(res.error);
      })
      .catch((err) => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [serverId]);

  useEffect(() => {
    if (!serverId) setServerId(ctx.servers[0]?.id ?? null);
  }, [ctx.servers]);

  const handleKill = async (s: Session) => {
    if (!serverId) return;
    const reason = window.prompt(
      `Kill SPID ${s.session_id} (${s.login})?\n\n` +
        'This rolls back any transaction it has open. The reason below is recorded ' +
        'with your name and cannot be edited afterwards.\n\nReason (10+ characters):'
    );
    if (reason === null) return;
    try {
      await killSession(serverId, s.session_id, reason);
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const blockers = sessions.filter((s) => s.blocking_count > 0);
  const running = sessions.filter((s) => s.status === 'running').length;
  const openTx = sessions.filter((s) => s.open_transactions > 0).length;
  const blocked = sessions.filter((s) => s.is_blocked).length;

  return (
    <div className="ops-page" style={{ ['--conn-active' as string]: connectionColor(server) }}>
      <div className="ops-header">
        <h2>Sessions and locks</h2>
        <div className="ops-header-actions">
          <select
            className="ops-select"
            value={serverId ?? ''}
            onChange={(e) => setServerId(Number(e.target.value) || null)}
          >
            {ctx.servers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <button className="btn btn-secondary" onClick={load} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {!supported ? (
        <div className="ops-empty">
          The session monitor isn’t available for this connection’s engine yet —
          only SQL Server so far.
        </div>
      ) : error ? (
        <div className="ops-error mono">{error}</div>
      ) : (
        <>
          <div className="ops-stats">
            <div className="ops-stat">
              <span className="ops-stat-label">Sessions</span>
              <span className="ops-stat-value mono">{sessions.length}</span>
            </div>
            <div className="ops-stat">
              <span className="ops-stat-label">Running now</span>
              <span className="ops-stat-value mono">{running}</span>
            </div>
            <div className="ops-stat">
              <span className="ops-stat-label">Blocked</span>
              <span className={`ops-stat-value mono ${blocked ? 'danger' : ''}`}>{blocked}</span>
            </div>
            <div className="ops-stat">
              <span className="ops-stat-label">Open transactions</span>
              <span className="ops-stat-value mono">{openTx}</span>
            </div>
          </div>

          {blockers.length > 0 && (
            <div className="ops-chain">
              {blockers.map((b) => (
                <div key={b.session_id} className="ops-chain-card">
                  <div className="ops-chain-head">
                    <span className="tag tag-danger">head blocker</span>
                    <span className="mono">SPID {b.session_id}</span>
                    <span className="ops-muted">
                      {b.login} · blocking {b.blocking_count}
                    </span>
                  </div>
                  <div className="ops-chain-body">
                    {b.open_transactions > 0
                      ? `It has ${b.open_transactions} transaction${
                          b.open_transactions === 1 ? '' : 's'
                        } open and is holding locks the waiters need.`
                      : 'It is holding locks the waiting sessions need.'}{' '}
                    Killing it rolls that work back.
                  </div>
                  {ctx.user?.is_approver && (
                    <button className="btn btn-danger" onClick={() => handleKill(b)}>
                      Kill SPID {b.session_id}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="ops-table">
            <div className="ops-row ops-head">
              <span>SPID</span>
              <span>User / statement</span>
              <span>Database</span>
              <span className="ops-num">Elapsed</span>
              <span>State</span>
            </div>
            {sessions.length === 0 && !loading && (
              <div className="ops-empty">No user sessions on this server.</div>
            )}
            {sessions.map((s) => (
              <div
                key={s.session_id}
                className={`ops-row ${s.blocking_count > 0 ? 'blocker' : ''} ${
                  s.is_blocked ? 'blocked' : ''
                } ${s.status === 'sleeping' ? 'idle' : ''} ${
                  selected === s.session_id ? 'selected' : ''
                }`}
                onClick={() => setSelected(s.session_id)}
              >
                <span className="mono">{s.session_id}</span>
                <span className="ops-user">
                  <span className="ops-login">{s.login}</span>
                  <span className="ops-stmt mono">{s.statement || s.program || '—'}</span>
                </span>
                <span className="mono">{s.database}</span>
                <span className="ops-num mono">{fmtElapsed(s.elapsed_ms)}</span>
                <span>
                  {s.blocking_count > 0 ? (
                    <span className="ops-danger">blocking {s.blocking_count}</span>
                  ) : s.is_blocked ? (
                    <span className="ops-danger">blocked</span>
                  ) : (
                    <span className="ops-muted">{s.status}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="ops-footer">
        {ctx.user?.is_approver
          ? 'Killing a session is recorded with your name and a required reason, and cannot be undone.'
          : 'Only approvers can end a session.'}
      </div>
    </div>
  );
}

export default SessionsPage;
