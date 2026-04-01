import { useState, useEffect, useCallback } from 'react';
import { AppContext } from '../../App';
import { getTableData, getTableColumns, getTableIndexes, editCell, exportData } from '../../services/api';
import { ColumnInfo, IndexInfo, TableDataResult } from '../../types';
import ResultsGrid from '../ResultsGrid/ResultsGrid';
import { VscRefresh, VscExport, VscInfo } from 'react-icons/vsc';
import './TableBrowser.css';

interface Props {
  ctx: AppContext;
}

function TableBrowser({ ctx }: Props) {
  const [data, setData] = useState<TableDataResult | null>(null);
  const [columns, setColumns] = useState<ColumnInfo[]>([]);
  const [indexes, setIndexes] = useState<IndexInfo[]>([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [showSchema, setShowSchema] = useState(false);

  const t = ctx.activeTable!;

  const loadData = useCallback(async (p: number = 1) => {
    setLoading(true);
    try {
      const res = await getTableData(t.serverId, t.database, t.schema, t.table, p);
      setData(res);
      setPage(p);
    } catch (err) {
      console.error('Failed to load table data:', err);
    } finally {
      setLoading(false);
    }
  }, [t.serverId, t.database, t.schema, t.table]);

  const loadSchema = useCallback(async () => {
    try {
      const [colRes, idxRes] = await Promise.all([
        getTableColumns(t.serverId, t.database, t.schema, t.table),
        getTableIndexes(t.serverId, t.database, t.schema, t.table),
      ]);
      setColumns(colRes.columns || []);
      setIndexes(idxRes.indexes || []);
    } catch (err) {
      console.error('Failed to load schema:', err);
    }
  }, [t.serverId, t.database, t.schema, t.table]);

  useEffect(() => {
    loadData(1);
    loadSchema();
  }, [loadData, loadSchema]);

  const handleExport = async (format: 'csv' | 'xlsx') => {
    const sql = `SELECT * FROM [${t.schema}].[${t.table}]`;
    try {
      await exportData(t.serverId, t.database, sql, format);
    } catch (err: any) {
      alert('Export failed: ' + err.message);
    }
  };

  const pkColumns = columns.filter((c) => c.is_primary_key).map((c) => c.name);

  return (
    <div className="table-browser">
      {/* Header */}
      <div className="table-header">
        <div className="table-title">
          <span className="table-name">[{t.schema}].[{t.table}]</span>
          <span className="table-db">{t.database}</span>
          {data && <span className="table-count">{data.total_rows.toLocaleString()} rows</span>}
        </div>
        <div className="table-actions">
          <button className="action-btn" onClick={() => setShowSchema(!showSchema)}>
            <VscInfo /> Schema
          </button>
          <button className="action-btn" onClick={() => loadData(page)}>
            <VscRefresh /> Refresh
          </button>
          <button className="action-btn" onClick={() => handleExport('csv')}>
            <VscExport /> CSV
          </button>
          <button className="action-btn" onClick={() => handleExport('xlsx')}>
            <VscExport /> Excel
          </button>
        </div>
      </div>

      {/* Schema Panel */}
      {showSchema && (
        <div className="schema-panel">
          <div className="schema-section">
            <h3>Columns</h3>
            <table className="schema-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Nullable</th>
                  <th>PK</th>
                  <th>Default</th>
                </tr>
              </thead>
              <tbody>
                {columns.map((col) => (
                  <tr key={col.name}>
                    <td>{col.name}</td>
                    <td>{col.data_type}{col.max_length ? `(${col.max_length})` : ''}</td>
                    <td>{col.is_nullable ? 'YES' : 'NO'}</td>
                    <td>{col.is_primary_key ? 'PK' : ''}</td>
                    <td>{col.default_value || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {indexes.length > 0 && (
            <div className="schema-section">
              <h3>Indexes</h3>
              <table className="schema-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Unique</th>
                    <th>Columns</th>
                  </tr>
                </thead>
                <tbody>
                  {indexes.map((idx) => (
                    <tr key={idx.name}>
                      <td>{idx.name}</td>
                      <td>{idx.type}</td>
                      <td>{idx.is_unique ? 'YES' : 'NO'}</td>
                      <td>{idx.columns}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Data Grid */}
      <div className="table-data">
        {loading ? (
          <div className="table-loading">Loading...</div>
        ) : data ? (
          <>
            <ResultsGrid columns={data.columns} rows={data.rows} />
            {/* Pagination */}
            <div className="pagination">
              <button
                disabled={page <= 1}
                onClick={() => loadData(page - 1)}
              >
                Previous
              </button>
              <span>
                Page {data.page} of {data.total_pages}
              </span>
              <button
                disabled={page >= data.total_pages}
                onClick={() => loadData(page + 1)}
              >
                Next
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

export default TableBrowser;
