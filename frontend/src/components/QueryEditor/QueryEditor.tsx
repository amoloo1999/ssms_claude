import { useState, useRef, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import Split from 'react-split';
import { AppContext } from '../../App';
import { executeQuery, exportData } from '../../services/api';
import ResultsGrid from '../ResultsGrid/ResultsGrid';
import { QueryResult } from '../../types';
import { VscPlay, VscExport, VscSparkle } from 'react-icons/vsc';
import AIAssistant from '../AIAssistant/AIAssistant';
import './QueryEditor.css';

interface Props {
  ctx: AppContext;
}

function QueryEditor({ ctx }: Props) {
  const [sql, setSql] = useState('SELECT TOP 100 * FROM ');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [running, setRunning] = useState(false);
  const [databases, setDatabases] = useState<string[]>([]);
  const [activeResultTab, setActiveResultTab] = useState(0);
  const [aiOpen, setAiOpen] = useState(false);
  const editorRef = useRef<any>(null);

  // Single source of truth for the active server/database is ctx.activeQuery.
  // Deriving the dropdown values from context (instead of mirroring them into
  // local state) avoids the race conditions that previously caused manual
  // database selections to get clobbered by the auto-default effect.
  const selectedServer = ctx.activeQuery?.serverId;
  const selectedDb = ctx.activeQuery?.database || '';

  const loadDatabases = async (serverId: number): Promise<string[]> => {
    const { getDatabases } = await import('../../services/api');
    const res = await getDatabases(serverId);
    const dbs: string[] = res.databases || [];
    setDatabases(dbs);
    return dbs;
  };

  // Auto-default the active server to the one whose name contains "main"
  // (case-insensitive), falling back to the first server. Runs once the
  // server list is loaded and only when nothing is already selected.
  useEffect(() => {
    if (selectedServer || !ctx.servers || ctx.servers.length === 0) return;
    const main = ctx.servers.find((s) => /main/i.test(s.name)) || ctx.servers[0];
    if (main) {
      ctx.setActiveQuery({ serverId: main.id, database: '' });
    }
  }, [ctx.servers, selectedServer]);

  // Whenever the active server changes, refresh the database list and (only
  // if no database is already selected) pick a sensible default.
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

  // Handle pending queries from context menu (e.g., Select Top 1000)
  useEffect(() => {
    if (ctx.pendingQuery) {
      const { serverId, database, sql: pendingSql } = ctx.pendingQuery;
      ctx.setPendingQuery(null);
      setSql(pendingSql);
      ctx.setActiveQuery({ serverId, database });
      loadDatabases(serverId);

      // Auto-execute after a short delay to let state settle
      setTimeout(async () => {
        setRunning(true);
        try {
          const res = await executeQuery(serverId, database, pendingSql);
          setResult(res);
          setActiveResultTab(0);
        } catch (err: any) {
          setResult({
            columns: [],
            rows: [],
            row_count: 0,
            execution_time_ms: 0,
            error: err.response?.data?.detail || err.message,
          });
        } finally {
          setRunning(false);
        }
      }, 100);
    }
  }, [ctx.pendingQuery]);

  const handleExecute = async () => {
    if (!selectedServer || !selectedDb) return;
    setRunning(true);
    try {
      const queryToRun = getActiveSQL();

      const res = await executeQuery(selectedServer, selectedDb, queryToRun);
      setResult(res);
      setActiveResultTab(0);
    } catch (err: any) {
      setResult({
        columns: [],
        rows: [],
        row_count: 0,
        execution_time_ms: 0,
        error: err.response?.data?.detail || err.message,
      });
    } finally {
      setRunning(false);
    }
  };

  const getActiveSQL = (): string => {
    const editor = editorRef.current;
    const selection = editor?.getSelection();
    if (selection && !selection.isEmpty()) {
      return editor.getModel().getValueInRange(selection);
    }
    return sql;
  };

  const handleExport = async (format: 'csv' | 'xlsx') => {
    const db = ctx.activeQuery?.database || selectedDb;
    const server = ctx.activeQuery?.serverId || selectedServer;
    const queryToExport = getActiveSQL();
    if (!server || !db || !queryToExport) return;
    try {
      await exportData(server, db, queryToExport, format);
    } catch (err: any) {
      alert('Export failed: ' + err.message);
    }
  };

  const handleEditorMount = (editor: any) => {
    editorRef.current = editor;
    // Ctrl+Enter / Cmd+Enter to execute
    editor.addCommand(2048 | 3, () => handleExecute()); // Ctrl+Enter
  };

  const resultSets = result?.result_sets && result.result_sets.length > 0
    ? result.result_sets
    : result && !result.error
      ? [{ columns: result.columns, rows: result.rows, row_count: result.row_count }]
      : [];
  const activeSet = resultSets[activeResultTab] || resultSets[0];

  return (
    <div className="query-editor-wrap">
    <div className="query-editor">
      {/* Toolbar */}
      <div className="query-toolbar">
        <div className="toolbar-left">
          <select
            className="db-select"
            value={ctx.activeQuery?.serverId || ''}
            onChange={(e) => {
              const id = Number(e.target.value);
              if (id) {
                // Clearing the database here lets the auto-default effect
                // pick a sensible one for the new server.
                ctx.setActiveQuery({ serverId: id, database: '' });
              }
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
          <button className="export-btn" onClick={() => handleExport('csv')} disabled={!result?.rows.length}>
            <VscExport /> CSV
          </button>
          <button className="export-btn" onClick={() => handleExport('xlsx')} disabled={!result?.rows.length}>
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
          <Editor
            height="100%"
            defaultLanguage="sql"
            theme="vs-dark"
            value={sql}
            onChange={(v) => setSql(v || '')}
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
            }}
          />
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
                        onClick={() => setActiveResultTab(i)}
                      >
                        Result {i + 1} ({rs.row_count})
                      </button>
                    ))}
                  </div>
                )}
                <div className="result-stats">
                  {activeSet?.row_count ?? 0} row(s)
                  {resultSets.length > 1 ? ` in result ${activeResultTab + 1} of ${resultSets.length}` : ''}
                  {' '}— total {result.execution_time_ms}ms
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
          setSql(newSql);
          editorRef.current?.setValue?.(newSql);
        }}
      />
    )}
    </div>
  );
}

export default QueryEditor;
