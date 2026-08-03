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

export const executeQuery = (
  serverId: number,
  database: string,
  sql: string,
  queryId?: string,
  signal?: AbortSignal,
) =>
  api
    .post(
      '/api/query/execute',
      { server_id: serverId, database, sql, query_id: queryId },
      { signal },
    )
    .then((r) => r.data);

/**
 * Estimated execution plan. Does not run the statement — the server returns the
 * plan instead of executing it.
 */
export const getQueryPlan = (serverId: number, database: string, sql: string) =>
  api
    .post('/api/query/plan', { server_id: serverId, database, sql })
    .then((r) => r.data);

export const getForeignKeys = (
  serverId: number,
  database: string,
  schema: string,
  table: string,
) =>
  api
    .get(
      `/api/explorer/servers/${serverId}/databases/${database}/tables/${schema}.${table}/foreign-keys`,
    )
    .then((r) => r.data);

// Ask the server to abort an in-flight query by its client-generated id.
export const cancelQuery = (queryId: string) =>
  api.post('/api/query/cancel', { query_id: queryId }).then((r) => r.data);

// ── AI ──

export const aiGenerate = (serverId: number, database: string, prompt: string, currentSql?: string) =>
  api
    .post('/api/ai/generate', { server_id: serverId, database, prompt, current_sql: currentSql })
    .then((r) => r.data);

export const aiFix = (serverId: number, database: string, sql: string, error: string) =>
  api.post('/api/ai/fix', { server_id: serverId, database, sql, error }).then((r) => r.data);

export const aiFindData = (serverId: number, prompt: string) =>
  api.post('/api/ai/find-data', { server_id: serverId, prompt }).then((r) => r.data);

export const getSchemaSnapshot = (serverId: number, database: string) =>
  api
    .get(`/api/explorer/servers/${serverId}/databases/${database}/schema-snapshot`)
    .then((r) => r.data);

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

// ── Permissions ──

export const getMyGrants = () => api.get('/api/permissions/me').then((r) => r.data);
export const getMyRequests = () => api.get('/api/permissions/my-requests').then((r) => r.data);
export const requestAccess = (data: {
  server_id: number;
  scope?: 'table' | 'database' | 'server';
  database?: string;
  schema_name?: string;
  table_name?: string;
  reason?: string;
}) => api.post('/api/permissions/request', data).then((r) => r.data);

export const listAccessRequests = (status?: string) =>
  api.get('/api/permissions/requests', { params: status ? { status } : {} }).then((r) => r.data);
export const approveRequest = (id: number, note: string = '') =>
  api.post(`/api/permissions/requests/${id}/approve`, { note }).then((r) => r.data);
export const denyRequest = (id: number, note: string = '') =>
  api.post(`/api/permissions/requests/${id}/deny`, { note }).then((r) => r.data);
export const listAllGrants = (userEmail?: string) =>
  api
    .get('/api/permissions/grants', { params: userEmail ? { user_email: userEmail } : {} })
    .then((r) => r.data);
export const createGrant = (data: {
  user_email: string;
  server_id: number;
  scope?: 'table' | 'database' | 'server';
  database?: string;
  schema_name?: string;
  table_name?: string;
}) => api.post('/api/permissions/grants', data).then((r) => r.data);
export const revokeGrant = (id: number) =>
  api.delete(`/api/permissions/grants/${id}`).then((r) => r.data);

// ── History (per-user) ──

export const getHistory = (search?: string, limit = 200) =>
  api
    .get('/api/history', { params: { ...(search ? { search } : {}), limit } })
    .then((r) => r.data);

export const clearHistory = () => api.delete('/api/history').then((r) => r.data);

// ── Snippets ──

export const getSnippets = () => api.get('/api/snippets').then((r) => r.data);

export const createSnippet = (data: {
  name: string;
  sql: string;
  description?: string;
  is_shared?: boolean;
}) => api.post('/api/snippets', data).then((r) => r.data);

export const updateSnippet = (
  id: number,
  data: { name?: string; sql?: string; description?: string; is_shared?: boolean },
) => api.put(`/api/snippets/${id}`, data).then((r) => r.data);

export const deleteSnippet = (id: number) =>
  api.delete(`/api/snippets/${id}`).then((r) => r.data);

export const markSnippetUsed = (id: number) =>
  api.post(`/api/snippets/${id}/used`).then((r) => r.data);

// ── Operations (sessions / kill / audit) ──

export const getSessions = (serverId: number) =>
  api.get(`/api/ops/servers/${serverId}/sessions`).then((r) => r.data);

export const killSession = (serverId: number, sessionId: number, reason: string) =>
  api
    .post('/api/ops/kill', { server_id: serverId, session_id: sessionId, reason })
    .then((r) => r.data);

export const getAudit = (params?: { event_type?: string; search?: string }) =>
  api.get('/api/ops/audit', { params: params || {} }).then((r) => r.data);

// ── Schedules ──

export const getSchedules = () => api.get('/api/schedules').then((r) => r.data);

export const createSchedule = (data: any) =>
  api.post('/api/schedules', data).then((r) => r.data);

export const updateSchedule = (id: number, data: any) =>
  api.put(`/api/schedules/${id}`, data).then((r) => r.data);

export const deleteSchedule = (id: number) =>
  api.delete(`/api/schedules/${id}`).then((r) => r.data);

export const getScheduleRuns = (id: number) =>
  api.get(`/api/schedules/${id}/runs`).then((r) => r.data);

// ── Export ──

export const exportData = async (serverId: number, database: string, sql: string, format: string) => {
  try {
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
  } catch (err: any) {
    const detail = err.response?.data;
    if (detail instanceof Blob) {
      const text = await detail.text();
      try {
        const json = JSON.parse(text);
        throw new Error(json.detail || json.error || text);
      } catch (parseErr) {
        if (parseErr instanceof SyntaxError) throw new Error(text);
        throw parseErr;
      }
    }
    throw new Error(detail?.detail || err.message);
  }
};

export default api;
