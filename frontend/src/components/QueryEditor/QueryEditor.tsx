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
  tables: { schema: string; name: string }[];
  columns: { schema: string; table: string; name: string; type: string }[];
}

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
        provideCompletionItems: (model: any, position: any) => {
          const lineToCursor = model.getValueInRange({
            startLineNumber: position.lineNumber,
            startColumn: 1,
            endLineNumber: position.lineNumber,
            endColumn: position.column,
          });

          // Pull the trailing dotted identifier path: up to three identifiers
          // separated by dots. Brackets [Foo] are stripped after capture.
          const tailMatch = lineToCursor.match(
            /((?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#@]*)?(?:\.(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#@]*)?){0,2})$/
          );
          const tail = tailMatch?.[1] || '';
          const rawParts = tail.split('.');
          const parts = rawParts.map((p: string) => p.replace(/^\[|\]$/g, ''));
          // The last segment is what the user is currently typing — replaced
          // by the suggestion. Earlier segments are the qualifying context.
          const word = model.getWordUntilPosition(position);
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endColumn: word.endColumn,
          };

          const ci = (s: string) => s.toLowerCase();
          // Monaco ranks suggestions by sortText (lexicographic). We compute a
          // rank against the prefix the user is currently typing so that
          // prefix matches come first, then substring matches, then the rest.
          // Within each tier we keep alphabetical order.
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
          const colKind = monaco.languages.CompletionItemKind.Field;
          const dbKind = monaco.languages.CompletionItemKind.Module;
          const schemaKind = monaco.languages.CompletionItemKind.Folder;

          const suggestions: any[] = [];
          const seen = new Set<string>();
          const push = (s: any) => {
            const key = `${s.kind}:${s.label}`;
            if (seen.has(key)) return;
            seen.add(key);
            // filterText drives Monaco's matcher; sortText drives ordering.
            if (s.filterText == null) s.filterText = s.label;
            if (s.sortText == null) s.sortText = rank(s.label);
            suggestions.push(s);
          };

          if (parts.length >= 3) {
            // db.schema.<typing> — list tables in that db+schema only.
            const [dbName, schemaName] = parts;
            const snap = cache.get(ci(dbName));
            if (!snap) {
              ensureSchemaSnapshot(dbName);
              push({
                label: 'Loading…',
                kind: tableKind,
                insertText: '',
                detail: `fetching tables for ${dbName}`,
                range,
                sortText: '9',
              });
            } else {
              for (const t of snap.tables) {
                if (ci(t.schema) === ci(schemaName)) {
                  push({
                    label: t.name,
                    kind: tableKind,
                    insertText: t.name,
                    detail: `${dbName}.${schemaName}.${t.name}`,
                    range,
                  });
                }
              }
            }
          } else if (parts.length === 2) {
            // Two segments — meaning depends on what the first part is.
            // If it's a database name, ONLY show schemas from that db.
            // Otherwise fall through to schema→tables or table→columns.
            const [first] = parts;

            const matchedDb = dbList.find((d) => ci(d) === ci(first));
            if (matchedDb) {
              // first is a database → suggest only schemas inside it.
              const snap = cache.get(ci(matchedDb));
              if (!snap) {
                ensureSchemaSnapshot(matchedDb);
                push({
                  label: 'Loading…',
                  kind: schemaKind,
                  insertText: '',
                  detail: `fetching schemas for ${matchedDb}`,
                  range,
                  sortText: '9',
                });
              } else {
                const schemas = new Set<string>();
                for (const t of snap.tables) schemas.add(t.schema);
                for (const s of schemas) {
                  push({
                    label: s,
                    kind: schemaKind,
                    insertText: s,
                    detail: `schema in ${matchedDb}`,
                    range,
                  });
                }
              }
            } else {
              // Not a database — try schema→tables, then table→columns.
              if (activeSnap) {
                // (b) first is a schema in the active database → suggest its tables.
                for (const t of activeSnap.tables) {
                  if (ci(t.schema) === ci(first)) {
                    push({
                      label: t.name,
                      kind: tableKind,
                      insertText: t.name,
                      detail: `${activeDb}.${t.schema}.${t.name}`,
                      range,
                    });
                  }
                }

                // (c) first is a table in the active database → suggest its columns.
                for (const c of activeSnap.columns) {
                  if (ci(c.table) === ci(first)) {
                    push({
                      label: c.name,
                      kind: colKind,
                      insertText: c.name,
                      detail: `${c.table}.${c.name} ${c.type}`,
                      range,
                    });
                  }
                }
              }
            }
          } else {
            // Single token (or empty) — offer databases on this server,
            // plus schemas and tables from the active database. Columns are
            // omitted here to keep the list manageable; they appear once you
            // qualify with a table name (e.g. tableName.<col>).
            for (const d of dbList) {
              push({ label: d, kind: dbKind, insertText: d, detail: 'database', range });
            }
            if (activeSnap) {
              const schemas = new Set<string>();
              for (const t of activeSnap.tables) schemas.add(t.schema);
              for (const s of schemas) {
                push({ label: s, kind: schemaKind, insertText: s, detail: 'schema', range });
              }
              for (const t of activeSnap.tables) {
                push({
                  label: t.name,
                  kind: tableKind,
                  insertText: t.name,
                  detail: `${t.schema}.${t.name}`,
                  range,
                });
              }
            }
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
