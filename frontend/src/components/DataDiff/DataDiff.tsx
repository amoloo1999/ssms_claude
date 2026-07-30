import { useMemo, useState } from 'react';
import { ResultSet } from '../../types';
import './DataDiff.css';

interface Props {
  /** Every result set open in this tab — the diff picks two of them. */
  sets: ResultSet[];
  hideIdentical: boolean;
}

type RowKind = 'changed' | 'right-only' | 'left-only' | 'identical';

interface DiffRow {
  key: string;
  kind: RowKind;
  left: any[] | null;
  right: any[] | null;
  /** Numeric delta on the compared column, when both sides are numbers. */
  delta: number | null;
}

const val = (v: unknown): string => (v === null || v === undefined ? '' : String(v));

const fmtDelta = (d: number): string =>
  `${d > 0 ? '+' : ''}${d.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

/**
 * Data diff — handoff screen 2D.
 *
 * Runs client-side, which the handoff explicitly sanctions for modest result
 * sets. Beyond a few tens of thousands of rows this should move server-side;
 * the row cap below is a guard against locking up the tab, and it says so in
 * the footer rather than silently truncating.
 */
const MAX_ROWS = 20000;

function DataDiff({ sets, hideIdentical }: Props) {
  const [leftIdx, setLeftIdx] = useState(0);
  const [rightIdx, setRightIdx] = useState(sets.length > 1 ? 1 : 0);
  const [keyCol, setKeyCol] = useState(0);
  const [compareCol, setCompareCol] = useState(1);
  const [showIdentical, setShowIdentical] = useState(!hideIdentical);

  const left = sets[leftIdx];
  const right = sets[rightIdx];

  const diff = useMemo(() => {
    if (!left || !right) return null;

    const truncated = left.rows.length > MAX_ROWS || right.rows.length > MAX_ROWS;
    const lRows = left.rows.slice(0, MAX_ROWS);
    const rRows = right.rows.slice(0, MAX_ROWS);

    const lMap = new Map<string, any[]>();
    for (const r of lRows) lMap.set(val(r[keyCol]), r);
    const rMap = new Map<string, any[]>();
    for (const r of rRows) rMap.set(val(r[keyCol]), r);

    const rows: DiffRow[] = [];
    const seen = new Set<string>();

    for (const [k, lRow] of lMap) {
      seen.add(k);
      const rRow = rMap.get(k);
      if (!rRow) {
        rows.push({ key: k, kind: 'left-only', left: lRow, right: null, delta: null });
        continue;
      }
      const lv = lRow[compareCol];
      const rv = rRow[compareCol];
      const same = val(lv) === val(rv);
      const delta =
        typeof lv === 'number' && typeof rv === 'number' ? rv - lv : null;
      rows.push({
        key: k,
        kind: same ? 'identical' : 'changed',
        left: lRow,
        right: rRow,
        delta: same ? null : delta,
      });
    }

    for (const [k, rRow] of rMap) {
      if (seen.has(k)) continue;
      rows.push({ key: k, kind: 'right-only', left: null, right: rRow, delta: null });
    }

    const counts = {
      changed: rows.filter((r) => r.kind === 'changed').length,
      rightOnly: rows.filter((r) => r.kind === 'right-only').length,
      leftOnly: rows.filter((r) => r.kind === 'left-only').length,
      identical: rows.filter((r) => r.kind === 'identical').length,
    };

    return { rows, counts, truncated };
  }, [left, right, keyCol, compareCol]);

  if (sets.length < 2) {
    return (
      <div className="diff-empty">
        Comparing needs two result sets. Run a batch that returns more than one —
        separate the statements and execute them together.
      </div>
    );
  }

  if (!diff) return <div className="diff-empty">Nothing to compare.</div>;

  const visible = showIdentical ? diff.rows : diff.rows.filter((r) => r.kind !== 'identical');

  const drift = (() => {
    const { changed, rightOnly, leftOnly } = diff.counts;
    if (!changed && !rightOnly && !leftOnly) return 'The two result sets are identical.';
    const bits: string[] = [];
    if (changed) bits.push(`${changed} row${changed === 1 ? '' : 's'} changed`);
    if (rightOnly) bits.push(`${rightOnly} only on the right`);
    if (leftOnly) bits.push(`${leftOnly} only on the left`);
    return `${bits.join(', ')} — keyed on ${left.columns[keyCol]}, comparing ${left.columns[compareCol]}.`;
  })();

  return (
    <div className="diff">
      <div className="diff-controls">
        <label className="diff-picker">
          Left
          <select value={leftIdx} onChange={(e) => setLeftIdx(Number(e.target.value))}>
            {sets.map((s, i) => (
              <option key={i} value={i}>
                Result {i + 1} ({s.row_count})
              </option>
            ))}
          </select>
        </label>
        <label className="diff-picker">
          Right
          <select value={rightIdx} onChange={(e) => setRightIdx(Number(e.target.value))}>
            {sets.map((s, i) => (
              <option key={i} value={i}>
                Result {i + 1} ({s.row_count})
              </option>
            ))}
          </select>
        </label>
        <label className="diff-picker">
          Key
          <select value={keyCol} onChange={(e) => setKeyCol(Number(e.target.value))}>
            {left.columns.map((c, i) => (
              <option key={i} value={i}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="diff-picker">
          Compare
          <select value={compareCol} onChange={(e) => setCompareCol(Number(e.target.value))}>
            {left.columns.map((c, i) => (
              <option key={i} value={i}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <button
          className={`btn ${showIdentical ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setShowIdentical((v) => !v)}
        >
          {showIdentical ? 'Hide identical' : 'Show identical'}
        </button>
      </div>

      <div className="diff-legend">
        <span className="diff-key diff-key-changed">{diff.counts.changed} changed</span>
        <span className="diff-key diff-key-right">{diff.counts.rightOnly} right only</span>
        <span className="diff-key diff-key-left">{diff.counts.leftOnly} left only</span>
        <span className="diff-key diff-key-same">{diff.counts.identical} identical</span>
      </div>

      <div className="diff-grid">
        <div className="diff-row diff-head">
          <span>{left.columns[keyCol]}</span>
          <span>Left · {left.columns[compareCol]}</span>
          <span>Right · {right.columns[compareCol] ?? left.columns[compareCol]}</span>
          <span className="diff-num">Δ</span>
        </div>

        {visible.map((r, i) => (
          <div key={`${r.key}-${i}`} className={`diff-row diff-${r.kind}`}>
            <span className="mono diff-keycell">{r.key || '—'}</span>
            <span className="mono">{r.left ? val(r.left[compareCol]) || '—' : '—'}</span>
            <span className="mono">{r.right ? val(r.right[compareCol]) || '—' : '—'}</span>
            <span className={`mono diff-num ${r.delta && r.delta > 0 ? 'up' : r.delta && r.delta < 0 ? 'down' : ''}`}>
              {r.delta === null ? (r.kind === 'left-only' ? 'left only' : r.kind === 'right-only' ? 'new' : '—') : fmtDelta(r.delta)}
            </span>
          </div>
        ))}

        {visible.length === 0 && <div className="diff-none">No differences to show.</div>}
      </div>

      <div className="diff-footer">
        {drift}
        {diff.truncated && (
          <span className="diff-warn">
            {' '}
            Compared the first {MAX_ROWS.toLocaleString()} rows of each side only.
          </span>
        )}
      </div>
    </div>
  );
}

export default DataDiff;
