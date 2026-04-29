import { useState, useRef, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import Split from 'react-split';
import { AppContext } from '../../App';
import { executeQuery, exportData, getSchemaSnapshot } from '../../services/api';
import ResultsGrid from '../ResultsGrid/ResultsGrid';
import { QueryResult } from '../../types';
import { VscPlay, VscExport, VscSparkle, VscAdd, VscClose } from 'react-icons/vsc';
import AIAssistant from '../AIAssistant/AIAssistant';
import './QueryEditor.css';

interface Props {
  ctx: AppContext;
}

interface EditorTab {
  id: string;
  title: string;
  sql: string;
  result: QueryResult | null;
  activeResultTab: number;
}

interface SchemaSnapshot {
  tables: { schema: string; name: string; kind?: 'table' | 'view' }[];
  columns: { schema: string; table: string; name: string; type: string }[];
}

const RESERVED = new Set([
  'select','from','where','join','inner','left','right','outer','full','cross','on','as',
  'group','order','by','having','union','insert','update','delete','set',
  'values','top','distinct','case','when','then','else','end','and','or',
  'not','null','is','in','between','like','exists','with','into',
]);
const needsBrackets = (name: string) =>
  /[^A-Za-z0-9_$#@]/.test(name) || /^[0-9]/.test(name) || RESERVED.has(name.toLowerCase());
const quoteIdent = (name: string) => (needsBrackets(name) ? `[${name}]` : name);

interface AliasMap {
  aliases: Map<string, { schema?: string; table: string }>;
  ctes: Map<string, string>;
}

// Extract `FROM/JOIN [schema.]table [AS] alias` and `WITH cte AS (...)` from
// the buffer so column suggestions can resolve unqualified names and aliases.
const parseFromClauses = (sql: string): AliasMap => {
  const aliases = new Map<string, { schema?: string; table: string }>();
  const ctes = new Map<string, string>();
  if (!sql) return { aliases, ctes };
  const cleaned = sql
    .replace(/'(?:''|[^'])*'/g, "''")
    .replace(/--[^\n]*/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');

  const ident = String.raw`(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#@]*)`;
  const unbracket = (s: string) => s.replace(/^\[|\]$/g, '');

  const fromRe = new RegExp(
    String.raw`\b(?:from|join)\s+(?:(${ident})\s*\.\s*)?(${ident})(?:\s+(?:as\s+)?(${ident}))?`,
    'gi',
  );
  let m: RegExpExecArray | null;
  while ((m = fromRe.exec(cleaned)) !== null) {
    const schema = m[1] ? unbracket(m[1]) : undefined;
    const table = unbracket(m[2]);
    const alias = m[3] ? unbracket(m[3]) : undefined;
    if (alias && !RESERVED.has(alias.toLowerCase())) {
      aliases.set(alias.toLowerCase(), { schema, table });
    }
    aliases.set(table.toLowerCase(), { schema, table });
  }

  const cteRe = new RegExp(String.raw`\b(?:with|,)\s+(${ident})\s*(?:\([^)]*\))?\s+as\s*\(`, 'gi');
  while ((m = cteRe.exec(cleaned)) !== null) {
    const name = unbracket(m[1]);
    ctes.set(name.toLowerCase(), name);
  }

  return { aliases, ctes };
};

type Context = 'after_from' | 'expression' | 'unknown';
const detectContext = (lineToCursor: string, fullSql: string, offset: number): Context => {
  const before = lineToCursor.replace(/(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#@.\[\]]*)$/, '');
  if (/\b(?:from|join|update|into|table)\s*$/i.test(before)) return 'after_from';
  const head = fullSql.slice(0, offset).toLowerCase();
  const lastFrom = Math.max(head.lastIndexOf(' from '), head.lastIndexOf('\nfrom '));
  const lastSelect = Math.max(head.lastIndexOf('select '), head.lastIndexOf('\nselect '));
  if (lastSelect > lastFrom) return 'expression';
  const lastWhere = head.lastIndexOf('where ');
  const lastGroup = head.lastIndexOf('group by');
  const lastOrder = head.lastIndexOf('order by');
  const lastHaving = head.lastIndexOf('having ');
  if (Math.max(lastWhere, lastGroup, lastOrder, lastHaving) > lastFrom) return 'expression';
  return 'unknown';
};

const newTabId = () => `t${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
const blankTab = (n: number): EditorTab => ({
  id: newTabId(),
  title: `Query ${n}`,
  sql: 'SELECT TOP 100 * FROM ',
  result: null,
  activeResultTab: 0,
});

function QueryEditor({ ctx }: Props) {
  const [tabs, setTabs] = useState<EditorTab[]>([blankTab(1)]);
  const [activeTabId, setActiveTabId] = useState<string>(() => '');
  const [running, setRunning] = useState(false);
  const [databases, setDatabases] = useState<string[]>([]);
  const [aiOpen, setAiOpen] = useState(false);
  const editorRef = useRef<any>(null);
  const monacoRef = useRef<any>(null);
  const completionDisposableRef = useRef<{ dispose: () => void } | null>(null);

  // Per-database schema cache (case-insensitive keys via toLowerCase) so the
  // completion provider can resolve cross-database references like
  // [OtherDb].dbo.SomeTable without re-fetching on every keystroke. Pending
  // promises are tracked separately so we don't kick off duplicate fetches.
  const schemaCacheRef = useRef<Map<string, SchemaSnapshot>>(new Map());
  const schemaPendingRef = useRef<Map<string, Promise<void>>>(new Map());
  const databaseListRef = useRef<string[]>([]);
  const selectedServerRef = useRef<number | undefined>(undefined);
  const selectedDbRef = useRef<string>('');

  // Initialise activeTabId after first render so the seed tab id is stable.
  useEffect(() => {
    if (!activeTabId && tabs[0]) setActiveTabId(tabs[0].id);
  }, [activeTabId, tabs]);

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  const selectedServer = ctx.activeQuery?.serverId;
  const selectedDb = ctx.activeQuery?.database || '';

  // ── tab helpers ───────────────────────────────────────────────────────────
  const updateTab = (id: string, patch: Partial<EditorTab>) =>
    setTabs((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const addTab = () => {
    setTabs((ts) => {
      const next = blankTab(ts.length + 1);
      setActiveTabId(next.id);
      return [...ts, next];
    });
  };

  const closeTab = (id: string) => {
    setTabs((ts) => {
      const idx = ts.findIndex((t) => t.id === id);
      const next = ts.filter((t) => t.id !== id);
      if (next.length === 0) {
        const fresh = blankTab(1);
        setActiveTabId(fresh.id);
        return [fresh];
      }
      if (id === activeTabId) {
        setActiveTabId(next[Math.max(0, idx - 1)].id);
      }
      return next;
    });
  };

  // Keep refs in sync so the (long-lived) Monaco completion provider closure
  // can read the current selection without being re-registered.
  useEffect(() => {
    selectedServerRef.current = selectedServer;
  }, [selectedServer]);
  useEffect(() => {
    selectedDbRef.current = selectedDb;
  }, [selectedDb]);

  // ── server / database default selection ──────────────────────────────────
  const loadDatabases = async (serverId: number): Promise<string[]> => {
    const { getDatabases } = await import('../../services/api');
    const res = await getDatabases(serverId);
    const dbs: string[] = res.databases || [];
    setDatabases(dbs);
    databaseListRef.current = dbs;
    // A new server means the per-database schema cache is stale.
    schemaCacheRef.current = new Map();
    schemaPendingRef.current = new Map();
    return dbs;
  };

  // Fetch a snapshot for a database into the cache. Returns immediately if
  // already cached or pending. Triggers Monaco's suggest UI to refresh once a
  // background fetch finishes so the user sees results without retyping.
  const ensureSchemaSnapshot = (dbName: string) => {
    const server = selectedServerRef.current;
    if (!server || !dbName) return;
    const key = dbName.toLowerCase();
    if (schemaCacheRef.current.has(key) || schemaPendingRef.current.has(key)) return;
    const p = (async () => {
      try {
        const snap = await getSchemaSnapshot(server, dbName);
        schemaCacheRef.current.set(key, snap);
        // Re-trigger the suggest popup so the just-fetched data shows up.
        editorRef.current?.trigger?.('autocomplete', 'editor.action.triggerSuggest', {});
      } catch {
        /* ignore */
      } finally {
        schemaPendingRef.current.delete(key);
      }
    })();
    schemaPendingRef.current.set(key, p);
  };

  useEffect(() => {
    if (selectedServer || !ctx.servers || ctx.servers.length === 0) return;
    const main = ctx.servers.find((s) => /main/i.test(s.name)) || ctx.servers[0];
    if (main) ctx.setActiveQuery({ serverId: main.id, database: '' });
  }, [ctx.servers, selectedServer]);

  useEffect(() => {
    if (!selectedServer) return;
    (async () => {
      const dbs = await loadDatabases(selectedServer);
      if (!selectedDb && dbs.length > 0) {
        const def = dbs.find((d) => d.toLowerCase() === 'master') || dbs[0];
        ctx.setActiveQuery({ serverId: selectedServer, database: def });
      }
    })();
  }, [selectedServer]);

  // ── prefetch active database snapshot for autocomplete ──────────────────
  useEffect(() => {
    if (!selectedServer || !selectedDb) return;
    ensureSchemaSnapshot(selectedDb);
  }, [selectedServer, selectedDb]);

  // ── pending query (Select Top 1000 from explorer) ────────────────────────
  useEffect(() => {
    if (!ctx.pendingQuery) return;
    const { serverId, database, sql: pendingSql } = ctx.pendingQuery;
    ctx.setPendingQuery(null);
    ctx.setActiveQuery({ serverId, database });
    loadDatabases(serverId);

    // Drop the pending query into a fresh tab so we don't clobber an open one.
    const fresh: EditorTab = { ...blankTab(tabs.length + 1), sql: pendingSql };
    setTabs((ts) => [...ts, fresh]);
    setActiveTabId(fresh.id);

    setTimeout(async () => {
      setRunning(true);
      try {
        const res = await executeQuery(serverId, database, pendingSql);
        updateTab(fresh.id, { result: res, activeResultTab: 0 });
      } catch (err: any) {
        updateTab(fresh.id, {
          result: {
            columns: [],
            rows: [],
            row_count: 0,
            execution_time_ms: 0,
            error: err.response?.data?.detail || err.message,
          },
        });
      } finally {
        setRunning(false);
      }
    }, 100);
  }, [ctx.pendingQuery]);

  // ── execute (kept in a ref so Monaco's Ctrl+Enter command stays fresh) ───
  const getActiveSQL = (): string => {
    const editor = editorRef.current;
    const selection = editor?.getSelection();
    if (selection && !selection.isEmpty()) {
      return editor.getModel().getValueInRange(selection);
    }
    return activeTab?.sql || '';
  };

  const handleExecute = async () => {
    if (!selectedServer || !selectedDb || !activeTab) return;
    setRunning(true);
    const targetId = activeTab.id;
    try {
      const queryToRun = getActiveSQL();
      const res = await executeQuery(selectedServer, selectedDb, queryToRun);
      updateTab(targetId, { result: res, activeResultTab: 0 });
    } catch (err: any) {
      updateTab(targetId, {
        result: {
          columns: [],
          rows: [],
          row_count: 0,
          execution_time_ms: 0,
          error: err.response?.data?.detail || err.message,
        },
      });
    } finally {
      setRunning(false);
    }
  };

  const handleExecuteRef = useRef(handleExecute);
  useEffect(() => {
    handleExecuteRef.current = handleExecute;
  });

  const handleExport = async (format: 'csv' | 'xlsx') => {
    const queryToExport = getActiveSQL();
    if (!selectedServer || !selectedDb || !queryToExport) return;
    try {
      await exportData(selectedServer, selectedDb, queryToExport, format);
    } catch (err: any) {
      alert('Export failed: ' + err.message);
    }
  };

  // ── editor mount: Ctrl+Enter binding + completion provider registration ──
  const handleEditorMount = (editor: any, monaco: any) => {
    editorRef.current = editor;
    monacoRef.current = monaco;

    // Ctrl+Enter / Cmd+Enter — call through ref so it always sees the latest
    // handleExecute (avoids the original stale-closure bug).
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
      handleExecuteRef.current();
    });

    // Register the completion provider once globally for the SQL language.
    if (!completionDisposableRef.current) {
      completionDisposableRef.current = monaco.languages.registerCompletionItemProvider('sql', {
        triggerCharacters: ['.', '['],
        provideCompletionItems: (model: any, position: any, context: any) => {
          const lineToCursor = model.getValueInRange({
            startLineNumber: position.lineNumber,
            startColumn: 1,
            endLineNumber: position.lineNumber,
            endColumn: position.column,
          });

          // Up to four identifiers (db.schema.table.column) separated by dots.
          const tailMatch = lineToCursor.match(
            /((?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#@]*)?(?:\.(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#@]*)?){0,3})$/
          );
          const tail = tailMatch?.[1] || '';
          const rawParts = tail.split('.');
          let parts = rawParts.map((p: string) => p.replace(/^\[|\]$/g, ''));
          // If the user just typed a trailing dot, the last segment is empty
          // — that's the prefix-of-nothing they're about to type. Keep all
          // segments; the `qualifiers` array (parts minus last) drives lookup.
          const qualifiers = parts.slice(0, -1).filter((p: string) => p.length > 0);
          const word = model.getWordUntilPosition(position);
          // Trigger char `[` opens a bracketed identifier. Replace from the
          // `[` instead of the empty word so we can write the full `[Name]`.
          const triggeredByBracket = context?.triggerCharacter === '[';
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: triggeredByBracket ? word.startColumn - 1 : word.startColumn,
            endColumn: word.endColumn,
          };

          const ci = (s: string) => s.toLowerCase();
          const typed = ci(word.word || '');
          const rank = (label: string) => {
            const l = ci(label);
            if (!typed) return `1:${l}`;
            if (l.startsWith(typed)) return `0:${l}`;
            if (l.includes(typed)) return `1:${l}`;
            return `2:${l}`;
          };
          const cache = schemaCacheRef.current;
          const dbList = databaseListRef.current;
          const activeDb = selectedDbRef.current;
          const activeSnap = activeDb ? cache.get(ci(activeDb)) : undefined;

          const tableKind = monaco.languages.CompletionItemKind.Struct;
          const viewKind = monaco.languages.CompletionItemKind.Interface;
          const colKind = monaco.languages.CompletionItemKind.Field;
          const dbKind = monaco.languages.CompletionItemKind.Module;
          const schemaKind = monaco.languages.CompletionItemKind.Folder;

          const suggestions: any[] = [];
          const seen = new Set<string>();
          const insertOf = (name: string) => {
            const q = quoteIdent(name);
            // If we consumed a leading `[`, drop it from the inserted text
            // (the range covers it) — quoteIdent will re-add brackets if
            // needed.
            return triggeredByBracket && q.startsWith('[') ? q : q;
          };
          const push = (s: any) => {
            const key = `${s.kind}:${s.label}`;
            if (seen.has(key)) return;
            seen.add(key);
            if (s.filterText == null) s.filterText = s.label;
            if (s.sortText == null) s.sortText = rank(s.label);
            if (s.range == null) s.range = range;
            suggestions.push(s);
          };

          // Parse FROM/JOIN clauses to learn aliases + CTEs.
          const fullSql = model.getValue();
          const offset = model.getOffsetAt(position);
          const aliasMap = parseFromClauses(fullSql);
          const ctx = detectContext(lineToCursor, fullSql, offset);

          // ── 4-part: db.schema.table.<col> ────────────────────────────────
          if (qualifiers.length === 3) {
            const [dbName, schemaName, tableName] = qualifiers;
            const snap = cache.get(ci(dbName));
            if (!snap) {
              ensureSchemaSnapshot(dbName);
              push({ label: 'Loading…', kind: colKind, insertText: '', detail: `fetching ${dbName}`, sortText: '9' });
            } else {
              for (const c of snap.columns) {
                if (ci(c.schema) === ci(schemaName) && ci(c.table) === ci(tableName)) {
                  push({ label: c.name, kind: colKind, insertText: insertOf(c.name), detail: `${c.name} ${c.type}` });
                }
              }
            }
            return { suggestions };
          }

          // ── 3-part: db.schema.<table>  OR  schema.table.<col> ────────────
          if (qualifiers.length === 2) {
            const [first, second] = qualifiers;
            const matchedDb = dbList.find((d) => ci(d) === ci(first));
            if (matchedDb) {
              const snap = cache.get(ci(matchedDb));
              if (!snap) {
                ensureSchemaSnapshot(matchedDb);
                push({ label: 'Loading…', kind: tableKind, insertText: '', detail: `fetching ${matchedDb}`, sortText: '9' });
              } else {
                for (const t of snap.tables) {
                  if (ci(t.schema) === ci(second)) {
                    push({
                      label: t.name,
                      kind: t.kind === 'view' ? viewKind : tableKind,
                      insertText: insertOf(t.name),
                      detail: `${matchedDb}.${second}.${t.name}${t.kind === 'view' ? ' (view)' : ''}`,
                    });
                  }
                }
              }
            } else if (activeSnap) {
              // schema.table.<col> in active db
              for (const c of activeSnap.columns) {
                if (ci(c.schema) === ci(first) && ci(c.table) === ci(second)) {
                  push({ label: c.name, kind: colKind, insertText: insertOf(c.name), detail: `${c.name} ${c.type}` });
                }
              }
            }
            return { suggestions };
          }

          // ── 2-part: db.<schema>  OR  schema.<table>  OR  alias/table.<col>
          if (qualifiers.length === 1) {
            const [first] = qualifiers;
            const matchedDb = dbList.find((d) => ci(d) === ci(first));
            if (matchedDb) {
              const snap = cache.get(ci(matchedDb));
              if (!snap) {
                ensureSchemaSnapshot(matchedDb);
                push({ label: 'Loading…', kind: schemaKind, insertText: '', detail: `fetching ${matchedDb}`, sortText: '9' });
              } else {
                const schemas = new Set<string>();
                for (const t of snap.tables) schemas.add(t.schema);
                for (const s of schemas) {
                  push({ label: s, kind: schemaKind, insertText: insertOf(s), detail: `schema in ${matchedDb}` });
                }
              }
              return { suggestions };
            }
            if (activeSnap) {
              // Alias resolution → columns.
              const aliased = aliasMap.aliases.get(ci(first));
              if (aliased) {
                for (const c of activeSnap.columns) {
                  const schemaOk = !aliased.schema || ci(c.schema) === ci(aliased.schema);
                  if (schemaOk && ci(c.table) === ci(aliased.table)) {
                    push({ label: c.name, kind: colKind, insertText: insertOf(c.name), detail: `${aliased.table}.${c.name} ${c.type}` });
                  }
                }
                if (suggestions.length > 0) return { suggestions };
              }
              // first is a schema → tables in that schema.
              for (const t of activeSnap.tables) {
                if (ci(t.schema) === ci(first)) {
                  push({
                    label: t.name,
                    kind: t.kind === 'view' ? viewKind : tableKind,
                    insertText: insertOf(t.name),
                    detail: `${activeDb}.${t.schema}.${t.name}${t.kind === 'view' ? ' (view)' : ''}`,
                  });
                }
              }
              // first is a bare table → columns.
              for (const c of activeSnap.columns) {
                if (ci(c.table) === ci(first)) {
                  push({ label: c.name, kind: colKind, insertText: insertOf(c.name), detail: `${c.table}.${c.name} ${c.type}` });
                }
              }
            }
            return { suggestions };
          }

          // ── 1-part: top-level. Offer dbs, schemas, tables/views, plus
          //    in-scope columns when the cursor isn't right after FROM/JOIN.
          for (const d of dbList) {
            push({ label: d, kind: dbKind, insertText: insertOf(d), detail: 'database' });
          }
          if (activeSnap) {
            const schemas = new Set<string>();
            for (const t of activeSnap.tables) schemas.add(t.schema);
            for (const s of schemas) {
              push({ label: s, kind: schemaKind, insertText: insertOf(s), detail: 'schema' });
            }
            for (const t of activeSnap.tables) {
              push({
                label: t.name,
                kind: t.kind === 'view' ? viewKind : tableKind,
                insertText: insertOf(t.name),
                detail: `${t.schema}.${t.name}${t.kind === 'view' ? ' (view)' : ''}`,
                // After FROM/JOIN, prefer tables; in expressions, demote them.
                sortText: ctx === 'after_from' ? `0:${ci(t.name)}` : `2:${ci(t.name)}`,
              });
            }

            // Columns from in-scope tables (alias parser) — only when we're
            // in an expression position (SELECT / WHERE / GROUP / ORDER /
            // HAVING). After FROM/JOIN it's noise.
            if (ctx !== 'after_from' && aliasMap.aliases.size > 0) {
              const inScope = new Set<string>();
              for (const a of aliasMap.aliases.values()) inScope.add(ci(a.table));
              for (const c of activeSnap.columns) {
                if (inScope.has(ci(c.table))) {
                  push({
                    label: c.name,
                    kind: colKind,
                    insertText: insertOf(c.name),
                    detail: `${c.table}.${c.name} ${c.type}`,
                    sortText: `0:${ci(c.name)}`,
                  });
                }
              }
            }
          }

          // CTEs as table-like suggestions.
          for (const cteName of aliasMap.ctes.values()) {
            push({
              label: cteName,
              kind: tableKind,
              insertText: insertOf(cteName),
              detail: 'CTE',
              sortText: ctx === 'after_from' ? `0:${ci(cteName)}` : `1:${ci(cteName)}`,
            });
          }

          return { suggestions };
        },
      });
    }
  };

  // Tear the global completion provider down on unmount so HMR doesn't stack
  // duplicates during development.
  useEffect(() => {
    return () => {
      completionDisposableRef.current?.dispose();
      completionDisposableRef.current = null;
    };
  }, []);

  // ── results derivation for the active tab ────────────────────────────────
  const result = activeTab?.result || null;
  const activeResultTab = activeTab?.activeResultTab ?? 0;
  const resultSets =
    result?.result_sets && result.result_sets.length > 0
      ? result.result_sets
      : result && !result.error
      ? [{ columns: result.columns, rows: result.rows, row_count: result.row_count }]
      : [];
  const activeSet = resultSets[activeResultTab] || resultSets[0];

  return (
    <div className="query-editor-wrap">
      <div className="query-editor">
        {/* Editor tab strip */}
        <div className="editor-tab-strip">
          {tabs.map((t) => (
            <div
              key={t.id}
              className={`editor-tab ${t.id === activeTabId ? 'active' : ''}`}
              onClick={() => setActiveTabId(t.id)}
            >
              <span className="editor-tab-title">{t.title}</span>
              <button
                className="editor-tab-close"
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(t.id);
                }}
                title="Close tab"
              >
                <VscClose />
              </button>
            </div>
          ))}
          <button className="editor-tab-add" onClick={addTab} title="New query tab">
            <VscAdd />
          </button>
        </div>

        {/* Toolbar */}
        <div className="query-toolbar">
          <div className="toolbar-left">
            <select
              className="db-select"
              value={ctx.activeQuery?.serverId || ''}
              onChange={(e) => {
                const id = Number(e.target.value);
                if (id) ctx.setActiveQuery({ serverId: id, database: '' });
              }}
            >
              <option value="">Select Server</option>
              {ctx.servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>

            <select
              className="db-select"
              value={selectedDb}
              onChange={(e) => {
                if (selectedServer) {
                  ctx.setActiveQuery({ serverId: selectedServer, database: e.target.value });
                }
              }}
            >
              <option value="">Select Database</option>
              {databases.map((db) => (
                <option key={db} value={db}>
                  {db}
                </option>
              ))}
            </select>

            <button
              className="execute-btn"
              onClick={handleExecute}
              disabled={running || !selectedServer || !selectedDb}
            >
              <VscPlay /> {running ? 'Running...' : 'Execute'}
            </button>
          </div>

          <div className="toolbar-right">
            <button
              className="export-btn"
              onClick={() => handleExport('csv')}
              disabled={!result?.rows.length}
            >
              <VscExport /> CSV
            </button>
            <button
              className="export-btn"
              onClick={() => handleExport('xlsx')}
              disabled={!result?.rows.length}
            >
              <VscExport /> Excel
            </button>
            <button
              className="export-btn"
              onClick={() => setAiOpen((v) => !v)}
              title="Toggle AI assistant"
            >
              <VscSparkle /> AI
            </button>
          </div>
        </div>

        {/* Editor + Results */}
        <Split
          className="split-vertical"
          sizes={[50, 50]}
          minSize={100}
          gutterSize={4}
          direction="vertical"
        >
          <div className="editor-pane">
            {activeTab && (
              <Editor
                key={activeTab.id}
                height="100%"
                defaultLanguage="sql"
                theme="vs-dark"
                value={activeTab.sql}
                onChange={(v) => updateTab(activeTab.id, { sql: v || '' })}
                onMount={handleEditorMount}
                options={{
                  minimap: { enabled: false },
                  fontSize: 14,
                  lineNumbers: 'on',
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  tabSize: 4,
                  wordWrap: 'on',
                  suggestOnTriggerCharacters: true,
                  quickSuggestions: { other: true, comments: false, strings: false },
                }}
              />
            )}
          </div>

          <div className="results-pane">
            {result ? (
              result.error ? (
                <div className="result-error">
                  <strong>Error:</strong> {result.error}
                  <div style={{ marginTop: 8 }}>
                    <button className="export-btn" onClick={() => setAiOpen(true)}>
                      <VscSparkle /> Ask AI to fix
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {resultSets.length > 1 && (
                    <div className="result-tabs">
                      {resultSets.map((rs, i) => (
                        <button
                          key={i}
                          className={`result-tab ${i === activeResultTab ? 'active' : ''}`}
                          onClick={() => updateTab(activeTab!.id, { activeResultTab: i })}
                        >
                          Result {i + 1} ({rs.row_count})
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="result-stats">
                    {activeSet?.row_count ?? 0} row(s)
                    {resultSets.length > 1
                      ? ` in result ${activeResultTab + 1} of ${resultSets.length}`
                      : ''}{' '}
                    — total {result.execution_time_ms}ms
                  </div>
                  {activeSet && <ResultsGrid columns={activeSet.columns} rows={activeSet.rows} />}
                </>
              )
            ) : (
              <div className="result-placeholder">
                Execute a query to see results here. Press Ctrl+Enter to run.
              </div>
            )}
          </div>
        </Split>
      </div>
      {aiOpen && (
        <AIAssistant
          serverId={selectedServer}
          database={selectedDb}
          currentSql={getActiveSQL()}
          lastError={result?.error || null}
          onClose={() => setAiOpen(false)}
          onInsertSql={(newSql) => {
            if (activeTab) updateTab(activeTab.id, { sql: newSql });
            editorRef.current?.setValue?.(newSql);
          }}
        />
      )}
    </div>
  );
}

export default QueryEditor;
