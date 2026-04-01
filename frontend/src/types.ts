export interface Server {
  id: number;
  name: string;
  host: string;
  port: number;
  username: string;
  description: string;
  from_config: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface DatabaseItem {
  name: string;
}

export interface TableItem {
  schema: string;
  name: string;
}

export interface ColumnInfo {
  name: string;
  data_type: string;
  max_length: number | null;
  is_nullable: boolean;
  default_value: string | null;
  is_primary_key: boolean;
  ordinal_position: number;
}

export interface IndexInfo {
  name: string;
  type: string;
  is_unique: boolean;
  is_primary_key: boolean;
  columns: string;
}

export interface QueryResult {
  columns: string[];
  rows: any[][];
  row_count: number;
  execution_time_ms: number;
  error: string | null;
}

export interface TableDataResult extends QueryResult {
  page: number;
  page_size: number;
  total_rows: number;
  total_pages: number;
}

export interface User {
  email: string;
  name: string;
  picture: string;
}

export interface TreeNode {
  id: string;
  label: string;
  type: 'server' | 'database' | 'folder' | 'table' | 'view' | 'procedure' | 'function' | 'column';
  children?: TreeNode[];
  data?: any;
  isLoading?: boolean;
  isExpanded?: boolean;
}
