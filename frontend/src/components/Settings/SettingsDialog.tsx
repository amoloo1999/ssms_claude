import { useEffect, useState } from 'react';
import { DEFAULTS, Density, Settings, saveSettings } from '../../utils/settings';
import { SHORTCUTS, ShortcutGroup, shortcutLabel } from '../../utils/shortcuts';
import './SettingsDialog.css';

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
  onClose: () => void;
}

type Section = 'Appearance' | 'Editor' | 'Results' | 'Safety' | 'Shortcuts';
const SECTIONS: Section[] = ['Appearance', 'Editor', 'Results', 'Safety', 'Shortcuts'];

function Toggle({
  label,
  hint,
  checked,
  onToggle,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onToggle: (v: boolean) => void;
}) {
  return (
    <label className="set-toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onToggle(e.target.checked)} />
      <span className="set-box" aria-hidden="true" />
      <span className="set-toggle-text">
        <span className="set-toggle-label">{label}</span>
        {hint && <span className="set-toggle-hint">{hint}</span>}
      </span>
    </label>
  );
}

function Segmented<T extends string>({
  value,
  options,
  onSelect,
}: {
  value: T;
  options: { value: T; label: string }[];
  onSelect: (v: T) => void;
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button
          key={o.value}
          className={`seg-opt ${o.value === value ? 'active' : ''}`}
          onClick={() => onSelect(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Settings — handoff screen 4C.
 *
 * Changes save as they're made, per the mock. Every control here drives real
 * behaviour; the mock's server-enforced safety rails are not shown, because
 * that enforcement doesn't exist yet and a dead toggle in a safety panel is
 * actively misleading.
 */
function SettingsDialog({ settings, onChange, onClose }: Props) {
  const [section, setSection] = useState<Section>('Appearance');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    const next = { ...settings, [key]: value };
    saveSettings(next);
    onChange(next);
  };

  const reset = () => {
    saveSettings({ ...DEFAULTS });
    onChange({ ...DEFAULTS });
  };

  return (
    <div className="set-backdrop" onMouseDown={onClose}>
      <div
        className="set-panel"
        role="dialog"
        aria-label="Settings"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <nav className="set-nav">
          <div className="set-nav-title">Settings</div>
          {SECTIONS.map((s) => (
            <button
              key={s}
              className={`set-nav-item ${s === section ? 'active' : ''}`}
              onClick={() => setSection(s)}
            >
              {s}
            </button>
          ))}
          <div className="set-nav-note">
            Settings are stored in this browser. Syncing them to your account
            needs a server-side store — not built yet.
          </div>
        </nav>

        <div className="set-body">
          <div className="set-body-head">
            <h3>{section}</h3>
            <button className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
          </div>

          {section === 'Appearance' && (
            <>
              <div className="set-field">
                <label>Grid density</label>
                <Segmented<Density>
                  value={settings.density}
                  options={[
                    { value: 'comfortable', label: 'Comfortable' },
                    { value: 'compact', label: 'Compact' },
                    { value: 'dense', label: 'Dense' },
                  ]}
                  onSelect={(v) => set('density', v)}
                />
              </div>
              <div className="set-field">
                <label>Editor font size</label>
                <input
                  className="input set-narrow"
                  type="number"
                  min={10}
                  max={22}
                  value={settings.editorFontSize}
                  onChange={(e) => set('editorFontSize', Number(e.target.value) || 13)}
                />
              </div>
              <p className="set-note">
                The interface is dark only. A light theme would need a second set
                of colour tokens throughout.
              </p>
            </>
          )}

          {section === 'Editor' && (
            <>
              <Toggle
                label="Autocomplete as you type"
                hint="Suggest databases, schemas, tables and columns"
                checked={settings.autocomplete}
                onToggle={(v) => set('autocomplete', v)}
              />
              <Toggle
                label="Wrap long lines"
                checked={settings.wordWrap}
                onToggle={(v) => set('wordWrap', v)}
              />
              <Toggle
                label="Uppercase keywords on insert"
                hint="Applies to snippets and AI-inserted SQL"
                checked={settings.uppercaseKeywords}
                onToggle={(v) => set('uppercaseKeywords', v)}
              />
              <Toggle
                label="Restore open tabs on reload"
                checked={settings.restoreTabs}
                onToggle={(v) => set('restoreTabs', v)}
              />
            </>
          )}

          {section === 'Results' && (
            <>
              <div className="set-field">
                <label>Show NULL as</label>
                <Segmented
                  value={settings.nullDisplay}
                  options={[
                    { value: 'dash' as const, label: '—' },
                    { value: 'blank' as const, label: 'Blank' },
                    { value: 'null' as const, label: 'NULL' },
                  ]}
                  onSelect={(v) => set('nullDisplay', v)}
                />
              </div>
              <Toggle
                label="Hide identical rows when comparing"
                checked={settings.hideIdenticalInDiff}
                onToggle={(v) => set('hideIdenticalInDiff', v)}
              />
            </>
          )}

          {section === 'Safety' && (
            <>
              <Toggle
                label="Confirm UPDATE or DELETE without a WHERE clause"
                hint="Prompts before the statement is sent"
                checked={settings.confirmWriteWithoutWhere}
                onToggle={(v) => set('confirmWriteWithoutWhere', v)}
              />
              <div className="set-field">
                <label>Warn when a result exceeds</label>
                <input
                  className="input set-narrow"
                  type="number"
                  min={1000}
                  step={1000}
                  value={settings.warnAboveRows}
                  onChange={(e) => set('warnAboveRows', Number(e.target.value) || 100000)}
                />
                <span className="set-suffix">rows</span>
              </div>
              <p className="set-note">
                These are conveniences in this browser, not access control. What
                you may read and write is enforced by the server from your role
                and grants, and cannot be changed here.
              </p>
            </>
          )}

          {section === 'Shortcuts' && (
            <>
              <p className="set-note">
                Bindings are fixed for now. Remapping needs somewhere per-user to
                store the overrides.
              </p>
              {(['Run', 'Navigate', 'Results', 'AI'] as ShortcutGroup[]).map((g) => {
                const rows = SHORTCUTS.filter((s) => s.group === g);
                if (!rows.length) return null;
                return (
                  <section key={g} className="set-shortcut-group">
                    <div className="set-kicker">{g}</div>
                    {rows.map((s) => (
                      <div key={s.id} className="set-shortcut-row">
                        <span>{s.label}</span>
                        <span className="mono">{shortcutLabel(s)}</span>
                      </div>
                    ))}
                  </section>
                );
              })}
            </>
          )}

          <div className="set-footer">
            <span>Changes save as you make them</span>
            <button className="btn btn-secondary" onClick={reset}>
              Reset to defaults
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default SettingsDialog;
