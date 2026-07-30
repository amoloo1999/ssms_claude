import { useEffect, useMemo, useState } from 'react';

import './ResultsChart.css';

interface Props {
  columns: string[];
  rows: any[][];
}

type ChartType = 'bars' | 'line' | 'scatter';

const fmt = (n: number): string => {
  if (!isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
};

/**
 * Chart the active result set — handoff screen 2C.
 *
 * Drawn with plain divs and one inline SVG rather than pulling in a charting
 * library: the three shapes here are simple, and the bundle is already over
 * the 500kB warning.
 */
function ResultsChart({ columns, rows }: Props) {
  const numericCols = useMemo(
    () =>
      columns
        .map((c, i) => ({ name: c, index: i }))
        .filter(({ index }) =>
          rows.some((r) => typeof r[index] === 'number') &&
          rows.every((r) => r[index] === null || r[index] === undefined || typeof r[index] === 'number')
        ),
    [columns, rows]
  );

  const [type, setType] = useState<ChartType>('bars');
  const [xIdx, setXIdx] = useState(0);
  const [yIdx, setYIdx] = useState(() => (numericCols[0]?.index ?? 1));

  // Re-seed the axis pickers when a new result set arrives with different shape.
  useEffect(() => {
    setXIdx(0);
    setYIdx(numericCols[0]?.index ?? 1);
  }, [columns.join('|')]);

  const points = useMemo(() => {
    return rows
      .map((r) => ({ label: r[xIdx] == null ? '—' : String(r[xIdx]), value: Number(r[yIdx]) }))
      .filter((p) => isFinite(p.value));
  }, [rows, xIdx, yIdx]);

  if (!columns.length || !rows.length) {
    return <div className="chart-empty">Run a query to chart its results.</div>;
  }

  if (!numericCols.length) {
    return (
      <div className="chart-empty">
        Nothing to chart — this result has no numeric column.
      </div>
    );
  }

  const max = Math.max(...points.map((p) => p.value), 0);
  const min = Math.min(...points.map((p) => p.value), 0);
  const span = max - min || 1;
  const sum = points.reduce((a, p) => a + p.value, 0);
  const mean = points.length ? sum / points.length : 0;

  // Bars step down the accent ramp as values fall, per the handoff.
  const rampFor = (value: number): string => {
    const t = (value - min) / span;
    if (t > 0.75) return 'var(--color-accent)';
    if (t > 0.5) return 'var(--color-accent-600)';
    if (t > 0.25) return 'var(--color-accent-700)';
    return 'var(--color-accent-800)';
  };

  return (
    <div className="chart">
      <div className="chart-controls">
        <div className="seg">
          {(['bars', 'line', 'scatter'] as ChartType[]).map((t) => (
            <button
              key={t}
              className={`seg-opt ${type === t ? 'active' : ''}`}
              onClick={() => setType(t)}
            >
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>

        <label className="chart-picker">
          X
          <select value={xIdx} onChange={(e) => setXIdx(Number(e.target.value))}>
            {columns.map((c, i) => (
              <option key={i} value={i}>
                {c}
              </option>
            ))}
          </select>
        </label>

        <label className="chart-picker">
          Y
          <select value={yIdx} onChange={(e) => setYIdx(Number(e.target.value))}>
            {numericCols.map((c) => (
              <option key={c.index} value={c.index}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="chart-title">
        {columns[yIdx]} by {columns[xIdx]}
      </div>
      <div className="chart-subtitle">
        {points.length} of {rows.length} row{rows.length === 1 ? '' : 's'} plotted
      </div>

      {type === 'bars' && (
        <div className="chart-bars">
          {points.map((p, i) => (
            <div key={i} className="chart-bar-row">
              <span className="chart-bar-label" title={p.label}>
                {p.label}
              </span>
              <div className="chart-bar-track">
                <div
                  className="chart-bar"
                  style={{
                    width: `${Math.max(((p.value - min) / span) * 100, 0.5)}%`,
                    background: rampFor(p.value),
                  }}
                />
              </div>
              <span className="chart-bar-value">{fmt(p.value)}</span>
            </div>
          ))}
        </div>
      )}

      {(type === 'line' || type === 'scatter') && (
        <LineOrScatter points={points} type={type} min={min} span={span} />
      )}

      <div className="chart-footer">
        Sum {fmt(sum)} · mean {fmt(mean)} · max {fmt(max)} · min {fmt(min)}
      </div>
    </div>
  );
}

function LineOrScatter({
  points,
  type,
  min,
  span,
}: {
  points: { label: string; value: number }[];
  type: 'line' | 'scatter';
  min: number;
  span: number;
}) {
  const W = 1000;
  const H = 320;
  const PAD = 8;

  const x = (i: number) =>
    points.length === 1 ? W / 2 : PAD + (i / (points.length - 1)) * (W - PAD * 2);
  const y = (v: number) => H - PAD - ((v - min) / span) * (H - PAD * 2);

  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(p.value)}`).join(' ');

  return (
    <div className="chart-svg-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="chart-svg">
        {type === 'line' && (
          <path d={path} fill="none" stroke="var(--color-accent)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        )}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={x(i)}
            cy={y(p.value)}
            r={type === 'scatter' ? 4 : 3}
            fill="var(--color-accent-300)"
          >
            <title>
              {p.label}: {fmt(p.value)}
            </title>
          </circle>
        ))}
      </svg>
      <div className="chart-axis">
        {points.length > 0 && <span>{points[0].label}</span>}
        {points.length > 1 && <span>{points[points.length - 1].label}</span>}
      </div>
    </div>
  );
}

export default ResultsChart;
