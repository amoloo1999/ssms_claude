import { useState } from 'react';
import { AppContext } from '../../App';
import { createServer, updateServer, deleteServer, testConnection, getServers } from '../../services/api';
import { Server, Dialect } from '../../types';
import { VscAdd, VscEdit, VscTrash, VscDebugStart, VscCheck, VscClose } from 'react-icons/vsc';
import { connectionColor, connectionEnv } from '../../utils/connectionColor';
import './ServerManager.css';

interface Props {
  ctx: AppContext;
}

interface FormData {
  name: string;
  host: string;
  port: number;
  username: string;
  password: string;
  description: string;
  dialect: Dialect;
  database: string;
}

// Engine catalogue: label, default port, and whether a single database must be
// named (single-connection engines bind to one database).
const DIALECTS: { value: Dialect; label: string; port: number; needsDatabase: boolean; hostLabel: string }[] = [
  { value: 'mssql', label: 'SQL Server', port: 1433, needsDatabase: false, hostLabel: 'server.example.com' },
  { value: 'postgres', label: 'PostgreSQL / Aurora', port: 5432, needsDatabase: true, hostLabel: 'cluster.xxxx.us-west-1.rds.amazonaws.com' },
  { value: 'mysql', label: 'MySQL / Aurora', port: 3306, needsDatabase: true, hostLabel: 'cluster.xxxx.rds.amazonaws.com' },
  { value: 'snowflake', label: 'Snowflake', port: 443, needsDatabase: true, hostLabel: 'account-identifier' },
];

const dialectInfo = (d: Dialect) => DIALECTS.find((x) => x.value === d) || DIALECTS[0];

const emptyForm: FormData = {
  name: '',
  host: '',
  port: 1433,
  username: '',
  password: '',
  description: '',
  dialect: 'mssql',
  database: '',
};

function ServerManager({ ctx }: Props) {
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [form, setForm] = useState<FormData>(emptyForm);
  const [testResult, setTestResult] = useState<Record<number, { success: boolean; message: string }>>({});
  const [saving, setSaving] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const refresh = async () => {
    const servers = await getServers();
    ctx.setServers(servers);
  };

  const handleAdd = () => {
    setForm(emptyForm);
    setEditing(null);
    setShowPassword(false);
    setShowForm(true);
  };

  const handleEdit = (server: Server) => {
    setForm({
      name: server.name,
      host: server.host,
      port: server.port,
      username: server.username,
      password: '',
      description: server.description,
      dialect: server.dialect || 'mssql',
      database: server.database || '',
    });
    setEditing(server.id);
    setShowForm(true);
  };

  // Switching engine resets the port to that engine's default (unless the user
  // already typed a non-default port).
  const handleDialectChange = (dialect: Dialect) => {
    setForm((f) => {
      const prevDefault = dialectInfo(f.dialect).port;
      const port = f.port === prevDefault ? dialectInfo(dialect).port : f.port;
      return { ...f, dialect, port };
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      if (editing) {
        const data: any = { ...form };
        if (!data.password) delete data.password; // don't update if empty
        await updateServer(editing, data);
      } else {
        await createServer(form);
      }
      await refresh();
      setShowForm(false);
      setForm(emptyForm);
      setEditing(null);
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this server connection?')) return;
    await deleteServer(id);
    await refresh();
  };

  const handleTest = async (id: number) => {
    setTestResult((prev) => ({ ...prev, [id]: { success: false, message: 'Testing...' } }));
    const result = await testConnection(id);
    setTestResult((prev) => ({ ...prev, [id]: result }));
  };

  const handleTogglePolicy = async (server: Server) => {
    const next = server.write_policy === 'read_only' ? 'read_write' : 'read_only';
    // Making a connection writable again is the direction worth confirming —
    // read-only is the safe state, and this affects every user of the app.
    if (
      next === 'read_write' &&
      !confirm(
        `Allow writes on "${server.name}"?\n\n` +
          'Every RevMan will be able to run UPDATE, DELETE and DDL against this ' +
          'connection.'
      )
    ) {
      return;
    }
    try {
      await updateServer(server.id, { write_policy: next });
      await refresh();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  return (
    <div className="server-manager">
      <div className="sm-header">
        <h2>Server Manager</h2>
        <button className="sm-add-btn" onClick={handleAdd}>
          <VscAdd /> Add Server
        </button>
      </div>

      {/* Add/Edit Form */}
      {showForm && (
        <div className="sm-form">
          <h3>{editing ? 'Edit Server' : 'Add Server'}</h3>
          <div className="form-grid">
            <label>
              Name
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="My SQL Server"
              />
            </label>
            <label>
              Engine
              <select
                value={form.dialect}
                onChange={(e) => handleDialectChange(e.target.value as Dialect)}
              >
                {DIALECTS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Host
              <input
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                placeholder={dialectInfo(form.dialect).hostLabel}
              />
            </label>
            <label>
              Port
              <input
                type="number"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
              />
            </label>
            <label>
              Username
              <input
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                placeholder="sa"
              />
            </label>
            {dialectInfo(form.dialect).needsDatabase && (
              <label>
                Database
                <input
                  value={form.database}
                  onChange={(e) => setForm({ ...form, database: e.target.value })}
                  placeholder="database name (required)"
                />
              </label>
            )}
            <label>
              Password
              <div className="password-field">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  placeholder={editing ? '(leave blank to keep current)' : 'Password'}
                />
                <button
                  type="button"
                  className="toggle-password"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </div>
            </label>
            <label>
              Description
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="Optional description"
              />
            </label>
          </div>
          <div className="form-actions">
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              <VscCheck /> {saving ? 'Saving...' : 'Save'}
            </button>
            <button className="btn btn-secondary" onClick={() => setShowForm(false)}>
              <VscClose /> Cancel
            </button>
          </div>
        </div>
      )}

      {/* Server List */}
      <div className="sm-list">
        {ctx.servers.length === 0 ? (
          <div className="sm-empty">
            No servers configured yet. Click "Add Server" to get started.
          </div>
        ) : (
          ctx.servers.map((server) => (
            <div
              key={server.id}
              className="sm-card"
              /* Each connection's row carries its own colour on the left edge —
                 the same colour it shows in the connection bar and the tree. */
              style={{ boxShadow: `inset 4px 0 0 ${connectionColor(server)}` }}
            >
              <div className="sm-card-info">
                <div className="sm-card-name">
                  {server.name}
                  <span className="tag tag-neutral">{connectionEnv(server).toLowerCase()}</span>
                  {server.write_policy === 'read_only' && (
                    <span className="tag tag-outline" title="Writes are refused for every user, including RevMan">
                      read-only
                    </span>
                  )}
                  {server.from_config && <span className="sm-badge">shared</span>}
                </div>
                <div className="sm-card-details">
                  {dialectInfo(server.dialect || 'mssql').label} &middot; {server.host}:{server.port}
                  {server.database ? `/${server.database}` : ''} &middot; {server.username}
                  {server.description && ` · ${server.description}`}
                </div>
                {testResult[server.id] && (
                  <div className={`sm-test-result ${testResult[server.id].success ? 'success' : 'error'}`}>
                    {testResult[server.id].message}
                  </div>
                )}
              </div>
              <div className="sm-card-actions">
                {/* Settable here rather than only in config.yaml, so marking a
                    connection read-only doesn't require editing a
                    credential-bearing file on the production box. */}
                <button
                  className={`sm-policy-btn ${server.write_policy === 'read_only' ? 'on' : ''}`}
                  onClick={() => handleTogglePolicy(server)}
                  title={
                    server.write_policy === 'read_only'
                      ? 'Read-only: writes refused for every user. Click to allow writes.'
                      : 'Writes allowed. Click to make this connection read-only for everyone.'
                  }
                >
                  {server.write_policy === 'read_only' ? 'Read-only' : 'Writable'}
                </button>
                <button className="sm-icon-btn" onClick={() => handleTest(server.id)} title="Test Connection">
                  <VscDebugStart />
                </button>
                {!server.from_config && (
                  <>
                    <button className="sm-icon-btn" onClick={() => handleEdit(server)} title="Edit">
                      <VscEdit />
                    </button>
                    <button className="sm-icon-btn danger" onClick={() => handleDelete(server.id)} title="Delete">
                      <VscTrash />
                    </button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default ServerManager;
