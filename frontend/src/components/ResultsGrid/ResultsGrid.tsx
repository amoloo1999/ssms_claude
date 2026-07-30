import { useCallback, useMemo, useRef } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';
import { DENSITY_ROW_HEIGHT, Density } from '../../utils/settings';
import './ResultsGrid.css';

export interface CellRef {
  rowIndex: number;
  column: string;
  value: unknown;
}

interface Props {
  columns: string[];
  rows: any[][];
  density?: Density;
  nullDisplay?: 'dash' | 'blank' | 'null';
  /** Fires as the focused cell moves, so the inspector can follow it. */
  onCellFocus?: (cell: CellRef | null) => void;
  /** Imperative handle so ↑↓ in the inspector can drive the grid. */
  gridRef?: { current: { moveFocus: (delta: number) => void } | null };
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

function ResultsGrid({
  columns,
  rows,
  density = 'compact',
  nullDisplay = 'dash',
  onCellFocus,
  gridRef,
}: Props) {
  const apiRef = useRef<any>(null);

  const nullText = nullDisplay === 'dash' ? '—' : nullDisplay === 'null' ? 'NULL' : '';

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
          valueFormatter: (p: any) =>
            p.value === null || p.value === undefined ? nullText : String(p.value),
        };
      }),
    [columns, rows, nullText]
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
    () => ({ sortable: true, filter: true, resizable: true }),
    []
  );

  const emitFocus = useCallback(
    (rowIndex: number | null, colId: string | null) => {
      if (!onCellFocus) return;
      if (rowIndex === null || colId === null) {
        onCellFocus(null);
        return;
      }
      const idx = Number(colId);
      onCellFocus({
        rowIndex,
        column: columns[idx] ?? colId,
        value: rows[rowIndex]?.[idx],
      });
    },
    [columns, rows, onCellFocus]
  );

  const onGridReady = useCallback(
    (p: any) => {
      apiRef.current = p.api;
      if (gridRef) {
        gridRef.current = {
          moveFocus: (delta: number) => {
            const focused = p.api.getFocusedCell();
            if (!focused) return;
            const next = focused.rowIndex + delta;
            if (next < 0 || next >= rows.length) return;
            p.api.setFocusedCell(next, focused.column.getColId());
            p.api.ensureIndexVisible(next, 'middle');
            emitFocus(next, focused.column.getColId());
          },
        };
      }
    },
    [gridRef, rows.length, emitFocus]
  );

  if (columns.length === 0) return null;

  return (
    <div className="results-grid ag-theme-alpine-dark">
      <AgGridReact
        columnDefs={columnDefs}
        rowData={rowData}
        defaultColDef={defaultColDef}
        rowHeight={DENSITY_ROW_HEIGHT[density]}
        animateRows={false}
        rowSelection="multiple"
        enableCellTextSelection={true}
        ensureDomOrder={true}
        suppressRowClickSelection={true}
        onGridReady={onGridReady}
        onCellFocused={(e: any) =>
          emitFocus(e.rowIndex ?? null, e.column?.getColId?.() ?? null)
        }
      />
    </div>
  );
}

export default ResultsGrid;
