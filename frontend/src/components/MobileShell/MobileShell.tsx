import { useEffect, useState } from 'react';
import { AppContext } from '../../App';
import {
  getSnippets,
  getHistory,
  getMyGrants,
  getDatabases,
  getSchemaSnapshot,
  executeQuery,
} from '../../services/api';
import { QueryHistoryEntry, QueryResult, Server, SnippetItem } from '../../types';
import { connectionColor } from '../../utils/connectionColor';
import { isReadOnlySql, writeVerb } from '../../utils/readOnlySql';
import './MobileShell.css';

interface Props {
  ctx: AppContext;
}

type MobileTab = 'queries' | 'history' | 'schema' | 'access';

/** What's open on top of the tab: a result, or the connection picker. */
type Overlay =
  | { kind: 'none' }
  | { kind: 'connection' }
  | { kind: 'result'; title: string; sql: string };

/**
 * The mobile companion — handoff screen 2J.
 *
 * READ-ONLY means no writes, not no execution. It runs a saved query and shows
 * its rows; it refuses anything that isn't a plain read, and there is no editor,
 * so nothing new can be composed here. That distinction is the whole point of
 * the screen: check a result from your phone, don't administer a database from it.
 */
function MobileShell({ ctx }: Props) {
  const [tab, setTab] = useState<MobileTab>('queries');
  const [overlay, setOverlay] = useState<Overlay>({ kind: 'none' });

  const [snippets, setSnippets] = useState<SnippetItem[]>([]);
  const [history, setHistory] = useState<QueryHistoryEntry[]>([]);
  const [grants, setGrants] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  // ── connection ────────────────────────────────────────────────────────────
  const [serverId, setServerId] = useState<number | null>(ctx.activeQuery?.serverId ?? null);
  const [database, setDatabase] = useState<string>(ctx.activeQuery?.database ?? '');
  const [databases, setDatabases] = useState<string[]>([]);

  const server = ctx.servers.find((s) => s.id === serverId) || null;

  useEffect(() => {
    if (serverId == null && ctx.servers.length) setServerId(ctx.servers[0].id);
  }, [ctx.servers]);

  useEffect(() => {
    if (serverId == null) return;
    getDatabases(serverId)
      .then((res: any) => {
        const dbs: string[] = res.databases || [];
        setDatabases(dbs);
        if (!dbs.includes(database)) {
          setDatabase(dbs.find((d) => d.toLowerCase() !== 'master') || dbs[0] || '');
        }
      })
      .catch(() => setDatabases([]));
  }, [serverId]);

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

  const openResult = (title: string, sql: string) => {
    const verb = writeVerb(sql);
    if (verb) {
      alert(
        `This query contains ${verb}, and the mobile view never writes.\n\n` +
          'Open it on the desktop app if you meant to run it.'
      );
      return;
    }
    if (!serverId || !database) {
      alert('Pick a connection first — tap the server name at the top.');
      return;
    }
    setOverlay({ kind: 'result', title, sql });
  };

  return (
    <div className="mob">
      <header className="mob-head">
        <div className="mob-title">
          <span className="mob-wordmark">SQL Studio</span>
          <span className="tag tag-outline">Read only</span>
        </div>
        {/* The connection is a control, not a label — switching servers was the
            first thing missing when this shipped without one. */}
        <button className="mob-conn" onClick={() => setOverlay({ kind: 'connection' })}>
          <span className="mob-dot" style={{ background: connectionColor(server) }} />
          <span className="mob-conn-name">{server ? server.name : 'Pick a connection'}</span>
          {database && <span className="mob-conn-db mono">{database}</span>}
          <span className="mob-chev">▾</span>
        </button>
      </header>

      <main className="mob-body">
        {loading && <div className="mob-empty">Loading…</div>}

        {!loading && tab === 'queries' && (
          <QueriesTab snippets={snippets} onOpen={openResult} me={ctx.user?.email} />
        )}
        {!loading && tab === 'history' && <HistoryTab history={history} onOpen={openResult} />}
        {!loading && tab === 'schema' && <SchemaTab serverId={serverId} database={database} />}
        {!loading && tab === 'access' && <AccessTab grants={grants} />}
      </main>

      <nav className="mob-tabs">
        {(['queries', 'history', 'schema', 'access'] as MobileTab[]).map((t) => (
          <button
            key={t}
            className={`mob-tab ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'queries'
              ? 'Queries'
              : t === 'history'
              ? 'History'
              : t === 'schema'
              ? 'Schema'
              : 'Access'}
          </button>
        ))}
      </nav>

      {overlay.kind === 'connection' && (
        <ConnectionSheet
          servers={ctx.servers}
          serverId={serverId}
          database={database}
          databases={databases}
          onPickServer={(id) => setServerId(id)}
          onPickDatabase={(db) => {
            setDatabase(db);
            setOverlay({ kind: 'none' });
          }}
          onClose={() => setOverlay({ kind: 'none' })}
        />
      )}

      {overlay.kind === 'result' && serverId && (
        <ResultView
          title={overlay.title}
          sql={overlay.sql}
          serverId={serverId}
          database={database}
          onClose={() => setOverlay({ kind: 'none' })}
        />
      )}
    </div>
  );
}

/* ── Queries ─────────────────────────────────────────────────────────────── */

function QueriesTab({
  snippets,
  onOpen,
  me,
}: {
  snippets: SnippetItem[];
  onOpen: (title: string, sql: string) => void;
  me?: string;
}) {
  if (snippets.length === 0) {
    return (
      <div className="mob-empty">
        No saved queries yet. Save one from the desktop app and it appears here,
        ready to run.
      </div>
    );
  }
  const mine = snippets.filter((s) => s.owner_email === me);
  const shared = snippets.filter((s) => s.owner_email !== me);

  const card = (s: SnippetItem) => {
    const readable = isReadOnlySql(s.sql);
    return (
      <article key={s.id} className="mob-card">
        <h3>{s.name}</h3>
        <div className="mob-meta">
          used {s.use_count} time{s.use_count === 1 ? '' : 's'}
          {s.owner_email !== me && ` · ${s.owner_email.split('@')[0]}`}
        </div>
        <pre className="mob-sql mono">{s.sql}</pre>
        <div className="mob-actions">
          <button className="mob-btn primary" onClick={() => onOpen(s.name, s.sql)}>
            Run
          </button>
          {!readable && <span className="mob-note">Contains a write — desktop only</span>}
        </div>
      </article>
    );
  };

  return (
    <>
      {mine.map(card)}
      {shared.length > 0 && <div className="mob-kicker">Shared with me</div>}
      {shared.map(card)}
    </>
  );
}

/* ── History ─────────────────────────────────────────────────────────────── */

function HistoryTab({
  history,
  onOpen,
}: {
  history: QueryHistoryEntry[];
  onOpen: (title: string, sql: string) => void;
}) {
  if (history.length === 0) return <div className="mob-empty">Nothing run yet.</div>;
  return (
    <>
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
              {h.status === 'error' ? 'failed' : `${h.row_count.toLocaleString()} rows`}
            </span>
          </div>
          <pre className="mob-sql mono">{h.sql}</pre>
          {h.error && <div className="mob-bad mob-error">{h.error}</div>}
          {h.status !== 'error' && (
            <div className="mob-actions">
              <button className="mob-btn" onClick={() => onOpen('Re-run', h.sql)}>
                Re-run
              </button>
            </div>
          )}
        </article>
      ))}
    </>
  );
}

/* ── Schema ──────────────────────────────────────────────────────────────── */

function SchemaTab({ serverId, database }: { serverId: number | null; database: string }) {
  const [tables, setTables] = useState<{ schema: string; name: string; kind?: string }[]>([]);
  const [columns, setColumns] = useState<{ schema: string; table: string; name: string; type: string }[]>([]);
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!serverId || !database) return;
    setLoading(true);
    getSchemaSnapshot(serverId, database)
      .then((snap: any) => {
        setTables(snap?.tables || []);
        setColumns(snap?.columns || []);
      })
      .catch(() => setTables([]))
      .finally(() => setLoading(false));
  }, [serverId, database]);

  if (!serverId || !database) {
    return <div className="mob-empty">Pick a connection to browse its tables.</div>;
  }
  if (loading) return <div className="mob-empty">Loading schema…</div>;

  const visible = tables.filter(
    (t) => !search || `${t.schema}.${t.name}`.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <>
      <input
        className="mob-search"
        value={search}
        placeholder="Search tables…"
        onChange={(e) => setSearch(e.target.value)}
      />
      {visible.length === 0 && <div className="mob-empty">No tables match.</div>}
      {visible.slice(0, 200).map((t) => {
        const key = `${t.schema}.${t.name}`;
        const isOpen = open === key;
        const cols = isOpen
          ? columns.filter((c) => c.schema === t.schema && c.table === t.name)
          : [];
        return (
          <div key={key} className="mob-tablerow">
            <button className="mob-tablebtn" onClick={() => setOpen(isOpen ? null : key)}>
              <span className="mono mob-obj">{key}</span>
              <span className="mob-chev">{isOpen ? '▾' : '›'}</span>
            </button>
            {isOpen && (
              <div className="mob-cols">
                {cols.length === 0 && <div className="mob-meta">No column detail cached.</div>}
                {cols.map((c) => (
                  <div key={c.name} className="mob-col mono">
                    {c.name}
                    <span className="mob-coltype">{c.type}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

/* ── Access ──────────────────────────────────────────────────────────────── */

function AccessTab({ grants }: { grants: any[] }) {
  if (grants.length === 0) {
    return (
      <div className="mob-empty">
        No individual table grants. Revenue Management roles have access without
        them.
      </div>
    );
  }
  return (
    <>
      <div className="mob-kicker">Granted to you</div>
      {grants.map((g: any) => (
        <article key={g.id} className="mob-card">
          <div className="mono mob-obj">
            [{g.database}].[{g.schema_name}].[{g.table_name}]
          </div>
          <div className="mob-meta">granted by {g.granted_by}</div>
        </article>
      ))}
    </>
  );
}

/* ── Connection picker ───────────────────────────────────────────────────── */

function ConnectionSheet({
  servers,
  serverId,
  database,
  databases,
  onPickServer,
  onPickDatabase,
  onClose,
}: {
  servers: Server[];
  serverId: number | null;
  database: string;
  databases: string[];
  onPickServer: (id: number) => void;
  onPickDatabase: (db: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="mob-sheet-backdrop" onClick={onClose}>
      <div className="mob-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="mob-sheet-head">Connection</div>
        <div className="mob-kicker">Server</div>
        {servers.map((s) => (
          <button
            key={s.id}
            className={`mob-pick ${s.id === serverId ? 'active' : ''}`}
            onClick={() => onPickServer(s.id)}
          >
            <span className="mob-dot" style={{ background: connectionColor(s) }} />
            {s.name}
            {s.write_policy === 'read_only' && <span className="tag tag-neutral">read-only</span>}
          </button>
        ))}
        <div className="mob-kicker">Database</div>
        {databases.length === 0 && <div className="mob-meta">Loading databases…</div>}
        {databases.map((db) => (
          <button
            key={db}
            className={`mob-pick ${db === database ? 'active' : ''}`}
            onClick={() => onPickDatabase(db)}
          >
            <span className="mono">{db}</span>
          </button>
        ))}
        <button className="mob-btn mob-close" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/* ── Result detail ───────────────────────────────────────────────────────── */

function ResultView({
  title,
  sql,
  serverId,
  database,
  onClose,
}: {
  title: string;
  sql: string;
  serverId: number;
  database: string;
  onClose: () => void;
}) {
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'rows' | 'sql'>('rows');
  const [running, setRunning] = useState(true);

  const run = () => {
    setRunning(true);
    setError(null);
    executeQuery(serverId, database, sql)
      .then((r: QueryResult) => {
        setResult(r);
        if (r.error) setError(r.error);
      })
      .catch((err: any) =>
        setError(err.response?.data?.detail?.detail || err.response?.data?.detail || err.message)
      )
      .finally(() => setRunning(false));
  };

  useEffect(run, [sql, serverId, database]);

  const cols = result?.columns || [];
  const rows = result?.rows || [];
  // With no room for a grid, each record becomes a two-line block: the first
  // column identifies it, and one figure gets the right-hand slot.
  //
  // That figure is the FIRST numeric column after the label, not the last:
  // people put the measure they care about early (SiteName, Revenue,
  // Occupancy — Revenue is the point), and the remaining columns still appear
  // on the sub-line.
  const figureIdx = (() => {
    for (let i = 1; i < cols.length; i++) {
      if (rows.some((r) => typeof r[i] === 'number')) return i;
    }
    return cols.length > 1 ? 1 : 0;
  })();

  return (
    <div className="mob-result">
      <header className="mob-result-head">
        <button className="mob-back" onClick={onClose} aria-label="Back">
          ‹
        </button>
        <div className="mob-result-title">
          <span>{title}</span>
          <span className="mob-meta">
            {running
              ? 'Running…'
              : error
              ? 'Failed'
              : `${rows.length.toLocaleString()} rows · ${Math.round(
                  result?.execution_time_ms || 0
                )} ms`}
          </span>
        </div>
      </header>

      <div className="mob-result-tabs">
        {(['rows', 'sql'] as const).map((v) => (
          <button
            key={v}
            className={`mob-rtab ${view === v ? 'active' : ''}`}
            onClick={() => setView(v)}
          >
            {v === 'rows' ? 'Rows' : 'SQL'}
          </button>
        ))}
      </div>

      <div className="mob-result-body">
        {running && <div className="mob-empty">Running on {database}…</div>}

        {!running && error && (
          <div className="mob-card">
            <div className="mob-bad">{error}</div>
          </div>
        )}

        {!running && !error && view === 'rows' && rows.length === 0 && (
          <div className="mob-empty">No rows matched.</div>
        )}

        {!running && !error && view === 'rows' &&
          rows.slice(0, 200).map((r, i) => (
            <div key={i} className="mob-record">
              <span className="mob-record-main">
                <span className="mob-record-label">{String(r[0] ?? '—')}</span>
                {cols.length > 2 && (
                  <span className="mob-record-sub">
                    {cols
                      .map((c, ci) => ({ c, ci }))
                      .filter(({ ci }) => ci !== 0 && ci !== figureIdx)
                      .slice(0, 2)
                      .map(({ c, ci }) => `${c}: ${r[ci] ?? '—'}`)
                      .join(' · ')}
                  </span>
                )}
              </span>
              <span className="mob-record-figure mono">
                {typeof r[figureIdx] === 'number'
                  ? (r[figureIdx] as number).toLocaleString()
                  : String(r[figureIdx] ?? '—')}
              </span>
            </div>
          ))}

        {!running && !error && view === 'rows' && rows.length > 200 && (
          <div className="mob-meta mob-truncated">
            Showing the first 200 of {rows.length.toLocaleString()} rows.
          </div>
        )}

        {view === 'sql' && <pre className="mob-sql mono mob-sql-full">{sql}</pre>}
      </div>

      <div className="mob-result-actions">
        <button className="mob-btn primary" onClick={run} disabled={running}>
          {running ? 'Running…' : 'Re-run'}
        </button>
      </div>
    </div>
  );
}

export default MobileShell;
