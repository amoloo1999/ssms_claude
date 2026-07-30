import { useEffect, useState } from 'react';
import Split from 'react-split';
import { AppContext } from '../../App';
import { getServers, logout } from '../../services/api';
import CommandPalette from '../CommandPalette/CommandPalette';
import ShortcutsSheet from '../ShortcutsSheet/ShortcutsSheet';
import SettingsDialog from '../Settings/SettingsDialog';
import { resolve, isTypingTarget, labelFor } from '../../utils/shortcuts';
import { emit } from '../../utils/actionBus';
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
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    getServers().then(ctx.setServers);
  }, []);

  // The one global key handler. It owns the shell-level bindings and forwards
  // everything else onto the action bus, where whichever component owns that
  // action has registered for it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const id = resolve(e);
      if (!id) return;

      // A bare-key binding (Space inspects a cell) must not fire while the
      // user is typing SQL or filling a field. Modified bindings still work
      // everywhere — Ctrl+Enter from inside the editor is the whole point.
      const bareKey = !e.ctrlKey && !e.metaKey && !e.altKey;
      if (bareKey && isTypingTarget(e.target)) return;

      // While a dialog is up, only let its own toggle through — otherwise
      // Ctrl+K inside the palette's own input would re-enter here.
      const dialogUp = paletteOpen || shortcutsOpen || settingsOpen;

      if (id === 'palette') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (id === 'shortcuts') {
        e.preventDefault();
        setShortcutsOpen((v) => !v);
        return;
      }
      if (id === 'settings') {
        e.preventDefault();
        setSettingsOpen((v) => !v);
        return;
      }
      if (dialogUp) return;

      // Only swallow the keystroke when something is actually listening, so an
      // unhandled binding still reaches the browser.
      if (emit(id)) e.preventDefault();
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [paletteOpen, shortcutsOpen, settingsOpen]);

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

  // Write policy is real state, not decoration, and it has two independent
  // sources: the connection's own policy (a read-only server refuses writes
  // from everyone, RevMan included) and the caller's role. The connection wins,
  // because that is the order the backend checks them in.
  // A named exemption lifts the connection gate for specific people, so the bar
  // has to tell THIS user what applies to them rather than showing everyone the
  // same blanket message.
  const exempt = !!ctx.user?.can_write_anywhere;
  const serverReadOnly = activeServer?.write_policy === 'read_only';
  const writePolicy =
    serverReadOnly && !exempt
      ? 'READ-ONLY CONNECTION — WRITES BLOCKED FOR ALL USERS'
      : serverReadOnly && exempt
      ? 'READ-ONLY CONNECTION — WRITES ALLOWED FOR YOU'
      : isRevMan
      ? 'WRITES ALLOWED'
      : 'VIEW ONLY — WRITES BLOCKED';

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
          {/* The handoff's search affordance. It opens the palette, which is
              the only thing it claims to do. */}
          <button className="topbar-search" onClick={() => setPaletteOpen(true)}>
            <span>Search objects, connections, actions</span>
            <span className="kbd">{labelFor('palette')}</span>
          </button>
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
          {ctx.servers.length} server{ctx.servers.length === 1 ? '' : 's'} reachable ·{' '}
          <button className="statusbar-link" onClick={() => setShortcutsOpen(true)}>
            {labelFor('shortcuts')} shortcuts
          </button>
        </span>
      </div>

      {paletteOpen && <CommandPalette ctx={ctx} onClose={() => setPaletteOpen(false)} />}
      {shortcutsOpen && <ShortcutsSheet onClose={() => setShortcutsOpen(false)} />}
      {settingsOpen && (
        <SettingsDialog
          settings={ctx.settings}
          onChange={ctx.setSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}

export default Layout;
