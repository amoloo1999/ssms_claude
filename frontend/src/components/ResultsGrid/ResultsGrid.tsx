import { useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';
import './ResultsGrid.css';

interface Props {
  columns: string[];
  rows: any[][];
}

/**
 * Decide whether a column reads as a figure.
 *
 * The handoff sets text in Inter and numbers in right-aligned tabular mono, so
 * columns of figures line up on the decimal. The API hands back untyped rows,
 * so the type is sampled from the data: up to 50 non-null values, all numeric.
 */
function isNumericColumn(rows: any[][], idx: number): boolean {
  let seen = 0;
  for (const row of rows) {
    const v = row[idx];
    if (v === null || v === undefined || v === '') continue;
    if (typeof v !== 'number') return false;
    if (++seen >= 50) break;
  }
  return seen > 0;
}

function ResultsGrid({ columns, rows }: Props) {
  const columnDefs = useMemo(
    () =>
      columns.map((col, idx) => {
        const numeric = isNumericColumn(rows, idx);
        return {
          headerName: col,
          field: String(idx),
          sortable: true,
          filter: true,
          resizable: true,
          minWidth: 100,
          cellClass: numeric ? 'cell-num' : 'cell-text',
          headerClass: numeric ? 'header-num' : undefined,
        };
      }),
    [columns, rows]
  );

  const rowData = useMemo(
    () =>
      rows.map((row) => {
        const obj: Record<string, any> = {};
        row.forEach((val, idx) => {
          obj[String(idx)] = val;
        });
        return obj;
      }),
    [rows]
  );

  const defaultColDef = useMemo(
    () => ({
      sortable: true,
      filter: true,
      resizable: true,
    }),
    []
  );

  if (columns.length === 0) return null;

  return (
    <div className="results-grid ag-theme-alpine-dark">
      <AgGridReact
        columnDefs={columnDefs}
        rowData={rowData}
        defaultColDef={defaultColDef}
        animateRows={false}
        rowSelection="multiple"
        enableCellTextSelection={true}
        ensureDomOrder={true}
        suppressRowClickSelection={true}
      />
    </div>
  );
}

export default ResultsGrid;
