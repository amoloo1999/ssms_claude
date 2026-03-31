import { useState } from 'react';
import { AppContext } from '../../App';
import { createServer, updateServer, deleteServer, testConnection, getServers } from '../../services/api';
import { Server } from '../../types';
import { VscAdd, VscEdit, VscTrash, VscDebugStart, VscCheck, VscClose } from 'react-icons/vsc';
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
}

const emptyForm: FormData = {
  name: '',
  host: '',
  port: 1433,
  username: '',
  password: '',
  description: '',
};

function ServerManager({ ctx }: Props) {
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [form, setForm] = useState<FormData>(emptyForm);
  const [testResult, setTestResult] = useState<Record<number, { success: boolean; message: string }>>({});
  const [saving, setSaving] = useState(false);

  const refresh = async () => {
    const servers = await getServers();
    ctx.setServers(servers);
  };

  const handleAdd = () => {
    setForm(emptyForm);
    setEditing(null);
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
    });
    setEditing(server.id);
    setShowForm(true);
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
              Host
              <input
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                placeholder="server.example.com"
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
            <label>
              Password
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder={editing ? '(leave blank to keep current)' : 'Password'}
              />
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
            <button className="btn-primary" onClick={handleSave} disabled={saving}>
              <VscCheck /> {saving ? 'Saving...' : 'Save'}
            </button>
            <button className="btn-secondary" onClick={() => setShowForm(false)}>
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
            <div key={server.id} className="sm-card">
              <div className="sm-card-info">
                <div className="sm-card-name">
                  {server.name}
                  {server.from_config && <span className="sm-badge">config</span>}
                </div>
                <div className="sm-card-details">
                  {server.host}:{server.port} &middot; {server.username}
                  {server.description && ` &middot; ${server.description}`}
                </div>
                {testResult[server.id] && (
                  <div className={`sm-test-result ${testResult[server.id].success ? 'success' : 'error'}`}>
                    {testResult[server.id].message}
                  </div>
                )}
              </div>
              <div className="sm-card-actions">
                <button className="sm-icon-btn" onClick={() => handleTest(server.id)} title="Test Connection">
                  <VscDebugStart />
                </button>
                <button className="sm-icon-btn" onClick={() => handleEdit(server)} title="Edit">
                  <VscEdit />
                </button>
                <button className="sm-icon-btn danger" onClick={() => handleDelete(server.id)} title="Delete">
                  <VscTrash />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default ServerManager;
