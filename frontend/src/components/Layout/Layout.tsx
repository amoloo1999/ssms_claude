import { useEffect } from 'react';
import Split from 'react-split';
import { AppContext } from '../../App';
import { getServers, logout } from '../../services/api';
import ObjectExplorer from '../ObjectExplorer/ObjectExplorer';
import QueryEditor from '../QueryEditor/QueryEditor';
import TableBrowser from '../TableBrowser/TableBrowser';
import ServerManager from '../ServerManager/ServerManager';
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

  return (
    <div className="layout">
      {/* Top Bar */}
      <div className="topbar">
        <div className="topbar-left">
          <span className="topbar-title">SQL Studio</span>
          <div className="topbar-tabs">
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
            <button
              className={`tab-btn ${ctx.activeTab === 'schema' ? 'active' : ''}`}
              onClick={() => ctx.setActiveTab('schema')}
            >
              Server Manager
            </button>
          </div>
        </div>
        <div className="topbar-right">
          {ctx.user && (
            <>
              <img src={ctx.user.picture} alt="" className="avatar" />
              <span className="user-name">{ctx.user.name}</span>
              <button className="logout-btn" onClick={handleLogout}>
                Logout
              </button>
            </>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="main-content">
        <Split
          className="split-horizontal"
          sizes={[22, 78]}
          minSize={200}
          gutterSize={4}
          direction="horizontal"
        >
          {/* Left Panel - Object Explorer */}
          <div className="panel-left">
            <ObjectExplorer ctx={ctx} />
          </div>

          {/* Right Panel - Content Area */}
          <div className="panel-right">
            {ctx.activeTab === 'query' && <QueryEditor ctx={ctx} />}
            {ctx.activeTab === 'table' && ctx.activeTable && (
              <TableBrowser ctx={ctx} />
            )}
            {ctx.activeTab === 'schema' && <ServerManager ctx={ctx} />}
          </div>
        </Split>
      </div>

      {/* Status Bar */}
      <div className="statusbar">
        <span>
          {ctx.activeQuery
            ? `Connected: Server ${ctx.activeQuery.serverId} / ${ctx.activeQuery.database}`
            : 'No active connection'}
        </span>
        <span>{ctx.servers.length} server(s) configured</span>
      </div>
    </div>
  );
}

export default Layout;
