import { useEffect, useState } from 'react';
import { ResultSet } from '../../types';
import './ExportDialog.css';

/**
 * `csv` and `xlsx` round-trip to the server, which re-runs the query and
 * streams a file. The rest are built here from rows already in the browser —
 * no second execution, and no new endpoint.
 */
export type ExportFormat = 'csv' | 'xlsx' | 'json' | 'insert' | 'markdown';

interface Props {
  sets: ResultSet[];
  activeIndex: number;
  /** Qualified name used for generated INSERT statements. */
  tableHint?: string;
  onServerExport: (format: 'csv' | 'xlsx') => Promise<void> | void;
  onClose: () => void;
}

const SERVER_FORMATS: ExportFormat[] = ['csv', 'xlsx'];

const FORMAT_LABEL: Record<ExportFormat, string> = {
  csv: 'CSV',
  xlsx: 'Excel',
  json: 'JSON',
  insert: 'INSERT',
  markdown: 'Markdown',
};

const cell = (v: unknown): string => (v === null || v === undefined ? '' : String(v));

const sqlLiteral = (v: unknown): string => {
  if (v === null || v === undefined) return 'NULL';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'boolean') return v ? '1' : '0';
  return `'${String(v).replace(/'/g, "''")}'`;
};

function build(format: ExportFormat, set: ResultSet, tableHint: string, includeHeader: boolean): string {
  const { columns, rows } = set;

  if (format === 'json') {
    return JSON.stringify(
      rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]]))),
      null,
      2
    );
  }

  if (format === 'insert') {
    const cols = columns.map((c) => `[${c}]`).join(', ');
    return rows
      .map((r) => `INSERT INTO ${tableHint} (${cols}) VALUES (${r.map(sqlLiteral).join(', ')});`)
      .join('\n');
  }

  if (format === 'markdown') {
    const head = `| ${columns.join(' | ')} |`;
    const rule = `| ${columns.map(() => '---').join(' | ')} |`;
    const body = rows.map((r) => `| ${r.map((v) => cell(v).replace(/\|/g, '\\|')).join(' | ')} |`);
    return [head, rule, ...body].join('\n');
  }

  // CSV built locally (used for the selection-only case).
  const esc = (v: unknown) => {
    const s = cell(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = rows.map((r) => r.map(esc).join(','));
  return includeHeader ? [columns.map(esc).join(','), ...lines].join('\n') : lines.join('\n');
}

function download(text: string, filename: string, mime: string) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Export — handoff screen 4B. */
function ExportDialog({ sets, activeIndex, tableHint, onServerExport, onClose }: Props) {
  const [scope, setScope] = useState<'this' | 'all'>('this');
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [includeHeader, setIncludeHeader] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const active = sets[activeIndex];
  const chosen = scope === 'this' ? [active] : sets;
  const rowCount = chosen.reduce((a, s) => a + (s?.rows.length || 0), 0);

  // Multi-set export only works locally: the server re-runs the query and
  // streams one file, so it can't split a batch into sheets.
  const serverPath = SERVER_FORMATS.includes(format) && scope === 'this';

  const run = async () => {
    if (!active) return;
    setBusy(true);
    try {
      if (serverPath) {
        await onServerExport(format as 'csv' | 'xlsx');
      } else if (format === 'xlsx') {
        // No local xlsx writer in the bundle — fall back to CSV per set.
        chosen.forEach((s, i) =>
          download(build('csv', s, tableHint || '[dbo].[Target]', includeHeader), `result-${i + 1}.csv`, 'text/csv')
        );
      } else {
        const ext = format === 'insert' ? 'sql' : format === 'markdown' ? 'md' : format;
        const mime =
          format === 'json' ? 'application/json' : format === 'csv' ? 'text/csv' : 'text/plain';
        if (chosen.length === 1) {
          download(build(format, chosen[0], tableHint || '[dbo].[Target]', includeHeader), `result.${ext}`, mime);
        } else {
          chosen.forEach((s, i) =>
            download(build(format, s, tableHint || '[dbo].[Target]', includeHeader), `result-${i + 1}.${ext}`, mime)
          );
        }
      }
      onClose();
    } catch (err: any) {
      alert('Export failed: ' + (err?.message || err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="exp-backdrop" onMouseDown={onClose}>
      <div
        className="exp"
        role="dialog"
        aria-label="Export results"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3 className="exp-title">Export</h3>

        <div className="exp-section">
          <div className="exp-kicker">What to export</div>
          <label className="exp-radio">
            <input
              type="radio"
              checked={scope === 'this'}
              onChange={() => setScope('this')}
            />
            <span className="exp-dot" />
            This result ({active?.rows.length.toLocaleString() ?? 0} rows)
          </label>
          <label className={`exp-radio ${sets.length < 2 ? 'disabled' : ''}`}>
            <input
              type="radio"
              checked={scope === 'all'}
              disabled={sets.length < 2}
              onChange={() => setScope('all')}
            />
            <span className="exp-dot" />
            All {sets.length} result sets, one file each
          </label>
        </div>

        <div className="exp-section">
          <div className="exp-kicker">Format</div>
          <div className="seg">
            {(Object.keys(FORMAT_LABEL) as ExportFormat[]).map((f) => (
              <button
                key={f}
                className={`seg-opt ${format === f ? 'active' : ''}`}
                onClick={() => setFormat(f)}
              >
                {FORMAT_LABEL[f]}
              </button>
            ))}
          </div>
        </div>

        {(format === 'csv' || format === 'markdown') && (
          <div className="exp-section">
            <label className="exp-radio">
              <input
                type="checkbox"
                checked={includeHeader}
                onChange={(e) => setIncludeHeader(e.target.checked)}
              />
              <span className="exp-dot exp-check" />
              Include the header row
            </label>
          </div>
        )}

        <p className="exp-note">
          {serverPath
            ? 'The server re-runs the query and streams the file, so the export reflects the data as of now.'
            : 'Built from the rows already loaded in this tab — no second execution.'}
          {format === 'insert' && tableHint && (
            <>
              {' '}
              Statements target <span className="mono">{tableHint}</span>.
            </>
          )}
        </p>

        <div className="exp-actions">
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={run} disabled={busy || !active}>
            {busy ? 'Exporting…' : `Export ${rowCount.toLocaleString()} rows`}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ExportDialog;
