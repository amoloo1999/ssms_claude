import { useEffect, useState } from 'react';
import { AppContext } from '../App';
import {
  listAccessRequests,
  approveRequest,
  denyRequest,
  listAllGrants,
  createGrant,
  revokeGrant,
} from '../services/api';
import { AccessRequest, TablePermission } from '../types';
import { VscCheck, VscClose, VscRefresh, VscTrash, VscAdd } from 'react-icons/vsc';
import './AdminPage.css';

interface Props {
  ctx: AppContext;
}

function AdminPage({ ctx }: Props) {
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [grants, setGrants] = useState<TablePermission[]>([]);
  const [loading, setLoading] = useState(false);
  const [showGrantForm, setShowGrantForm] = useState(false);
  const [grantForm, setGrantForm] = useState({
    user_email: '',
    server_id: ctx.servers[0]?.id || 0,
    database: '',
    schema_name: 'dbo',
    table_name: '',
  });

  const refresh = async () => {
    setLoading(true);
    try {
      const [reqs, gs] = await Promise.all([listAccessRequests(), listAllGrants()]);
      setRequests(reqs);
      setGrants(gs);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const pending = requests.filter((r) => r.status === 'pending');
  const decided = requests.filter((r) => r.status !== 'pending');

  const serverName = (id: number) =>
    ctx.servers.find((s) => s.id === id)?.name || `server #${id}`;

  const handleApprove = async (id: number) => {
    await approveRequest(id);
    await refresh();
  };
  const handleDeny = async (id: number) => {
    const note = prompt('Optional note for denial:') || '';
    await denyRequest(id, note);
    await refresh();
  };
  const handleRevoke = async (id: number) => {
    if (!confirm('Revoke this grant?')) return;
    await revokeGrant(id);
    await refresh();
  };

  const handleCreateGrant = async () => {
    if (!grantForm.user_email || !grantForm.database || !grantForm.table_name) {
      alert('Email, database, and table name are required.');
      return;
    }
    await createGrant(grantForm);
    setGrantForm({ ...grantForm, table_name: '' });
    setShowGrantForm(false);
    await refresh();
  };

  // Group grants by user for cleaner display
  const grantsByUser: Record<string, TablePermission[]> = {};
  for (const g of grants) {
    (grantsByUser[g.user_email] ||= []).push(g);
  }

  return (
    <div className="admin-page">
      <div className="admin-header">
        <h2>Permissions Admin</h2>
        <button className="action-btn" onClick={refresh} disabled={loading}>
          <VscRefresh /> Refresh
        </button>
      </div>

      {/* Pending requests */}
      <section className="admin-section">
        <h3>Pending Requests ({pending.length})</h3>
        {pending.length === 0 ? (
          <div className="admin-empty">No pending requests.</div>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Target</th>
                <th>Reason</th>
                <th>Requested</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {pending.map((r) => (
                <tr key={r.id}>
                  <td>{r.user_email}</td>
                  <td>
                    <code>
                      {serverName(r.server_id)} / [{r.database}].[{r.schema_name}].[{r.table_name}]
                    </code>
                  </td>
                  <td>{r.reason || <em>(no reason given)</em>}</td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                  <td className="admin-actions">
                    <button className="btn-approve" onClick={() => handleApprove(r.id)}>
                      <VscCheck /> Approve
                    </button>
                    <button className="btn-deny" onClick={() => handleDeny(r.id)}>
                      <VscClose /> Deny
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Existing grants */}
      <section className="admin-section">
        <div className="admin-subhead">
          <h3>Active Grants ({grants.length})</h3>
          <button className="action-btn" onClick={() => setShowGrantForm((v) => !v)}>
            <VscAdd /> Direct Grant
          </button>
        </div>
        {showGrantForm && (
          <div className="grant-form">
            <input
              placeholder="user@williamwarren.com"
              value={grantForm.user_email}
              onChange={(e) => setGrantForm({ ...grantForm, user_email: e.target.value })}
            />
            <select
              value={grantForm.server_id}
              onChange={(e) => setGrantForm({ ...grantForm, server_id: Number(e.target.value) })}
            >
              {ctx.servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <input
              placeholder="database"
              value={grantForm.database}
              onChange={(e) => setGrantForm({ ...grantForm, database: e.target.value })}
            />
            <input
              placeholder="schema"
              value={grantForm.schema_name}
              onChange={(e) => setGrantForm({ ...grantForm, schema_name: e.target.value })}
            />
            <input
              placeholder="table"
              value={grantForm.table_name}
              onChange={(e) => setGrantForm({ ...grantForm, table_name: e.target.value })}
            />
            <button className="btn-approve" onClick={handleCreateGrant}>
              <VscCheck /> Grant
            </button>
          </div>
        )}
        {Object.keys(grantsByUser).length === 0 ? (
          <div className="admin-empty">No grants yet.</div>
        ) : (
          Object.entries(grantsByUser).map(([email, userGrants]) => (
            <div key={email} className="user-grants-block">
              <div className="user-grants-header">{email}</div>
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Server</th>
                    <th>Table</th>
                    <th>Granted by</th>
                    <th>Granted at</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {userGrants.map((g) => (
                    <tr key={g.id}>
                      <td>{serverName(g.server_id)}</td>
                      <td>
                        <code>
                          [{g.database}].[{g.schema_name}].[{g.table_name}]
                        </code>
                      </td>
                      <td>{g.granted_by}</td>
                      <td>{new Date(g.granted_at).toLocaleString()}</td>
                      <td>
                        <button className="btn-deny" onClick={() => handleRevoke(g.id)}>
                          <VscTrash /> Revoke
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        )}
      </section>

      {/* Decided requests */}
      {decided.length > 0 && (
        <section className="admin-section">
          <h3>Decided Requests ({decided.length})</h3>
          <table className="admin-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Target</th>
                <th>Status</th>
                <th>Decided by</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {decided.slice(0, 50).map((r) => (
                <tr key={r.id} className={`status-${r.status}`}>
                  <td>{r.user_email}</td>
                  <td>
                    <code>
                      {serverName(r.server_id)} / [{r.database}].[{r.schema_name}].[{r.table_name}]
                    </code>
                  </td>
                  <td>{r.status}</td>
                  <td>{r.decided_by || ''}</td>
                  <td>{r.decision_note || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

export default AdminPage;
