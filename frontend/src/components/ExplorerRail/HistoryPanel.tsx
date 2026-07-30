import { useEffect, useState } from 'react';
import { AppContext } from '../../App';
import { getHistory, clearHistory } from '../../services/api';
import { QueryHistoryEntry } from '../../types';
import { connectionColor } from '../../utils/connectionColor';

interface Props {
  ctx: AppContext;
}

/** Group label for a run — Today / Yesterday / the date. */
function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (same(d, today)) return 'Today';
  if (same(d, yesterday)) return 'Yesterday';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

const timeOf = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });

/** Collapse a statement to one line so the rail stays scannable. */
const oneLine = (sql: string) => sql.replace(/\s+/g, ' ').trim();

/**
 * Query history — handoff screen 3A, left rail.
 *
 * Shows only the caller's own runs; the endpoint is scoped by user and there is
 * no all-users view, because SQL text names objects the reader may have no
 * grant for. Failed runs are kept and show their reason.
 */
function HistoryPanel({ ctx }: Props) {
  const [entries, setEntries] = useState<QueryHistoryEntry[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);

  const load = (q: string) => {
    setLoading(true);
    getHistory(q || undefined)
      .then(setEntries)
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    const t = setTimeout(() => load(search), search ? 250 : 0);
    return () => clearTimeout(t);
  }, [search]);

  const openInTab = (e: QueryHistoryEntry) => {
    ctx.setPendingQuery({ serverId: e.server_id, database: e.database, sql: e.sql });
    ctx.setActiveTab('query');
  };

  const handleClear = async () => {
    if (!confirm('Clear your query history? This only affects your own runs.')) return;
    await clearHistory();
    load(search);
  };

  let lastDay = '';

  return (
    <div className="rail-panel">
      <div className="rail-tools">
        <input
          className="input"
          value={search}
          placeholder={`Search ${entries.length ? `${entries.length} ` : ''}runs…`}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="rail-list">
        {loading && <div className="rail-empty">Loading…</div>}
        {!loading && entries.length === 0 && (
          <div className="rail-empty">
            {search ? 'No runs match that search.' : 'No queries run yet.'}
          </div>
        )}

        {entries.map((e) => {
          const day = dayLabel(e.started_at);
          const showDay = day !== lastDay;
          lastDay = day;
          const server = ctx.servers.find((s) => s.id === e.server_id) || null;
          return (
            <div key={e.id}>
              {showDay && <div className="rail-kicker">{day}</div>}
              <div
                className={`hist-row ${selected === e.id ? 'selected' : ''}`}
                onClick={() => setSelected(e.id)}
                onDoubleClick={() => openInTab(e)}
                title="Double-click to open in a new tab"
              >
                <div className="hist-head">
                  <span className="hist-time mono">{timeOf(e.started_at)}</span>
                  <span className="hist-sql mono">{oneLine(e.sql)}</span>
                </div>
                <div className="hist-meta">
                  <span
                    className="hist-conn"
                    style={{ color: server ? connectionColor(server) : undefined }}
                  >
                    {e.server_name || `server ${e.server_id}`}
                  </span>
                  {e.status === 'error' ? (
                    <span className="hist-error">{e.error || 'failed'}</span>
                  ) : (
                    <span>
                      {e.row_count.toLocaleString()} rows · {Math.round(e.duration_ms)} ms
                    </span>
                  )}
                </div>
                {selected === e.id && (
                  <div className="hist-actions">
                    <button className="btn btn-primary" onClick={() => openInTab(e)}>
                      Open in tab
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="rail-footer">
        <span>Your runs only · never leaves the tenant</span>
        {entries.length > 0 && (
          <button className="rail-link" onClick={handleClear}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

export default HistoryPanel;
