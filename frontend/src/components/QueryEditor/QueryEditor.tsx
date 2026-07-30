import { useState, useRef, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import Split from 'react-split';
import { AppContext } from '../../App';
import { executeQuery, cancelQuery, exportData, getSchemaSnapshot, requestAccess } from '../../services/api';
import ResultsGrid, { CellRef } from '../ResultsGrid/ResultsGrid';
import ResultsChart from '../ResultsChart/ResultsChart';
import DataDiff from '../DataDiff/DataDiff';
import CellInspector from '../CellInspector/CellInspector';
import ExportDialog from '../ExportDialog/ExportDialog';
import { QueryResult, MissingTable, Dialect } from '../../types';
import { VscPlay, VscDebugStop, VscExport, VscSparkle, VscAdd, VscClose, VscCopy, VscSave } from 'react-icons/vsc';
import AIAssistant from '../AIAssistant/AIAssistant';
import { quoteIdent as quoteIdentAlways } from '../../utils/sqlDialect';
import { onAll } from '../../utils/actionBus';
import { labelFor } from '../../utils/shortcuts';
import { isUnscopedWrite } from '../../utils/settings';
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
  missingTables?: MissingTable[];
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
  'apply','pivot','unpivot','option','for',
]);
const RESERVED_ALT = [...RESERVED].join('|');
const needsBrackets = (name: string) =>
  /[^A-Za-z0-9_$#@]/.test(name) || /^[0-9]/.test(name) || RESERVED.has(name.toLowerCase());

// Quote only when the identifier needs it; the per-engine quote chars live in
// utils/sqlDialect so the editor and the explorer can't drift apart.
const quoteIdent = (name: string, dialect: Dialect = 'mssql') =>
  needsBrackets(name) ? quoteIdentAlways(name, dialect) : name;

interface AliasMap {
  aliases: Map<string, { db?: string; schema?: string; table: string }>;
  ctes: Map<string, string>;
}

// Extract `FROM/JOIN [db.][schema.]table [AS] alias` and `WITH cte AS (...)`
// from the buffer so column suggestions can resolve unqualified names and
// aliases — including the cross-database case (`db.schema.table`).
const parseFromClauses = (sql: string): AliasMap => {
  const aliases = new Map<string, { db?: string; schema?: string; table: string }>();
  const ctes = new Map<string, string>();
  if (!sql) return { aliases, ctes };
  const cleaned = sql
    .replace(/'(?:''|[^'])*'/g, "''")
    .replace(/--[^\n]*/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');

  const ident = String.raw`(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#@]*)`;
  const unbracket = (s: string) => s.replace(/^\[|\]$/g, '');

  // The negative lookahead before the alias capture prevents the regex from
  // greedy-eating the next clause keyword (JOIN, WHERE, ON, …) as an
  // implicit alias.
  // The leading `(?:ident\s*\.\s*){0,2}` captures up to two qualifier dots
  // (db. and schema.) so `FROM Sites.dbo.Sites` resolves to db=Sites,
  // schema=dbo, table=Sites instead of dropping the real table name.
  const fromRe = new RegExp(
    String.raw`\b(?:from|join)\s+((?:${ident}\s*\.\s*){0,2})(${ident})(?:\s+(?:as\s+)?(?!(?:${RESERVED_ALT})\b)(${ident}))?`,
    'gi',
  );
  let m: RegExpExecArray | null;
  while ((m = fromRe.exec(cleaned)) !== null) {
    const qualifiersRaw = m[1] || '';
    const table = unbracket(m[2]);
    const alias = m[3] ? unbracket(m[3]) : undefined;

    // Split qualifiers — there are 0, 1, or 2 of them, each followed by a dot.
    const quals: string[] = [];
    const qRe = new RegExp(ident, 'gi');
    let qm: RegExpExecArray | null;
    while ((qm = qRe.exec(qualifiersRaw)) !== null) quals.push(unbracket(qm[0]));
    const db = quals.length === 2 ? quals[0] : undefined;
    const schema = quals.length === 2 ? quals[1] : quals[0];

    if (alias && !RESERVED.has(alias.toLowerCase())) {
      aliases.set(alias.toLowerCase(), { db, schema, table });
    }
    aliases.set(table.toLowerCase(), { db, schema, table });
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
  /** Which results view is showing: the grid, a chart, or the diff. */
  const [resultView, setResultView] = useState<'grid' | 'chart' | 'diff'>('grid');
  const [inspected, setInspected] = useState<CellRef | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  /** Wall-clock ms since the running query started, for the progress state. */
  const [elapsed, setElapsed] = useState(0);
  const gridHandle = useRef<{ moveFocus: (delta: number) => void } | null>(null);
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
  const selectedDialectRef = useRef<Dialect>('mssql');
  // Per-execution handles for the Stop button: the AbortController cancels the
  // HTTP request, the query_id lets the server abort the in-flight SQL.
  const abortRef = useRef<AbortController | null>(null);
  const queryIdRef = useRef<string | null>(null);

  // Initialise activeTabId after first render so the seed tab id is stable.
  useEffect(() => {
    if (!activeTabId && tabs[0]) setActiveTabId(tabs[0].id);
  }, [activeTabId, tabs]);

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  const selectedServer = ctx.activeQuery?.serverId;
  const selectedDb = ctx.activeQuery?.database || '';
  const selectedDialect: Dialect =
    (ctx.servers.find((s) => s.id === selectedServer)?.dialect as Dialect) || 'mssql';

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
  useEffect(() => {
    selectedDialectRef.current = selectedDialect;
  }, [selectedDialect]);

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

  // Unique id per execution so the Stop button can cancel the right query.
  // crypto.randomUUID is undefined in a non-secure context (this app is served
  // over plain HTTP), so fall back to a timestamp+random id.
  const newQueryId = (): string => {
    try {
      if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    } catch {
      /* not available over http — fall through */
    }
    return `q-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  };

  const handleExecute = async () => {
    if (!selectedServer || !selectedDb || !activeTab) return;
    if (running) return; // already running — Stop is shown instead

    // Client-side guard: an UPDATE or DELETE with no WHERE clause is almost
    // always a mistake. This is a convenience, not access control — the server
    // is what actually decides whether this user may write at all.
    if (ctx.settings.confirmWriteWithoutWhere && isUnscopedWrite(getActiveSQL())) {
      const ok = window.confirm(
        'This statement updates or deletes every row — it has no WHERE clause.\n\nRun it anyway?'
      );
      if (!ok) return;
    }

    const queryId = newQueryId();
    const controller = new AbortController();
    queryIdRef.current = queryId;
    abortRef.current = controller;
    setRunning(true);
    const targetId = activeTab.id;
    try {
      const queryToRun = getActiveSQL();
      const res = await executeQuery(
        selectedServer,
        selectedDb,
        queryToRun,
        queryId,
        controller.signal,
      );
      updateTab(targetId, { result: res, activeResultTab: 0, missingTables: undefined });
    } catch (err: any) {
      // User hit Stop: axios surfaces an aborted request as ERR_CANCELED /
      // CanceledError. The server-side SQL abort was already fired by handleStop.
      if (err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') {
        updateTab(targetId, {
          result: {
            columns: [],
            rows: [],
            row_count: 0,
            execution_time_ms: 0,
            error: 'Query cancelled.',
          },
          missingTables: undefined,
        });
        return;
      }
      // Permissions errors come back as `detail = {detail, missing_tables}`.
      const raw = err.response?.data?.detail;
      let message: string;
      let missing: MissingTable[] | undefined;
      if (raw && typeof raw === 'object') {
        message = raw.detail || 'Access denied';
        missing = raw.missing_tables;
      } else {
        message = raw || err.message;
      }
      updateTab(targetId, {
        result: {
          columns: [],
          rows: [],
          row_count: 0,
          execution_time_ms: 0,
          error: message,
        },
        missingTables: missing,
      });
    } finally {
      setRunning(false);
      abortRef.current = null;
      queryIdRef.current = null;
    }
  };

  // Stop the running query: tell the server to abort the SQL first (the worker
  // thread keeps running the statement until SQL Server gets the attention
  // signal), then abort the HTTP request so the awaiting promise rejects.
  const handleStop = async () => {
    const qid = queryIdRef.current;
    if (qid) {
      try {
        await cancelQuery(qid);
      } catch {
        /* best-effort — abort the request regardless */
      }
    }
    abortRef.current?.abort();
  };

  const handleExecuteRef = useRef(handleExecute);
  useEffect(() => {
    handleExecuteRef.current = handleExecute;
  });

  // Live elapsed counter for the running state. The server doesn't stream
  // progress, so this is wall-clock time only — the UI says "elapsed", not
  // "percent complete", because a percentage would be invented.
  useEffect(() => {
    if (!running) {
      setElapsed(0);
      return;
    }
    const started = Date.now();
    const id = window.setInterval(() => setElapsed(Date.now() - started), 100);
    return () => window.clearInterval(id);
  }, [running]);

  // Register the actions this component owns. The shell's key handler and the
  // command palette both fire through the bus, so a binding and a palette entry
  // are the same code path.
  useEffect(() => {
    return onAll({
      execute: () => handleExecuteRef.current(),
      'execute-selection': () => handleExecuteRef.current(),
      cancel: () => {
        if (running) handleStop();
      },
      'new-tab': () => addTab(),
      'close-tab': () => activeTabId && closeTab(activeTabId),
      'next-tab': () => cycleTab(1),
      'prev-tab': () => cycleTab(-1),
      'grid-tab': () => setResultView('grid'),
      'chart-tab': () => setResultView('chart'),
      'diff-tab': () => setResultView('diff'),
      'inspect-cell': () => setInspectorOpen((v) => !v),
      export: () => setExportOpen(true),
      'ai-panel': () => setAiOpen((v) => !v),
    });
  }, [running, activeTabId, tabs.length]);

  const cycleTab = (delta: number) => {
    if (tabs.length < 2) return;
    const i = tabs.findIndex((t) => t.id === activeTabId);
    if (i === -1) return;
    const next = (i + delta + tabs.length) % tabs.length;
    setActiveTabId(tabs[next].id);
  };

  const handleSaveSnippet = async () => {
    const sql = getActiveSQL().trim() || activeTab?.sql?.trim();
    if (!sql) return;
    const name = window.prompt('Name this snippet:', activeTab?.title || 'Untitled query');
    if (!name) return;
    try {
      const { createSnippet } = await import('../../services/api');
      await createSnippet({ name, sql });
      // The rail reloads its own list when opened, so nothing to refresh here.
    } catch (err: any) {
      alert('Could not save snippet: ' + (err.response?.data?.detail || err.message));
    }
  };

  const handleExport = async (format: 'csv' | 'xlsx') => {
    const queryToExport = getActiveSQL();
    if (!selectedServer || !selectedDb || !queryToExport) return;
    try {
      await exportData(selectedServer, selectedDb, queryToExport, format);
    } catch (err: any) {
      alert('Export failed: ' + err.message);
    }
  };

  // Copy the active result set to the clipboard as TSV (headers + rows) so it
  // pastes cleanly into Excel/Sheets. The app is served over plain HTTP, where
  // navigator.clipboard is unavailable, so fall back to a hidden textarea.
  const handleCopyResults = async (set?: { columns: string[]; rows: any[][] }) => {
    if (!set || !set.rows.length) return;
    const esc = (v: any) => (v == null ? '' : String(v).replace(/\t/g, ' ').replace(/\r?\n/g, ' '));
    const tsv = [set.columns.join('\t'), ...set.rows.map((r) => r.map(esc).join('\t'))].join('\n');
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(tsv);
      } else {
        const ta = document.createElement('textarea');
        ta.value = tsv;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
    } catch {
      alert('Copy failed');
    }
  };

  // ── editor mount: Ctrl+Enter binding + completion provider registration ──
  const handleEditorMount = (editor: any, monaco: any) => {
    editorRef.current = editor;
    monacoRef.current = monaco;

    // Monaco ships its own palette, so the editor is the one surface the CSS
    // token layer can't reach. Mirror the Nocturne tokens here: keywords take
    // the accent, literals the positive green, and the chrome (gutter,
    // suggest widget, selection) matches the panels around it.
    monaco.editor.defineTheme('nocturne', {
      base: 'vs-dark',
      inherit: true,
      rules: [
        { token: 'keyword', foreground: '9184d9' },
        { token: 'keyword.sql', foreground: '9184d9' },
        { token: 'predefined.sql', foreground: 'd2cefd' },
        { token: 'operator.sql', foreground: '9397ab' },
        { token: 'string', foreground: 'aebf92' },
        { token: 'string.sql', foreground: 'aebf92' },
        { token: 'number', foreground: 'aebf92' },
        { token: 'comment', foreground: '75798c', fontStyle: 'italic' },
        { token: 'identifier', foreground: 'e9e9ed' },
      ],
      colors: {
        'editor.background': '#161826',
        'editor.foreground': '#e9e9ed',
        'editor.lineHighlightBackground': '#1c1f30',
        'editor.selectionBackground': '#2b2741',
        'editorCursor.foreground': '#9184d9',
        'editorLineNumber.foreground': '#595d6c',
        'editorLineNumber.activeForeground': '#9184d9',
        'editorIndentGuide.background': '#292b31',
        'editorWidget.background': '#232532',
        'editorWidget.border': '#3f424d',
        'editorSuggestWidget.background': '#232532',
        'editorSuggestWidget.border': '#3f424d',
        'editorSuggestWidget.selectedBackground': '#2b2741',
        'editorSuggestWidget.highlightForeground': '#d2cefd',
        'editorHoverWidget.background': '#232532',
        'editorHoverWidget.border': '#3f424d',
        'scrollbarSlider.background': '#3f424d99',
        'scrollbarSlider.hoverBackground': '#595d6ccc',
      },
    });
    monaco.editor.setTheme('nocturne');

    // Ctrl+Enter / Cmd+Enter — call through ref so it always sees the latest
    // handleExecute (avoids the original stale-closure bug).
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
      handleExecuteRef.current();
    });

    // Monaco's quickSuggestions only fires on word characters, so the popup
    // never auto-opens at positions like `WHERE `, `AND `, or after a comma
    // (the user has to type a letter or hit Ctrl+Space). Manually trigger
    // suggest the moment the user types a space after a clause keyword or a
    // comma — that's where columns would otherwise be invisibly available.
    editor.onDidType((text: string) => {
      if (text !== ' ' && text !== ',') return;
      const pos = editor.getPosition();
      if (!pos) return;
      const lineToCursor = editor.getModel().getValueInRange({
        startLineNumber: pos.lineNumber,
        startColumn: 1,
        endLineNumber: pos.lineNumber,
        endColumn: pos.column,
      });
      const triggers = /(?:\b(?:where|and|or|having|on|by|set|when|case)\s+|,\s*)$/i;
      if (triggers.test(lineToCursor)) {
        editor.trigger('autocomplete', 'editor.action.triggerSuggest', {});
      }
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
          const insertOf = (name: string) => quoteIdent(name, selectedDialectRef.current);
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
            // Alias resolution → columns. Pull from the FROM-referenced db's
            // snapshot if the table lives in a different database, otherwise
            // from the active db.
            const aliased = aliasMap.aliases.get(ci(first));
            if (aliased) {
              const dbForLookup = aliased.db || activeDb;
              const snap = dbForLookup ? cache.get(ci(dbForLookup)) : undefined;
              if (!snap && dbForLookup) {
                ensureSchemaSnapshot(dbForLookup);
                push({ label: 'Loading…', kind: colKind, insertText: '', detail: `fetching ${dbForLookup}`, sortText: '9' });
                return { suggestions };
              }
              if (snap) {
                for (const c of snap.columns) {
                  const schemaOk = !aliased.schema || ci(c.schema) === ci(aliased.schema);
                  if (schemaOk && ci(c.table) === ci(aliased.table)) {
                    push({ label: c.name, kind: colKind, insertText: insertOf(c.name), detail: `${aliased.table}.${c.name} ${c.type}` });
                  }
                }
                if (suggestions.length > 0) return { suggestions };
              }
            }
            if (activeSnap) {
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

          }

          // Columns from in-scope tables (alias parser) — only in
          // expression positions. Pulls from the FROM-referenced db's
          // snapshot when the table reference crosses databases (e.g.
          // `FROM Sites.dbo.Sites` while master is the active db).
          if (ctx !== 'after_from' && aliasMap.aliases.size > 0) {
            const seenTable = new Set<string>();
            for (const a of aliasMap.aliases.values()) {
              const key = `${ci(a.db || activeDb || '')}|${ci(a.schema || '')}|${ci(a.table)}`;
              if (seenTable.has(key)) continue;
              seenTable.add(key);
              const dbForLookup = a.db || activeDb;
              if (!dbForLookup) continue;
              const snap = cache.get(ci(dbForLookup));
              if (!snap) {
                ensureSchemaSnapshot(dbForLookup);
                continue;
              }
              for (const c of snap.columns) {
                const schemaOk = !a.schema || ci(c.schema) === ci(a.schema);
                if (schemaOk && ci(c.table) === ci(a.table)) {
                  push({
                    label: c.name,
                    kind: colKind,
                    insertText: insertOf(c.name),
                    detail: `${a.table}.${c.name} ${c.type}`,
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
  const activeServerName =
    ctx.servers.find((s) => s.id === selectedServer)?.name || 'the server';

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

            {running ? (
              <button className="stop-btn" onClick={handleStop}>
                <VscDebugStop /> Stop
              </button>
            ) : (
              <>
                <button
                  className="execute-btn"
                  onClick={handleExecute}
                  disabled={!selectedServer || !selectedDb}
                >
                  <VscPlay /> Execute
                </button>
                <span className="kbd">⌘↵</span>
              </>
            )}
          </div>

          <div className="toolbar-right">
            <button
              className="export-btn"
              onClick={() => setExportOpen(true)}
              disabled={!activeSet?.rows.length}
              title={`Export results (${labelFor('export')})`}
            >
              <VscExport /> Export…
            </button>
            <button
              className="export-btn"
              onClick={handleSaveSnippet}
              disabled={!activeTab?.sql?.trim()}
              title="Save this query to the snippet library"
            >
              <VscSave /> Save snippet
            </button>
            <button
              className="export-btn"
              onClick={() => handleCopyResults(activeSet)}
              disabled={!activeSet?.rows.length}
              title="Copy results to clipboard (TSV)"
            >
              <VscCopy /> Copy
            </button>
            <button
              className="export-btn"
              onClick={() => setAiOpen((v) => !v)}
              title={`Toggle AI assistant (${labelFor('ai-panel')})`}
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
                theme="nocturne"
                value={activeTab.sql}
                onChange={(v) => updateTab(activeTab.id, { sql: v || '' })}
                onMount={handleEditorMount}
                options={{
                  minimap: { enabled: false },
                  fontFamily: "'JetBrains Mono', 'Cascadia Mono', Consolas, monospace",
                  fontSize: ctx.settings.editorFontSize,
                  // 1.8 line-height per the handoff's editor spec — SQL reads
                  // as a list of clauses, not a wall.
                  lineHeight: Math.round(ctx.settings.editorFontSize * 1.8),
                  lineNumbers: 'on',
                  lineNumbersMinChars: 4,
                  padding: { top: 10, bottom: 10 },
                  renderLineHighlight: 'line',
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  tabSize: 4,
                  wordWrap: ctx.settings.wordWrap ? 'on' : 'off',
                  suggestOnTriggerCharacters: ctx.settings.autocomplete,
                  quickSuggestions: ctx.settings.autocomplete
                    ? { other: true, comments: false, strings: false }
                    : false,
                }}
              />
            )}
          </div>

          <div className="results-pane">
            {running ? (
              /* Running — handoff 4A/1. Wall-clock only; the server doesn't
                 stream progress, so this is an indeterminate bar with a real
                 elapsed counter rather than a fabricated percentage. */
              <div className="rs">
                <div className="rs-progress" />
                <div className="rs-title">Executing on {activeServerName}</div>
                <div className="rs-body">
                  <span className="mono">{(elapsed / 1000).toFixed(1)}s</span> elapsed ·{' '}
                  {selectedDb || 'no database'}
                </div>
                <div className="rs-actions">
                  <button className="btn btn-danger" onClick={handleStop}>
                    Cancel <span className="kbd">{labelFor('cancel')}</span>
                  </button>
                </div>
                <div className="rs-note">Other tabs stay usable while this runs.</div>
              </div>
            ) : result ? (
              result.error ? (
                result.error === 'Query cancelled.' ? (
                  /* Cancelled — handoff 4A/2. */
                  <div className="rs">
                    <div className="rs-title">Query cancelled</div>
                    <div className="rs-body">
                      The statement was aborted on the server. Any open transaction
                      was rolled back.
                    </div>
                    <div className="rs-actions">
                      <button className="btn btn-primary" onClick={handleExecute}>
                        Run again
                      </button>
                    </div>
                  </div>
                ) : (
                <div className="result-error">
                  <strong>Error:</strong> {result.error}
                  {activeTab?.missingTables && activeTab.missingTables.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ marginBottom: 6, fontSize: 12, opacity: 0.85 }}>
                        Request access to:
                      </div>
                      {activeTab.missingTables.map((m, i) => (
                        <button
                          key={i}
                          className="export-btn"
                          style={{ marginRight: 6, marginBottom: 6 }}
                          onClick={async () => {
                            try {
                              await requestAccess({
                                server_id: m.server_id,
                                database: m.database,
                                schema_name: m.schema,
                                table_name: m.table,
                                reason: '',
                              });
                              alert(
                                `Access requested for [${m.database}].[${m.schema}].[${m.table}]`,
                              );
                            } catch (err: any) {
                              alert(err.response?.data?.detail || err.message);
                            }
                          }}
                        >
                          [{m.database}].[{m.schema}].[{m.table}]
                        </button>
                      ))}
                    </div>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <button className="export-btn" onClick={() => setAiOpen(true)}>
                      <VscSparkle /> Ask AI to fix
                    </button>
                  </div>
                </div>
                )
              ) : (
                <>
                  {/* View tabs — the grid, a chart of it, or a diff between
                      result sets. */}
                  <div className="result-tabs">
                    {(['grid', 'chart', 'diff'] as const).map((v) => (
                      <button
                        key={v}
                        className={`result-tab ${resultView === v ? 'active' : ''}`}
                        onClick={() => setResultView(v)}
                      >
                        {v === 'grid' ? 'Grid' : v === 'chart' ? 'Chart' : 'Diff'}
                      </button>
                    ))}
                    {resultSets.length > 1 &&
                      resultSets.map((rs, i) => (
                        <button
                          key={`set-${i}`}
                          className={`result-tab result-set-tab ${
                            i === activeResultTab ? 'active' : ''
                          }`}
                          onClick={() => updateTab(activeTab!.id, { activeResultTab: i })}
                        >
                          Result {i + 1} ({rs.row_count})
                        </button>
                      ))}
                    <span className="result-meta mono">
                      {(activeSet?.row_count ?? 0).toLocaleString()} rows ·{' '}
                      {Math.round(result.execution_time_ms)} ms
                    </span>
                  </div>

                  {activeSet && activeSet.rows.length === 0 ? (
                    /* Empty — handoff 4A/3. The column headers stay visible so
                       it's clear the query ran and simply matched nothing. */
                    <div className="rs rs-empty">
                      <div className="rs-columns mono">{activeSet.columns.join(' · ')}</div>
                      <div className="rs-title">No rows matched</div>
                      <div className="rs-body">
                        The query ran successfully against {selectedDb} and returned
                        no rows.
                      </div>
                      <div className="rs-actions">
                        <button className="btn btn-secondary" onClick={() => setAiOpen(true)}>
                          <VscSparkle /> Why is this empty?
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="result-body">
                      {resultView === 'grid' && activeSet && (
                        <>
                          <ResultsGrid
                            columns={activeSet.columns}
                            rows={activeSet.rows}
                            density={ctx.settings.density}
                            nullDisplay={ctx.settings.nullDisplay}
                            onCellFocus={setInspected}
                            gridRef={gridHandle}
                          />
                          {inspectorOpen && inspected && (
                            <CellInspector
                              cell={inspected}
                              onClose={() => setInspectorOpen(false)}
                              onMove={(d) => gridHandle.current?.moveFocus(d)}
                            />
                          )}
                        </>
                      )}
                      {resultView === 'chart' && activeSet && (
                        <ResultsChart columns={activeSet.columns} rows={activeSet.rows} />
                      )}
                      {resultView === 'diff' && (
                        <DataDiff
                          sets={resultSets}
                          hideIdentical={ctx.settings.hideIdenticalInDiff}
                        />
                      )}
                    </div>
                  )}
                </>
              )
            ) : (
              <div className="result-placeholder">
                Execute a query to see results here. Press {labelFor('execute')} to run.
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
      {exportOpen && (
        <ExportDialog
          sets={resultSets}
          activeIndex={activeResultTab}
          tableHint={ctx.activeTable ? `[${ctx.activeTable.schema}].[${ctx.activeTable.table}]` : undefined}
          onServerExport={handleExport}
          onClose={() => setExportOpen(false)}
        />
      )}
    </div>
  );
}

export default QueryEditor;
