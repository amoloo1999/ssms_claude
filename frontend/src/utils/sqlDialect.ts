import { Dialect } from '../types';

// Per-engine identifier quote characters so generated/inserted SQL is valid for
// the selected server (e.g. "col" on Postgres, `col` on MySQL, [col] on MSSQL).
export const QUOTES: Record<Dialect, [string, string]> = {
  mssql: ['[', ']'],
  postgres: ['"', '"'],
  mysql: ['`', '`'],
  snowflake: ['"', '"'],
};

/** Quote an identifier for `dialect`, doubling the closing char to escape it. */
export function quoteIdent(name: string, dialect: Dialect = 'mssql'): string {
  const [open, close] = QUOTES[dialect] || QUOTES.mssql;
  return `${open}${name.split(close).join(close + close)}${close}`;
}

/** `[schema].[table]` for MSSQL, `"schema"."table"` for Postgres, etc. */
export function quoteQualified(dialect: Dialect, ...parts: string[]): string {
  return parts.filter(Boolean).map((p) => quoteIdent(p, dialect)).join('.');
}

/**
 * Row-limited SELECT in the syntax the engine understands. T-SQL puts the cap
 * up front (`SELECT TOP n`); Postgres/MySQL/Snowflake use a trailing LIMIT.
 */
export function selectTopSql(dialect: Dialect, schema: string, table: string, limit = 1000): string {
  const target = quoteQualified(dialect, schema, table);
  if (dialect === 'mssql') return `SELECT TOP ${limit} * FROM ${target}`;
  return `SELECT * FROM ${target} LIMIT ${limit}`;
}

/** Context-menu wording — "Top" is T-SQL phrasing; others say "First". */
export function selectTopLabel(dialect: Dialect, limit = 1000): string {
  return dialect === 'mssql' ? `Select Top ${limit} Rows` : `Select First ${limit} Rows`;
}

const lit = (s: string) => `'${s.split("'").join("''")}'`;

/** Same shape as selectTopSql but with an explicit column list (SSMS "Script as SELECT"). */
export function selectColumnsSql(
  dialect: Dialect,
  schema: string,
  table: string,
  columns: string[],
  limit = 1000
): string {
  const target = quoteQualified(dialect, schema, table);
  const cols = columns.length
    ? columns.map((c) => `    ${quoteIdent(c, dialect)}`).join(',\n')
    : '    *';
  if (dialect === 'mssql') return `SELECT TOP ${limit}\n${cols}\nFROM ${target}`;
  return `SELECT\n${cols}\nFROM ${target}\nLIMIT ${limit}`;
}

export function countRowsSql(dialect: Dialect, schema: string, table: string): string {
  return `SELECT COUNT(*) AS row_count FROM ${quoteQualified(dialect, schema, table)}`;
}

/** Column metadata via INFORMATION_SCHEMA — portable across MSSQL/Postgres/MySQL. */
export function columnMetadataSql(dialect: Dialect, schema: string, table: string): string {
  void dialect;
  return [
    'SELECT column_name,',
    '       data_type,',
    '       character_maximum_length,',
    '       is_nullable,',
    '       column_default',
    'FROM information_schema.columns',
    `WHERE table_schema = ${lit(schema)}`,
    `  AND table_name = ${lit(table)}`,
    'ORDER BY ordinal_position',
  ].join('\n');
}

/**
 * Distinct values with counts for the first column — the "what's in this field?"
 * query, ready to retarget at another column.
 */
export function distinctCountsSql(
  dialect: Dialect,
  schema: string,
  table: string,
  column: string,
  limit = 100
): string {
  const target = quoteQualified(dialect, schema, table);
  const col = quoteIdent(column, dialect);
  if (dialect === 'mssql') {
    return `SELECT TOP ${limit} ${col}, COUNT(*) AS n\nFROM ${target}\nGROUP BY ${col}\nORDER BY n DESC`;
  }
  return `SELECT ${col}, COUNT(*) AS n\nFROM ${target}\nGROUP BY ${col}\nORDER BY n DESC\nLIMIT ${limit}`;
}

export function insertTemplateSql(
  dialect: Dialect,
  schema: string,
  table: string,
  columns: string[]
): string {
  const target = quoteQualified(dialect, schema, table);
  const cols = columns.map((c) => quoteIdent(c, dialect)).join(', ');
  const placeholders = columns.map((c) => `<${c}>`).join(', ');
  return `INSERT INTO ${target} (${cols})\nVALUES (${placeholders})`;
}

export function updateTemplateSql(
  dialect: Dialect,
  schema: string,
  table: string,
  columns: string[]
): string {
  const target = quoteQualified(dialect, schema, table);
  const sets = columns.map((c) => `    ${quoteIdent(c, dialect)} = <${c}>`).join(',\n');
  return `UPDATE ${target}\nSET\n${sets}\nWHERE <condition>  -- required: an unfiltered UPDATE hits every row`;
}
