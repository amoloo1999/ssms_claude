import { useEffect, useState } from 'react';
import { AppContext } from '../App';
import { getMyGrants, getMyRequests, requestAccess } from '../services/api';
import { TablePermission, AccessRequest } from '../types';
import { VscRefresh, VscAdd } from 'react-icons/vsc';
import './AdminPage.css';

interface Props {
  ctx: AppContext;
}

function MyAccessPage({ ctx }: Props) {
  const [grants, setGrants] = useState<TablePermission[]>([]);
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<{
    server_id: number;
    scope: 'table' | 'database' | 'server';
    database: string;
    schema_name: string;
    table_name: string;
    reason: string;
  }>({
    server_id: ctx.servers[0]?.id || 0,
    scope: 'table',
    database: '',
    schema_name: 'dbo',
    table_name: '',
    reason: '',
  });

  const refresh = async () => {
    setLoading(true);
    try {
      const [g, r] = await Promise.all([getMyGrants(), getMyRequests()]);
      setGrants(g);
      setRequests(r);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const serverName = (id: number) =>
    ctx.servers.find((s) => s.id === id)?.name || `server #${id}`;

  const renderTarget = (g: { database: string; schema_name: string; table_name: string }) => {
    if (g.database === '*') return <em>(entire server)</em>;
    if (g.table_name === '*' && g.schema_name === '*') {
      return (
        <>
          <code>[{g.database}]</code> <em>(entire database)</em>
        </>
      );
    }
    return (
      <code>
        [{g.database}].[{g.schema_name}].[{g.table_name}]
      </code>
    );
  };

  const handleSubmit = async () => {
    if (form.scope !== 'server' && !form.database) {
      alert('Database is required for table and database requests.');
      return;
    }
    if (form.scope === 'table' && !form.table_name) {
      alert('Table name is required for table requests.');
      return;
    }
    try {
      await requestAccess(form);
      setForm({ ...form, table_name: '', reason: '' });
      setShowForm(false);
      await refresh();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const pending = requests.filter((r) => r.status === 'pending');

  return (
    <div className="admin-page">
      <div className="admin-header">
        <h2>My Access</h2>
        <button className="action-btn" onClick={refresh} disabled={loading}>
          <VscRefresh /> Refresh
        </button>
      </div>

      <section className="admin-section">
        <div className="admin-subhead">
          <h3>Request New Table Access</h3>
          <button className="action-btn" onClick={() => setShowForm((v) => !v)}>
            <VscAdd /> {showForm ? 'Cancel' : 'New Request'}
          </button>
        </div>
        {showForm && (
          <div className="grant-form">
            <select
              value={form.scope}
              onChange={(e) =>
                setForm({ ...form, scope: e.target.value as 'table' | 'database' | 'server' })
              }
              title="Scope"
            >
              <option value="table">Table</option>
              <option value="database">Database</option>
              <option value="server">Server</option>
            </select>
            <select
              value={form.server_id}
              onChange={(e) => setForm({ ...form, server_id: Number(e.target.value) })}
            >
              {ctx.servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {form.scope !== 'server' && (
              <input
                placeholder="database"
                value={form.database}
                onChange={(e) => setForm({ ...form, database: e.target.value })}
              />
            )}
            {form.scope === 'table' && (
              <input
                placeholder="schema (default dbo)"
                value={form.schema_name}
                onChange={(e) => setForm({ ...form, schema_name: e.target.value })}
              />
            )}
            {form.scope === 'table' && (
              <input
                placeholder="table"
                value={form.table_name}
                onChange={(e) => setForm({ ...form, table_name: e.target.value })}
              />
            )}
            <input
              placeholder="reason (optional)"
              value={form.reason}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              style={{ flex: 1, minWidth: 200 }}
            />
            <button className="btn-approve" onClick={handleSubmit}>
              Submit
            </button>
          </div>
        )}
      </section>

      <section className="admin-section">
        <h3>Pending Requests ({pending.length})</h3>
        {pending.length === 0 ? (
          <div className="admin-empty">No pending requests.</div>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th>Target</th>
                <th>Reason</th>
                <th>Requested</th>
              </tr>
            </thead>
            <tbody>
              {pending.map((r) => (
                <tr key={r.id}>
                  <td>
                    {serverName(r.server_id)} / {renderTarget(r)}
                  </td>
                  <td>{r.reason || <em>(no reason)</em>}</td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="admin-section">
        <h3>Granted Tables ({grants.length})</h3>
        {grants.length === 0 ? (
          <div className="admin-empty">
            You don't have access to any tables yet. Use the AI Assistant to find data, then click
            "Request access" when prompted, or submit a request above.
          </div>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th>Server</th>
                <th>Table</th>
                <th>Granted by</th>
                <th>Granted at</th>
              </tr>
            </thead>
            <tbody>
              {grants.map((g) => (
                <tr key={g.id}>
                  <td>{serverName(g.server_id)}</td>
                  <td>{renderTarget(g)}</td>
                  <td>{g.granted_by}</td>
                  <td>{new Date(g.granted_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {requests.filter((r) => r.status !== 'pending').length > 0 && (
        <section className="admin-section">
          <h3>Past Requests</h3>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Target</th>
                <th>Status</th>
                <th>Decided by</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {requests
                .filter((r) => r.status !== 'pending')
                .map((r) => (
                  <tr key={r.id} className={`status-${r.status}`}>
                    <td>{renderTarget(r)}</td>
                    <td>{r.status}</td>
                    <td>{r.decided_by}</td>
                    <td>{r.decision_note}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

export default MyAccessPage;
