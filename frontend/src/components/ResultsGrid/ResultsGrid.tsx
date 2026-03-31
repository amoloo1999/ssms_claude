import { useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';
import './ResultsGrid.css';

interface Props {
  columns: string[];
  rows: any[][];
}

function ResultsGrid({ columns, rows }: Props) {
  const columnDefs = useMemo(
    () =>
      columns.map((col, idx) => ({
        headerName: col,
        field: String(idx),
        sortable: true,
        filter: true,
        resizable: true,
        minWidth: 100,
      })),
    [columns]
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
