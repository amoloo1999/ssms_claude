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
  const schemaRef = useRef<SchemaSnapshot | null>(null);
  const completionDisposableRef = useRef<{ dispose: () => void } | null>(null);

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

  // ── server / database default selection ──────────────────────────────────
  const loadDatabases = async (serverId: number): Promise<string[]> => {
    const { getDatabases } = await import('../../services/api');
    const res = await getDatabases(serverId);
    const dbs: string[] = res.databases || [];
    setDatabases(dbs);
    return dbs;
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

  // ── schema snapshot for autocomplete ─────────────────────────────────────
  useEffect(() => {
    if (!selectedServer || !selectedDb) {
      schemaRef.current = null;
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const snap = await getSchemaSnapshot(selectedServer, selectedDb);
        if (!cancelled) schemaRef.current = snap;
      } catch {
        if (!cancelled) schemaRef.current = null;
      }
    })();
    return () => {
      cancelled = true;
    };
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
        triggerCharacters: ['.', ' ', '['],
        provideCompletionItems: (model: any, position: any) => {
          const snap = schemaRef.current;
          if (!snap) return { suggestions: [] };
          const word = model.getWordUntilPosition(position);
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endColumn: word.endColumn,
          };
          const tableKind = monaco.languages.CompletionItemKind.Struct;
          const colKind = monaco.languages.CompletionItemKind.Field;
          const suggestions: any[] = [];
          for (const t of snap.tables) {
            suggestions.push({
              label: `${t.schema}.${t.name}`,
              kind: tableKind,
              insertText: `${t.schema}.${t.name}`,
              detail: 'table',
              range,
            });
            suggestions.push({
              label: t.name,
              kind: tableKind,
              insertText: t.name,
              detail: `${t.schema}.${t.name}`,
              range,
            });
          }
          // De-dupe column names while keeping a sample qualified detail.
          const seenCols = new Map<string, string>();
          for (const c of snap.columns) {
            if (!seenCols.has(c.name)) seenCols.set(c.name, `${c.schema}.${c.table}.${c.name}`);
          }
          for (const [name, detail] of seenCols) {
            suggestions.push({
              label: name,
              kind: colKind,
              insertText: name,
              detail,
              range,
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
