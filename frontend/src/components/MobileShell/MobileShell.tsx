import { useEffect, useState } from 'react';
import { AppContext } from '../../App';
import { getSnippets, getHistory, getMyGrants } from '../../services/api';
import { QueryHistoryEntry, SnippetItem } from '../../types';
import { connectionColor } from '../../utils/connectionColor';
import './MobileShell.css';

interface Props {
  ctx: AppContext;
}

type MobileTab = 'queries' | 'history' | 'access';

/**
 * The read-only mobile companion — handoff screen 2J.
 *
 * Deliberately NOT a shrunken desktop app. There is no editor and no way to
 * execute anything: writing SQL on a phone against production is not a thing
 * anyone should be encouraged to do, and the handoff makes read-only the whole
 * premise of this layout.
 *
 * What it is for: checking a saved query's last result, seeing what ran, and
 * seeing what you have access to — from a phone.
 */
function MobileShell({ ctx }: Props) {
  const [tab, setTab] = useState<MobileTab>('queries');
  const [snippets, setSnippets] = useState<SnippetItem[]>([]);
  const [history, setHistory] = useState<QueryHistoryEntry[]>([]);
  const [grants, setGrants] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getSnippets().catch(() => []),
      getHistory(undefined, 50).catch(() => []),
      getMyGrants().catch(() => []),
    ])
      .then(([s, h, g]) => {
        setSnippets(s);
        setHistory(h);
        setGrants(Array.isArray(g) ? g : []);
      })
      .finally(() => setLoading(false));
  }, []);

  const activeServer =
    ctx.servers.find((s) => s.id === ctx.activeQuery?.serverId) || ctx.servers[0] || null;

  return (
    <div className="mob">
      <header className="mob-head">
        <div className="mob-title">
          <span className="mob-wordmark">SQL Studio</span>
          <span className="tag tag-outline">Read only</span>
        </div>
        {activeServer && (
          <div className="mob-conn">
            <span
              className="mob-dot"
              style={{ background: connectionColor(activeServer) }}
            />
            {activeServer.name}
          </div>
        )}
      </header>

      <main className="mob-body">
        {loading && <div className="mob-empty">Loading…</div>}

        {!loading && tab === 'queries' && (
          <>
            {snippets.length === 0 && (
              <div className="mob-empty">
                No saved queries yet. Save one from the desktop app and it shows
                up here.
              </div>
            )}
            {snippets.map((s) => (
              <article key={s.id} className="mob-card">
                <h3>{s.name}</h3>
                <div className="mob-meta">
                  used {s.use_count} time{s.use_count === 1 ? '' : 's'}
                  {s.is_shared && ` · shared by ${s.owner_email.split('@')[0]}`}
                </div>
                <pre className="mob-sql mono">{s.sql}</pre>
              </article>
            ))}
          </>
        )}

        {!loading && tab === 'history' && (
          <>
            {history.length === 0 && <div className="mob-empty">Nothing run yet.</div>}
            {history.map((h) => (
              <article key={h.id} className="mob-card">
                <div className="mob-row">
                  <span className="mob-when mono">
                    {new Date(h.started_at).toLocaleString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                  <span className={h.status === 'error' ? 'mob-bad' : 'mob-num mono'}>
                    {h.status === 'error'
                      ? 'failed'
                      : `${h.row_count.toLocaleString()} rows`}
                  </span>
                </div>
                <pre className="mob-sql mono">{h.sql}</pre>
                {h.error && <div className="mob-bad mob-error">{h.error}</div>}
              </article>
            ))}
          </>
        )}

        {!loading && tab === 'access' && (
          <>
            <div className="mob-kicker">Granted to you</div>
            {grants.length === 0 && (
              <div className="mob-empty">
                No table grants. Revenue Management roles have access without
                individual grants.
              </div>
            )}
            {grants.map((g: any) => (
              <article key={g.id} className="mob-card">
                <div className="mono mob-obj">
                  [{g.database}].[{g.schema_name}].[{g.table_name}]
                </div>
                <div className="mob-meta">granted by {g.granted_by}</div>
              </article>
            ))}
          </>
        )}
      </main>

      <nav className="mob-tabs">
        {(['queries', 'history', 'access'] as MobileTab[]).map((t) => (
          <button
            key={t}
            className={`mob-tab ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'queries' ? 'Queries' : t === 'history' ? 'History' : 'Access'}
          </button>
        ))}
      </nav>
    </div>
  );
}

export default MobileShell;
