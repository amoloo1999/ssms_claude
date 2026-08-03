import { useEffect, useState } from 'react';
import { getQueryPlan } from '../../services/api';
import './QueryPlan.css';

interface PlanNode {
  depth: number;
  operator: string;
  detail: string;
  cost_pct: number;
  rows: number;
}

interface MissingIndex {
  database: string;
  schema: string;
  table: string;
  impact: number;
  columns: string[];
}

interface Plan {
  supported: boolean;
  error?: string;
  nodes: PlanNode[];
  warnings: string[];
  missing_indexes: MissingIndex[];
}

interface Props {
  /** undefined until a server is picked — the effect below waits for it. */
  serverId: number | null | undefined;
  database: string;
  sql: string;
  onAskAI: () => void;
}

const fmtRows = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
};

/** Strip SQL Server's brackets for display — [dbo] reads better as dbo. */
const unbracket = (s: string) => s.replace(/^\[|\]$/g, '');

/**
 * Execution plan — handoff screen 2C, right panel.
 *
 * Fetched on demand rather than with every execution: the plan is a second
 * round trip, and most runs never open this tab.
 */
function QueryPlan({ serverId, database, sql, onAskAI }: Props) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!serverId || !database || !sql.trim()) return;
    let cancelled = false;
    setLoading(true);
    getQueryPlan(serverId, database, sql)
      .then((p) => !cancelled && setPlan(p))
      .catch((err) =>
        !cancelled &&
        setPlan({
          supported: true,
          error: err.response?.data?.detail?.detail || err.response?.data?.detail || err.message,
          nodes: [],
          warnings: [],
          missing_indexes: [],
        })
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [serverId, database, sql]);

  if (loading) return <div className="plan-empty">Reading the plan…</div>;
  if (!plan) return <div className="plan-empty">Run a query to see its plan.</div>;

  if (!plan.supported) {
    return (
      <div className="plan-empty">
        Execution plans aren’t available for this connection’s engine yet — only
        SQL Server so far.
      </div>
    );
  }

  if (plan.error) {
    return (
      <div className="plan-error">
        <strong>Could not read the plan.</strong>
        <div className="plan-error-body mono">{plan.error}</div>
      </div>
    );
  }

  if (!plan.nodes.length) return <div className="plan-empty">No plan returned.</div>;

  const totalRows = plan.nodes.reduce((a, n) => Math.max(a, n.rows), 0);
  // The operator carrying the largest share of cost is what to look at first.
  const dominant = plan.nodes.reduce((a, b) => (b.cost_pct > a.cost_pct ? b : a));
  const mi = plan.missing_indexes[0];

  const createIndexSql = mi
    ? `CREATE NONCLUSTERED INDEX IX_${unbracket(mi.table)}_${mi.columns
        .map(unbracket)
        .join('_')}\n    ON ${mi.schema}.${mi.table} (${mi.columns.join(', ')});`
    : '';

  const copyIndex = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(createIndexSql);
      } else {
        const ta = document.createElement('textarea');
        ta.value = createIndexSql;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* the statement is selectable in the card regardless */
    }
  };

  return (
    <div className="plan">
      <div className="plan-stats">
        <span>
          <span className="plan-stat-label">Operators</span>
          <span className="plan-stat-value mono">{plan.nodes.length}</span>
        </span>
        <span>
          <span className="plan-stat-label">Est. rows</span>
          <span className="plan-stat-value mono">{fmtRows(totalRows)}</span>
        </span>
        {plan.warnings.length > 0 && (
          <span>
            <span className="plan-stat-label">Warnings</span>
            <span className="plan-stat-value mono plan-warn">{plan.warnings.length}</span>
          </span>
        )}
      </div>

      <div className="plan-tree">
        {plan.nodes.map((n, i) => {
          const isDominant = n === dominant && plan.nodes.length > 1;
          return (
            <div
              key={i}
              className={`plan-row ${isDominant ? 'dominant' : ''}`}
              style={{ paddingLeft: `calc(${n.depth} * var(--space-6) + var(--space-4))` }}
            >
              <span className="plan-op">{n.operator}</span>
              <span className="plan-detail">{n.detail}</span>
              <span className="plan-bar-track">
                <span
                  className="plan-bar"
                  style={{ width: `${Math.max(n.cost_pct, 1)}%` }}
                />
              </span>
              <span className="plan-figures mono">
                {n.cost_pct}% · {fmtRows(n.rows)} rows
              </span>
            </div>
          );
        })}
      </div>

      {plan.warnings.length > 0 && (
        <div className="plan-warnings">
          {plan.warnings.map((w, i) => (
            <div key={i} className="plan-warning-row">
              {w}
            </div>
          ))}
        </div>
      )}

      {mi && (
        <div className="plan-advice">
          <div className="plan-advice-title">
            {dominant.operator} is doing most of the work
            {dominant.rows > 0 && <> over {fmtRows(dominant.rows)} rows</>}.
          </div>
          <div className="plan-advice-body">
            SQL Server estimates an index here would cut its cost by about{' '}
            {Math.round(mi.impact)}%.
          </div>
          <pre className="plan-index mono">{createIndexSql}</pre>
          <div className="plan-advice-actions">
            <button className="btn btn-secondary" onClick={copyIndex}>
              {copied ? 'Copied' : 'Copy CREATE INDEX'}
            </button>
            <button className="btn btn-primary" onClick={onAskAI}>
              ✦ Ask AI to explain
            </button>
          </div>
          <div className="plan-advice-note">
            An estimate, not a recommendation — adding an index costs write
            throughput and storage. Check the whole workload before creating one.
          </div>
        </div>
      )}
    </div>
  );
}

export default QueryPlan;
