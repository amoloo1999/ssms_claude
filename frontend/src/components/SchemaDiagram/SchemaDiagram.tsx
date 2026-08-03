import { useEffect, useMemo, useState } from 'react';
import { AppContext } from '../../App';
import { getForeignKeys, getTableColumns, requestAccess } from '../../services/api';
import { ColumnInfo } from '../../types';
import './SchemaDiagram.css';

interface Props {
  ctx: AppContext;
}

interface Edge {
  constraint: string;
  from_schema: string;
  from_table: string;
  from_column: string;
  to_schema: string;
  to_table: string;
  to_column: string;
}

interface Entity {
  schema: string;
  table: string;
  key: string;
  locked: boolean;
  isFocus: boolean;
  /** Incoming FK (something references this) vs outgoing (this references it). */
  direction: 'focus' | 'incoming' | 'outgoing';
}

const keyOf = (schema: string, table: string) => `${schema}.${table}`;

/**
 * Schema diagram — handoff screen 2F.
 *
 * One hop around a focus table, laid out in columns: what the focus references
 * on the right, what references it on the left. Positions are computed rather
 * than draggable — the handoff's drag/zoom gestures are a nicety, and a
 * deterministic layout is more useful than a pannable one that starts as a pile.
 */
function SchemaDiagram({ ctx }: Props) {
  const [edges, setEdges] = useState<Edge[]>([]);
  const [locked, setLocked] = useState<{ schema: string; table: string }[]>([]);
  const [supported, setSupported] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [columns, setColumns] = useState<Record<string, ColumnInfo[]>>({});

  const focus = ctx.activeTable;

  useEffect(() => {
    if (!focus) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getForeignKeys(focus.serverId, focus.database, focus.schema, focus.table)
      .then((res) => {
        if (cancelled) return;
        setSupported(res.supported !== false);
        setEdges(res.edges || []);
        setLocked(res.locked || []);
        if (res.error) setError(res.error);
      })
      .catch((err) => !cancelled && setError(err.response?.data?.detail || err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [focus?.serverId, focus?.database, focus?.schema, focus?.table]);

  const lockedSet = useMemo(
    () => new Set(locked.map((l) => keyOf(l.schema, l.table))),
    [locked]
  );

  const entities: Entity[] = useMemo(() => {
    if (!focus) return [];
    const focusKey = keyOf(focus.schema, focus.table);
    const map = new Map<string, Entity>();
    map.set(focusKey, {
      schema: focus.schema,
      table: focus.table,
      key: focusKey,
      locked: false,
      isFocus: true,
      direction: 'focus',
    });

    for (const e of edges) {
      const from = keyOf(e.from_schema, e.from_table);
      const to = keyOf(e.to_schema, e.to_table);
      // The focus is the parent → the other end is something it references.
      if (from === focusKey && !map.has(to)) {
        map.set(to, {
          schema: e.to_schema,
          table: e.to_table,
          key: to,
          locked: lockedSet.has(to),
          isFocus: false,
          direction: 'outgoing',
        });
      }
      if (to === focusKey && !map.has(from)) {
        map.set(from, {
          schema: e.from_schema,
          table: e.from_table,
          key: from,
          locked: lockedSet.has(from),
          isFocus: false,
          direction: 'incoming',
        });
      }
    }
    return [...map.values()];
  }, [edges, focus, lockedSet]);

  // Columns are fetched per readable entity so the boxes show real fields.
  useEffect(() => {
    if (!focus) return;
    let cancelled = false;
    const readable = entities.filter((e) => !e.locked);
    Promise.all(
      readable.map((e) =>
        getTableColumns(focus.serverId, focus.database, e.schema, e.table)
          .then((res: any) => [e.key, res.columns || res] as const)
          .catch(() => [e.key, []] as const)
      )
    ).then((pairs) => {
      if (cancelled) return;
      setColumns(Object.fromEntries(pairs));
    });
    return () => {
      cancelled = true;
    };
  }, [entities.map((e) => e.key).join('|'), focus?.serverId, focus?.database]);

  const askForAccess = async (e: Entity) => {
    if (!focus) return;
    try {
      await requestAccess({
        server_id: focus.serverId,
        database: focus.database,
        schema_name: e.schema,
        table_name: e.table,
        reason: `Referenced by ${focus.schema}.${focus.table} — needed to read the schema diagram.`,
      });
      alert(`Access requested for ${e.schema}.${e.table}`);
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  if (!focus) {
    return (
      <div className="diagram-empty">
        Pick a table in the explorer to see how it relates to its neighbours.
      </div>
    );
  }

  if (loading) return <div className="diagram-empty">Reading foreign keys…</div>;

  if (!supported) {
    return (
      <div className="diagram-empty">
        Schema diagrams aren’t available for this connection’s engine yet — only
        SQL Server so far.
      </div>
    );
  }

  const incoming = entities.filter((e) => e.direction === 'incoming');
  const outgoing = entities.filter((e) => e.direction === 'outgoing');
  const focusEntity = entities.find((e) => e.isFocus)!;

  const renderBox = (e: Entity) => {
    const cols = columns[e.key] || [];
    // Which columns actually participate in a relationship with the focus.
    const fkCols = new Set(
      edges
        .filter((x) => keyOf(x.from_schema, x.from_table) === e.key)
        .map((x) => x.from_column)
        .concat(
          edges
            .filter((x) => keyOf(x.to_schema, x.to_table) === e.key)
            .map((x) => x.to_column)
        )
    );
    return (
      <div key={e.key} className={`ent ${e.isFocus ? 'focus' : ''} ${e.locked ? 'locked' : ''}`}>
        <div className="ent-head">
          <span className="mono">{e.key}</span>
          {e.locked && <span className="tag tag-neutral">locked</span>}
        </div>
        {e.locked ? (
          <div className="ent-locked">
            <p>Columns hidden — you don’t have SELECT on this object.</p>
            <button className="btn btn-primary" onClick={() => askForAccess(e)}>
              Request access
            </button>
          </div>
        ) : (
          <div className="ent-cols">
            {cols.length === 0 && <div className="ent-col muted">…</div>}
            {cols.slice(0, 12).map((c) => (
              <div key={c.name} className="ent-col">
                <span className="ent-key">
                  {c.is_primary_key ? 'PK' : fkCols.has(c.name) ? 'FK' : ''}
                </span>
                <span className="ent-name mono">{c.name}</span>
                <span className="ent-type">{c.data_type}</span>
              </div>
            ))}
            {cols.length > 12 && (
              <div className="ent-col muted">+{cols.length - 12} more</div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="diagram">
      <div className="diagram-toolbar">
        <span className="mono diagram-focus">{focus.schema}.{focus.table}</span>
        <span className="diagram-note">
          {focus.database} · 1 hop · {edges.length} foreign key
          {edges.length === 1 ? '' : 's'}
        </span>
      </div>

      {error && <div className="diagram-error mono">{error}</div>}

      {edges.length === 0 && !error ? (
        <div className="diagram-empty">
          No foreign keys touch {focus.schema}.{focus.table}. Nothing to draw —
          the relationships here may be by convention rather than declared
          constraints.
        </div>
      ) : (
        <div className="diagram-stage">
          <div className="diagram-col">
            {incoming.length > 0 && <div className="diagram-kicker">References this</div>}
            {incoming.map(renderBox)}
          </div>
          <div className="diagram-col diagram-col-focus">
            <div className="diagram-kicker">Focus</div>
            {renderBox(focusEntity)}
          </div>
          <div className="diagram-col">
            {outgoing.length > 0 && <div className="diagram-kicker">Referenced by this</div>}
            {outgoing.map(renderBox)}
          </div>
        </div>
      )}

      <div className="diagram-legend">
        Left: tables holding a foreign key INTO this one. Right: tables this one
        points at. Locked objects stay visible so you can ask for them in one click.
      </div>
    </div>
  );
}

export default SchemaDiagram;
