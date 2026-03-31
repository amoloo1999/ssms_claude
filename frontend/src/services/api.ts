import axios from 'axios';

const api = axios.create({
  baseURL: '',
  withCredentials: true,
});

// Redirect to login on 401
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      window.location.href = '/auth/login';
    }
    return Promise.reject(err);
  }
);

// ── Auth ──

export const getMe = () => api.get('/auth/me').then((r) => r.data);
export const logout = () => api.get('/auth/logout');

// ── Servers ──

export const getServers = () => api.get('/api/servers/').then((r) => r.data);
export const createServer = (data: any) => api.post('/api/servers/', data).then((r) => r.data);
export const updateServer = (id: number, data: any) => api.put(`/api/servers/${id}`, data).then((r) => r.data);
export const deleteServer = (id: number) => api.delete(`/api/servers/${id}`).then((r) => r.data);
export const testConnection = (id: number) => api.post(`/api/servers/${id}/test`).then((r) => r.data);

// ── Explorer ──

export const getDatabases = (serverId: number) =>
  api.get(`/api/explorer/servers/${serverId}/databases`).then((r) => r.data);

export const getTables = (serverId: number, database: string) =>
  api.get(`/api/explorer/servers/${serverId}/databases/${database}/tables`).then((r) => r.data);

export const getViews = (serverId: number, database: string) =>
  api.get(`/api/explorer/servers/${serverId}/databases/${database}/views`).then((r) => r.data);

export const getProcedures = (serverId: number, database: string) =>
  api.get(`/api/explorer/servers/${serverId}/databases/${database}/procedures`).then((r) => r.data);

export const getFunctions = (serverId: number, database: string) =>
  api.get(`/api/explorer/servers/${serverId}/databases/${database}/functions`).then((r) => r.data);

export const getTableColumns = (serverId: number, database: string, schema: string, table: string) =>
  api.get(`/api/explorer/servers/${serverId}/databases/${database}/tables/${schema}.${table}/columns`).then((r) => r.data);

export const getTableIndexes = (serverId: number, database: string, schema: string, table: string) =>
  api.get(`/api/explorer/servers/${serverId}/databases/${database}/tables/${schema}.${table}/indexes`).then((r) => r.data);

// ── Query ──

export const executeQuery = (serverId: number, database: string, sql: string) =>
  api.post('/api/query/execute', { server_id: serverId, database, sql }).then((r) => r.data);

// ── Tables ──

export const getTableData = (
  serverId: number,
  database: string,
  schema: string,
  table: string,
  page = 1,
  pageSize = 100,
  sortColumn?: string,
  sortDirection?: string
) => {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (sortColumn) params.set('sort_column', sortColumn);
  if (sortDirection) params.set('sort_direction', sortDirection);
  return api
    .get(`/api/tables/servers/${serverId}/databases/${database}/${schema}.${table}/data?${params}`)
    .then((r) => r.data);
};

export const editCell = (data: {
  server_id: number;
  database: string;
  schema_name: string;
  table: string;
  primary_key_columns: string[];
  primary_key_values: any[];
  column: string;
  new_value: string | null;
}) => api.put('/api/tables/edit', data).then((r) => r.data);

export const insertRow = (serverId: number, database: string, schema: string, table: string, rowData: any) =>
  api.post(`/api/tables/servers/${serverId}/databases/${database}/${schema}.${table}/row`, rowData).then((r) => r.data);

// ── Export ──

export const exportData = async (serverId: number, database: string, sql: string, format: string) => {
  const response = await api.post(
    '/api/export/download',
    { server_id: serverId, database, sql, format },
    { responseType: 'blob' }
  );
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', `export.${format}`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

export default api;
