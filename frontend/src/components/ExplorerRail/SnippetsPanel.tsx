import { useEffect, useState } from 'react';
import { AppContext } from '../../App';
import {
  getSnippets,
  deleteSnippet,
  markSnippetUsed,
  updateSnippet,
} from '../../services/api';
import { SnippetItem } from '../../types';

interface Props {
  ctx: AppContext;
}

const oneLine = (sql: string) => sql.replace(/\s+/g, ' ').trim();

/**
 * Snippet library — handoff screen 3A, right pane.
 *
 * Your own snippets plus anything explicitly shared. Only the owner can edit or
 * delete: a shared snippet is something other people run, so letting anyone
 * rewrite its SQL would be a quiet way to change what a colleague executes.
 */
function SnippetsPanel({ ctx }: Props) {
  const [snippets, setSnippets] = useState<SnippetItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const load = () => {
    setLoading(true);
    getSnippets()
      .then(setSnippets)
      .catch(() => setSnippets([]))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const mine = ctx.user?.email;

  const open = async (s: SnippetItem) => {
    const target = ctx.activeQuery;
    if (!target) {
      alert('Pick a server and database first.');
      return;
    }
    ctx.setPendingQuery({
      serverId: target.serverId,
      database: target.database,
      sql: s.sql,
    });
    ctx.setActiveTab('query');
    try {
      await markSnippetUsed(s.id);
      load();
    } catch {
      /* the counter is a nicety — never block opening the snippet on it */
    }
  };

  const remove = async (s: SnippetItem) => {
    if (!confirm(`Delete the snippet "${s.name}"?`)) return;
    try {
      await deleteSnippet(s.id);
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const toggleShare = async (s: SnippetItem) => {
    try {
      await updateSnippet(s.id, { is_shared: !s.is_shared });
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const visible = snippets.filter(
    (s) =>
      !search ||
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      s.sql.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="rail-panel">
      <div className="rail-tools">
        <input
          className="input"
          value={search}
          placeholder="Search snippets…"
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="rail-list">
        {loading && <div className="rail-empty">Loading…</div>}
        {!loading && visible.length === 0 && (
          <div className="rail-empty">
            {search
              ? 'No snippets match that search.'
              : 'No snippets yet. Save one from the query toolbar.'}
          </div>
        )}

        {visible.map((s) => {
          const owned = s.owner_email === mine;
          return (
            <div key={s.id} className="snip-row">
              <div className="snip-head">
                <span className="snip-name">{s.name}</span>
                {s.is_shared && (
                  <span className="tag tag-neutral">{owned ? 'shared' : `by ${s.owner_email.split('@')[0]}`}</span>
                )}
              </div>
              <div className="snip-sql mono">{oneLine(s.sql)}</div>
              {s.description && <div className="snip-desc">{s.description}</div>}
              <div className="snip-meta">
                <span>
                  used {s.use_count} time{s.use_count === 1 ? '' : 's'}
                </span>
                <span className="snip-actions">
                  <button className="rail-link" onClick={() => open(s)}>
                    Open
                  </button>
                  {owned && (
                    <>
                      <button className="rail-link" onClick={() => toggleShare(s)}>
                        {s.is_shared ? 'Unshare' : 'Share'}
                      </button>
                      <button className="rail-link danger" onClick={() => remove(s)}>
                        Delete
                      </button>
                    </>
                  )}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="rail-footer">
        <span>Shared snippets are editable only by their owner</span>
      </div>
    </div>
  );
}

export default SnippetsPanel;
