import { useState } from 'react';
import { AppContext } from '../../App';
import ObjectExplorer from '../ObjectExplorer/ObjectExplorer';
import HistoryPanel from './HistoryPanel';
import SnippetsPanel from './SnippetsPanel';
import './ExplorerRail.css';

interface Props {
  ctx: AppContext;
}

type RailTab = 'explorer' | 'history' | 'snippets';

const TABS: { id: RailTab; label: string }[] = [
  { id: 'explorer', label: 'Explorer' },
  { id: 'history', label: 'History' },
  { id: 'snippets', label: 'Snippets' },
];

/**
 * The left rail — handoff screen 2A.
 *
 * A wrapper rather than a change to ObjectExplorer: the tree is 580 lines of
 * context menus, lazy loading and rename handling, and none of it needs to know
 * that it now shares a rail with two sibling panels.
 */
function ExplorerRail({ ctx }: Props) {
  const [tab, setTab] = useState<RailTab>('explorer');

  return (
    <div className="explorer-rail">
      <div className="rail-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`rail-tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* The tree stays mounted across tab switches — remounting it would drop
          every expanded node and re-fetch the schema. */}
      <div className={`rail-body ${tab === 'explorer' ? '' : 'hidden'}`}>
        <ObjectExplorer ctx={ctx} />
      </div>
      {tab === 'history' && (
        <div className="rail-body">
          <HistoryPanel ctx={ctx} />
        </div>
      )}
      {tab === 'snippets' && (
        <div className="rail-body">
          <SnippetsPanel ctx={ctx} />
        </div>
      )}
    </div>
  );
}

export default ExplorerRail;
