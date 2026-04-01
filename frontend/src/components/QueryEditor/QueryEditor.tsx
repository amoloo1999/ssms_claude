import { useState, useRef, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import Split from 'react-split';
import { AppContext } from '../../App';
import { executeQuery, exportData } from '../../services/api';
import ResultsGrid from '../ResultsGrid/ResultsGrid';
import { QueryResult } from '../../types';
import { VscPlay, VscExport } from 'react-icons/vsc';
import './QueryEditor.css';

interface Props {
  ctx: AppContext;
}

function QueryEditor({ ctx }: Props) {
  const [sql, setSql] = useState('SELECT TOP 100 * FROM ');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [running, setRunning] = useState(false);
  const [selectedDb, setSelectedDb] = useState('');
  const [databases, setDatabases] = useState<string[]>([]);
  const editorRef = useRef<any>(null);

  const selectedServer = ctx.activeQuery?.serverId;

  // Load databases when server changes
  const loadDatabases = async (serverId: number) => {
    const { getDatabases } = await import('../../services/api');
    const res = await getDatabases(serverId);
    setDatabases(res.databases || []);
    if (res.databases?.length > 0 && !selectedDb) {
      setSelectedDb(res.databases[0]);
    }
  };

  useState(() => {
    if (selectedServer) {
      loadDatabases(selectedServer);
      if (ctx.activeQuery?.database) {
        setSelectedDb(ctx.activeQuery.database);
      }
    }
  });

  // Handle pending queries from context menu (e.g., Select Top 1000)
  useEffect(() => {
    if (ctx.pendingQuery) {
      const { serverId, database, sql: pendingSql } = ctx.pendingQuery;
      ctx.setPendingQuery(null);
      setSql(pendingSql);
      setSelectedDb(database);
      loadDatabases(serverId);

      // Auto-execute after a short delay to let state settle
      setTimeout(async () => {
        setRunning(true);
        try {
          const res = await executeQuery(serverId, database, pendingSql);
          setResult(res);
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
      const editor = editorRef.current;
      const selection = editor?.getSelection();
      let queryToRun = sql;

      // Use selected text if there's a selection
      if (selection && !selection.isEmpty()) {
        queryToRun = editor.getModel().getValueInRange(selection);
      }

      const res = await executeQuery(selectedServer, selectedDb, queryToRun);
      setResult(res);
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

  const handleExport = async (format: 'csv' | 'xlsx') => {
    if (!selectedServer || !selectedDb || !sql) return;
    await exportData(selectedServer, selectedDb, sql, format);
  };

  const handleEditorMount = (editor: any) => {
    editorRef.current = editor;
    // Ctrl+Enter / Cmd+Enter to execute
    editor.addCommand(2048 | 3, () => handleExecute()); // Ctrl+Enter
  };

  return (
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
                ctx.setActiveQuery({ serverId: id, database: selectedDb });
                loadDatabases(id);
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
              setSelectedDb(e.target.value);
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
              </div>
            ) : (
              <>
                <div className="result-stats">
                  {result.row_count} row(s) returned in {result.execution_time_ms}ms
                </div>
                <ResultsGrid columns={result.columns} rows={result.rows} />
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
  );
}

export default QueryEditor;
