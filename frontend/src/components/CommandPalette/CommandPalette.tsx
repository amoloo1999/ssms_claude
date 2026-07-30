import { useEffect, useMemo, useRef, useState } from 'react';
import { AppContext } from '../../App';
import { getSchemaSnapshot } from '../../services/api';
import { SHORTCUTS, shortcutLabel } from '../../utils/shortcuts';
import { emit, has } from '../../utils/actionBus';
import { quoteIdent } from '../../utils/sqlDialect';
import { connectionColor } from '../../utils/connectionColor';
import { Dialect } from '../../types';
import './CommandPalette.css';

interface Props {
  ctx: AppContext;
  onClose: () => void;
}

type ItemKind = 'table' | 'view' | 'server' | 'action';

interface Item {
  kind: ItemKind;
  /** What the user reads and what the matcher runs against. */
  label: string;
  /** Muted second line. */
  context: string;
  /** Right-aligned mono hint shown on the selected row. */
  hint?: string;
  /** Colour swatch for server rows. */
  color?: string;
  run: (alt: boolean) => void;
  disabled?: boolean;
}

const GROUP_ORDER: Record<ItemKind, number> = { table: 0, view: 1, server: 2, action: 3 };
const GROUP_LABEL: Record<ItemKind, string> = {
  table: 'Tables',
  view: 'Views',
  server: 'Connections',
  action: 'Actions',
};

/**
 * Subsequence fuzzy match.
 *
 * Returns the matched character positions so the row can highlight them, or
 * null when the query isn't a subsequence of the candidate. Consecutive runs
 * and matches right after a separator score higher, so `dbo.Sites` ranks above
 * `SiteDetailsBackup` for the query "sites".
 */
function fuzzy(query: string, candidate: string): { score: number; positions: number[] } | null {
  if (!query) return { score: 0, positions: [] };
  const q = query.toLowerCase();
  const c = candidate.toLowerCase();

  const positions: number[] = [];
  let ci = 0;
  let score = 0;
  let streak = 0;

  for (let qi = 0; qi < q.length; qi++) {
    const ch = q[qi];
    let found = -1;
    while (ci < c.length) {
      if (c[ci] === ch) {
        found = ci;
        break;
      }
      ci++;
    }
    if (found === -1) return null;

    const prev = found > 0 ? c[found - 1] : '';
    if (found === 0) score += 10;
    else if (prev === '.' || prev === '_' || prev === ' ') score += 8;
    streak = positions.length && positions[positions.length - 1] === found - 1 ? streak + 1 : 0;
    score += 1 + streak * 2;

    positions.push(found);
    ci = found + 1;
  }

  // Prefer shorter candidates when the score ties — an exact-ish hit on a
  // short name beats a scattered hit across a long one.
  score -= candidate.length * 0.05;
  return { score, positions };
}

function Highlighted({ text, positions }: { text: string; positions: number[] }) {
  if (!positions.length) return <>{text}</>;
  const set = new Set(positions);
  return (
    <>
      {[...text].map((ch, i) =>
        set.has(i) ? (
          <span key={i} className="cp-match">
            {ch}
          </span>
        ) : (
          <span key={i}>{ch}</span>
        )
      )}
    </>
  );
}

function CommandPalette({ ctx, onClose }: Props) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  /** false = current connection only; true = every server (Tab widens). */
  const [wide, setWide] = useState(false);
  const [objects, setObjects] = useState<{ schema: string; name: string; kind?: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const serverId = ctx.activeQuery?.serverId;
  const database = ctx.activeQuery?.database;
  const activeServer = ctx.servers.find((s) => s.id === serverId) || null;
  const dialect: Dialect = (activeServer?.dialect as Dialect) || 'mssql';

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Objects come from the schema snapshot, which the backend already filters by
  // the caller's grants — so a non-RevMan sees only what they can select from.
  // Locked objects therefore don't appear here at all; surfacing them with a
  // "request access" affordance needs the endpoint to return them, which is
  // phase B.
  useEffect(() => {
    if (!serverId || !database) return;
    let cancelled = false;
    setLoading(true);
    getSchemaSnapshot(serverId, database)
      .then((snap: any) => {
        if (!cancelled) setObjects(snap?.tables || []);
      })
      .catch(() => {
        if (!cancelled) setObjects([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [serverId, database]);

  const items: Item[] = useMemo(() => {
    const out: Item[] = [];

    if (serverId && database) {
      for (const t of objects) {
        const qualified = `${t.schema}.${t.name}`;
        const isView = t.kind === 'view';
        out.push({
          kind: isView ? 'view' : 'table',
          label: qualified,
          context: `${database}${wide && activeServer ? ` · ${activeServer.name}` : ''}`,
          hint: '↵ open · ⌥↵ select top 100',
          run: (alt) => {
            if (alt) {
              const q = `SELECT TOP 100 * FROM ${quoteIdent(t.schema, dialect)}.${quoteIdent(
                t.name,
                dialect
              )}`;
              ctx.setPendingQuery({ serverId, database, sql: q });
              ctx.setActiveTab('query');
            } else {
              ctx.setActiveTable({ serverId, database, schema: t.schema, table: t.name });
              ctx.setActiveTab('table');
            }
            onClose();
          },
        });
      }
    }

    for (const s of ctx.servers) {
      if (!wide && s.id === serverId) continue;
      out.push({
        kind: 'server',
        label: s.name,
        context: `${s.dialect} · ${s.host}`,
        hint: '↵ switch connection',
        color: connectionColor(s),
        run: () => {
          ctx.setActiveQuery({ serverId: s.id, database: '' });
          ctx.setActiveTab('query');
          onClose();
        },
      });
    }

    for (const s of SHORTCUTS) {
      out.push({
        kind: 'action',
        label: s.label,
        context: s.group,
        hint: shortcutLabel(s),
        disabled: !has(s.id),
        run: () => {
          onClose();
          // Let the dialog unmount and hand focus back before the action runs,
          // so things like "Inspect cell" don't fight the palette for focus.
          setTimeout(() => emit(s.id), 0);
        },
      });
    }

    return out;
  }, [objects, ctx.servers, serverId, database, wide, activeServer, dialect]);

  const results = useMemo(() => {
    const scored = items
      .map((item) => {
        const m = fuzzy(query, item.label);
        return m ? { item, ...m } : null;
      })
      .filter(Boolean) as { item: Item; score: number; positions: number[] }[];

    scored.sort((a, b) => {
      const g = GROUP_ORDER[a.item.kind] - GROUP_ORDER[b.item.kind];
      if (query) return b.score - a.score || g;
      return g || a.item.label.localeCompare(b.item.label);
    });
    return scored.slice(0, 60);
  }, [items, query]);

  useEffect(() => {
    setSelected(0);
  }, [query, wide]);

  // Keep the selected row in view as the user arrows through.
  useEffect(() => {
    const el = listRef.current?.querySelector('[data-selected="true"]');
    el?.scrollIntoView({ block: 'nearest' });
  }, [selected]);

  const choose = (i: number, alt: boolean) => {
    const hit = results[i];
    if (!hit || hit.item.disabled) return;
    hit.item.run(alt);
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      choose(selected, e.altKey);
    } else if (e.key === 'Tab') {
      e.preventDefault();
      setWide((w) => !w);
    }
  };

  let lastKind: ItemKind | null = null;

  return (
    <div className="cp-backdrop" onMouseDown={onClose}>
      <div
        className="cp-panel"
        role="dialog"
        aria-label="Command palette"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="cp-header">
          <span className="cp-glyph">⌘</span>
          <input
            ref={inputRef}
            className="cp-input"
            value={query}
            placeholder="Search objects, connections, actions"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
          />
          <span className="cp-esc">esc</span>
        </div>

        <div className="cp-results" ref={listRef}>
          {loading && <div className="cp-empty">Loading objects…</div>}
          {!loading && results.length === 0 && (
            <div className="cp-empty">No matches for “{query}”.</div>
          )}
          {results.map((r, i) => {
            const showKicker = r.item.kind !== lastKind;
            lastKind = r.item.kind;
            return (
              <div key={`${r.item.kind}-${r.item.label}-${i}`}>
                {showKicker && <div className="cp-kicker">{GROUP_LABEL[r.item.kind]}</div>}
                <div
                  className={`cp-row ${i === selected ? 'selected' : ''} ${
                    r.item.disabled ? 'disabled' : ''
                  }`}
                  data-selected={i === selected}
                  onMouseEnter={() => setSelected(i)}
                  onClick={(e) => choose(i, e.altKey)}
                >
                  <span className="cp-icon">
                    {r.item.color ? (
                      <span className="cp-dot" style={{ background: r.item.color }} />
                    ) : (
                      <span className="cp-kindmark">
                        {r.item.kind === 'action' ? '⌁' : r.item.kind === 'view' ? '◇' : '▤'}
                      </span>
                    )}
                  </span>
                  <span
                    className={`cp-label ${
                      r.item.kind === 'table' || r.item.kind === 'view' ? 'mono' : ''
                    }`}
                  >
                    <Highlighted text={r.item.label} positions={r.positions} />
                  </span>
                  <span className="cp-context">{r.item.context}</span>
                  {i === selected && r.item.hint && !r.item.disabled && (
                    <span className="cp-hint">{r.item.hint}</span>
                  )}
                  {r.item.disabled && <span className="tag tag-neutral">unavailable here</span>}
                </div>
              </div>
            );
          })}
        </div>

        <div className="cp-footer">
          <span>
            {wide
              ? 'All connections · ⇥ to scope to the current one'
              : `Scoped to ${activeServer ? activeServer.name : 'no connection'} · ⇥ to widen`}
          </span>
          <span className="cp-footer-keys">↑↓ move · ↵ open</span>
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;
