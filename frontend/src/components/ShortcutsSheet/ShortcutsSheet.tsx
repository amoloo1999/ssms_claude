import { useEffect } from 'react';
import { SHORTCUTS, ShortcutGroup, shortcutLabel } from '../../utils/shortcuts';
import './ShortcutsSheet.css';

interface Props {
  onClose: () => void;
}

const GROUPS: ShortcutGroup[] = ['Run', 'Navigate', 'Results', 'AI'];

const isMac =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

/**
 * The keyboard cheat sheet — handoff screen 2I.
 *
 * Rendered from the same registry the key handler matches against, so the sheet
 * can't drift from what the app actually does.
 */
function ShortcutsSheet({ onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="sheet-backdrop" onMouseDown={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-label="Keyboard shortcuts"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="sheet-header">
          <h3>Keyboard shortcuts</h3>
          <span className="sheet-hint">{isMac ? '⌘/' : 'Ctrl+/'} to close</span>
        </div>

        <div className="sheet-columns">
          {GROUPS.map((group) => {
            const rows = SHORTCUTS.filter((s) => s.group === group);
            if (!rows.length) return null;
            return (
              <section key={group} className="sheet-group">
                <div className="sheet-kicker">{group}</div>
                {rows.map((s) => (
                  <div key={s.id} className="sheet-row">
                    <span className="sheet-label">{s.label}</span>
                    <span className="sheet-keys">{shortcutLabel(s)}</span>
                  </div>
                ))}
              </section>
            );
          })}
        </div>

        <div className="sheet-footer">
          {isMac
            ? 'Windows and Linux swap ⌘ for Ctrl.'
            : 'macOS swaps Ctrl for ⌘. Bindings are not yet remappable.'}
        </div>
      </div>
    </div>
  );
}

export default ShortcutsSheet;
