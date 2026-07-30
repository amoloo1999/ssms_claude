import { useEffect, useMemo, useState } from 'react';
import './CellInspector.css';

export interface InspectedCell {
  rowIndex: number;
  column: string;
  value: unknown;
}

interface Props {
  cell: InspectedCell;
  onClose: () => void;
  onMove: (delta: number) => void;
}

type Format = 'json' | 'raw' | 'hex';

const toText = (v: unknown): string => {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  return String(v);
};

const tryPrettyJson = (text: string): string | null => {
  const t = text.trim();
  if (!t || (t[0] !== '{' && t[0] !== '[')) return null;
  try {
    return JSON.stringify(JSON.parse(t), null, 2);
  } catch {
    return null;
  }
};

const toHex = (text: string): string => {
  const bytes = new TextEncoder().encode(text);
  const lines: string[] = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = [...bytes.slice(i, i + 16)];
    const hex = chunk.map((b) => b.toString(16).padStart(2, '0')).join(' ');
    const ascii = chunk.map((b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
    lines.push(`${i.toString(16).padStart(8, '0')}  ${hex.padEnd(47)}  ${ascii}`);
  }
  return lines.join('\n');
};

/** Syntax-tint pretty-printed JSON: keys accent, string values green. */
function JsonBody({ text }: { text: string }) {
  const parts = useMemo(() => text.split(/("(?:\\.|[^"\\])*"\s*:)/g), [text]);
  return (
    <pre className="ci-body">
      {parts.map((part, i) =>
        /^"(?:\\.|[^"\\])*"\s*:$/.test(part) ? (
          <span key={i} className="ci-key">
            {part}
          </span>
        ) : (
          <span key={i} className="ci-val">
            {part}
          </span>
        )
      )}
    </pre>
  );
}

/**
 * Cell inspector — handoff screen 3B.
 *
 * Opens on Space over the selected cell. Long values (NVARCHAR(MAX) notes, JSON
 * blobs) are unreadable in a grid cell; this is where you actually read one.
 */
function CellInspector({ cell, onClose, onMove }: Props) {
  const text = toText(cell.value);
  const pretty = useMemo(() => tryPrettyJson(text), [text]);
  const [format, setFormat] = useState<Format>(pretty ? 'json' : 'raw');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setFormat(pretty ? 'json' : 'raw');
  }, [pretty, cell.rowIndex, cell.column]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === ' ') {
        e.preventDefault();
        onClose();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        onMove(1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        onMove(-1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, onMove]);

  const copy = async () => {
    const payload = format === 'json' && pretty ? pretty : format === 'hex' ? toHex(text) : text;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(payload);
      } else {
        // The app is served over plain HTTP, where navigator.clipboard is
        // unavailable — same fallback the results copy uses.
        const ta = document.createElement('textarea');
        ta.value = payload;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard blocked — the value is selectable in the panel regardless */
    }
  };

  const bytes = new TextEncoder().encode(text).length;
  const lines = text ? text.split('\n').length : 0;
  const isNull = cell.value === null || cell.value === undefined;

  return (
    <div className="ci">
      <div className="ci-head">
        <span className="ci-title">Cell inspector</span>
        <span className="ci-meta mono">
          row {cell.rowIndex + 1} · {cell.column}
        </span>
        <button className="ci-close" onClick={onClose} title="Close (Space)">
          ✕
        </button>
      </div>

      <div className="ci-formats">
        <div className="seg">
          {(['json', 'raw', 'hex'] as Format[]).map((f) => (
            <button
              key={f}
              className={`seg-opt ${format === f ? 'active' : ''}`}
              onClick={() => setFormat(f)}
              disabled={f === 'json' && !pretty}
              title={f === 'json' && !pretty ? 'Not valid JSON' : undefined}
            >
              {f === 'json' ? 'JSON' : f === 'raw' ? 'Raw text' : 'Hex'}
            </button>
          ))}
        </div>
        <span className="ci-size mono">
          {bytes} bytes{lines > 1 ? ` · ${lines} lines` : ''}
        </span>
      </div>

      <div className="ci-content">
        {isNull ? (
          <div className="ci-null">NULL</div>
        ) : format === 'json' && pretty ? (
          <JsonBody text={pretty} />
        ) : (
          <pre className="ci-body">{format === 'hex' ? toHex(text) : text}</pre>
        )}
      </div>

      <div className="ci-actions">
        <button className="btn btn-secondary" onClick={copy}>
          {copied ? 'Copied' : 'Copy value'}
        </button>
        <span className="ci-hint mono">↑↓ move cell</span>
      </div>
    </div>
  );
}

export default CellInspector;
