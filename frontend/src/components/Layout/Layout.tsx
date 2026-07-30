import { useEffect } from 'react';
import Split from 'react-split';
import { AppContext } from '../../App';
import { getServers, logout } from '../../services/api';
import ObjectExplorer from '../ObjectExplorer/ObjectExplorer';
import QueryEditor from '../QueryEditor/QueryEditor';
import TableBrowser from '../TableBrowser/TableBrowser';
import ServerManager from '../ServerManager/ServerManager';
import AdminPage from '../../pages/AdminPage';
import MyAccessPage from '../../pages/MyAccessPage';
import { connectionColor, connectionEnv } from '../../utils/connectionColor';
import './Layout.css';

interface Props {
  ctx: AppContext;
}

function Layout({ ctx }: Props) {
  useEffect(() => {
    getServers().then(ctx.setServers);
  }, []);

  const handleLogout = async () => {
    await logout();
    window.location.href = '/login';
  };

  const isRevMan = ctx.user?.role === 'revman';
  const isApprover = !!ctx.user?.is_approver;

  // The active connection drives the colour system: its colour paints the
  // connection bar's left edge, the status bar's top rule and the tree dot.
  const activeServer =
    ctx.servers.find((s) => s.id === ctx.activeQuery?.serverId) || null;
  const connColor = connectionColor(activeServer);

  // Write policy is real state, not decoration — non-RevMan executions are
  // rejected by the backend's denylist (services/permissions.py).
  const writePolicy = isRevMan ? 'WRITES ALLOWED' : 'VIEW ONLY — WRITES BLOCKED';

  return (
    <div className="layout" style={{ ['--conn-active' as string]: connColor }}>
      {/* ── Top bar ─────────────────────────────────────────────────────── */}
      <div className="topbar">
        <div className="topbar-left">
          <span className="topbar-title">SQL Studio</span>
          <nav className="topbar-tabs">
            <button
              className={`tab-btn ${ctx.activeTab === 'query' ? 'active' : ''}`}
              onClick={() => ctx.setActiveTab('query')}
            >
              Query Editor
            </button>
            <button
              className={`tab-btn ${ctx.activeTab === 'table' ? 'active' : ''}`}
              onClick={() => ctx.setActiveTab('table')}
              disabled={!ctx.activeTable}
            >
              Table Browser
            </button>
            {isRevMan && (
              <button
                className={`tab-btn ${ctx.activeTab === 'schema' ? 'active' : ''}`}
                onClick={() => ctx.setActiveTab('schema')}
              >
                Servers
              </button>
            )}
            {!isRevMan && (
              <button
                className={`tab-btn ${ctx.activeTab === 'my-access' ? 'active' : ''}`}
                onClick={() => ctx.setActiveTab('my-access')}
              >
                My Access
              </button>
            )}
            {isApprover && (
              <button
                className={`tab-btn ${ctx.activeTab === 'admin' ? 'active' : ''}`}
                onClick={() => ctx.setActiveTab('admin')}
              >
                Admin
              </button>
            )}
          </nav>
        </div>
        <div className="topbar-right">
          {ctx.user && (
            <>
              <img src={ctx.user.picture} alt="" className="avatar" />
              <span className="user-name">{ctx.user.name}</span>
              <span className="tag tag-outline">{isRevMan ? 'RevMan' : 'View'}</span>
              <button className="btn btn-secondary" onClick={handleLogout}>
                Log out
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── Connection bar ──────────────────────────────────────────────────
          The load-bearing safety feature: its inset left edge is the active
          connection's colour, and it states the write policy in the open. */}
      <div className="connbar">
        {activeServer ? (
          <>
            <span className="conn-pill">
              <span className="conn-dot" />
              {activeServer.name.toUpperCase()}
            </span>
            <span className="conn-policy">
              {connectionEnv(activeServer)} — {writePolicy}
            </span>
            <span className="conn-divider" />
            <span className="conn-db mono">
              {ctx.activeQuery?.database || 'no database'}
            </span>
            <span className="conn-dialect">{activeServer.dialect}</span>
          </>
        ) : (
          <span className="conn-policy conn-policy-idle">NO ACTIVE CONNECTION</span>
        )}
        <span className="conn-session">
          {ctx.servers.length} server{ctx.servers.length === 1 ? '' : 's'} configured
        </span>
      </div>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      <div className="main-content">
        <Split
          className="split-horizontal"
          sizes={[22, 78]}
          minSize={200}
          gutterSize={4}
          direction="horizontal"
        >
          <div className="panel-left">
            <ObjectExplorer ctx={ctx} />
          </div>

          <div className="panel-right">
            {ctx.activeTab === 'query' && <QueryEditor ctx={ctx} />}
            {ctx.activeTab === 'table' && ctx.activeTable && <TableBrowser ctx={ctx} />}
            {ctx.activeTab === 'schema' && isRevMan && <ServerManager ctx={ctx} />}
            {ctx.activeTab === 'admin' && isApprover && <AdminPage ctx={ctx} />}
            {ctx.activeTab === 'my-access' && !isRevMan && <MyAccessPage ctx={ctx} />}
          </div>
        </Split>
      </div>

      {/* ── Status bar ──────────────────────────────────────────────────── */}
      <div className="statusbar">
        <span>
          {activeServer
            ? `${activeServer.name} / ${ctx.activeQuery?.database || '—'} — connected as ${
                ctx.user?.email || ''
              }`
            : 'No active connection'}
        </span>
        <span className="statusbar-right">
          {ctx.servers.length} server{ctx.servers.length === 1 ? '' : 's'} reachable
        </span>
      </div>
    </div>
  );
}

export default Layout;
