export interface Server {
  id: number;
  name: string;
  host: string;
  port: number;
  username: string;
  description: string;
  from_config: boolean;
  kind: 'main' | 'gp';
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

export interface ResultSet {
  columns: string[];
  rows: any[][];
  row_count: number;
}

export interface QueryResult {
  columns: string[];
  rows: any[][];
  row_count: number;
  result_sets?: ResultSet[];
  execution_time_ms: number;
  error: string | null;
}

export interface AIResponse {
  sql: string | null;
  explanation: string;
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
  role: 'revman' | 'user';
  is_approver: boolean;
}

export interface MissingTable {
  server_id: number;
  database: string;
  schema: string;
  table: string;
}

export interface TablePermission {
  id: number;
  user_email: string;
  server_id: number;
  database: string;
  schema_name: string;
  table_name: string;
  granted_by: string;
  granted_at: string;
}

export interface AccessRequest {
  id: number;
  user_email: string;
  server_id: number;
  database: string;
  schema_name: string;
  table_name: string;
  reason: string;
  status: 'pending' | 'approved' | 'denied';
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string;
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
