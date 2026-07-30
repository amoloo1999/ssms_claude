/**
 * User settings, persisted to localStorage.
 *
 * The handoff's settings panel says "Settings sync to your account, not the
 * machine", which needs a `user_settings` table server-side. That's phase B —
 * until then these are per-browser, and the panel says so rather than claiming
 * a sync that isn't happening.
 *
 * Only settings that actually drive behaviour live here. The mock's safety
 * rails ("block writes on red connections", "require a comment on production
 * writes") are deliberately absent: they describe server-side enforcement that
 * doesn't exist yet, and a toggle that silently does nothing is worse than no
 * toggle.
 */

export type Density = 'comfortable' | 'compact' | 'dense';

export interface Settings {
  // Appearance
  density: Density;
  editorFontSize: number;
  // Editor
  autocomplete: boolean;
  uppercaseKeywords: boolean;
  wordWrap: boolean;
  restoreTabs: boolean;
  // Results
  nullDisplay: 'dash' | 'blank' | 'null';
  hideIdenticalInDiff: boolean;
  // Safety — client-side confirmations, genuinely enforced by the UI.
  confirmWriteWithoutWhere: boolean;
  warnAboveRows: number;
}

export const DEFAULTS: Settings = {
  density: 'compact',
  editorFontSize: 13,
  autocomplete: true,
  uppercaseKeywords: false,
  wordWrap: true,
  restoreTabs: true,
  nullDisplay: 'dash',
  hideIdenticalInDiff: true,
  confirmWriteWithoutWhere: true,
  warnAboveRows: 100000,
};

const KEY = 'sqlstudio.settings.v1';

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    // Merge over defaults so a settings blob written by an older build doesn't
    // leave newly-added keys undefined.
    return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* private mode or quota — settings just don't persist */
  }
}

/** Row height in px for each density step, applied to the results grid. */
export const DENSITY_ROW_HEIGHT: Record<Density, number> = {
  comfortable: 32,
  compact: 26,
  dense: 22,
};

/**
 * Does this statement write without a WHERE clause?
 *
 * Used for the confirmation prompt. Deliberately conservative — it strips
 * strings and comments first so a literal containing the word "where" can't
 * suppress the warning.
 */
export function isUnscopedWrite(sql: string): boolean {
  const cleaned = sql
    .replace(/'(?:''|[^'])*'/g, "''")
    .replace(/--[^\n]*/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  if (!/\b(update|delete)\b/i.test(cleaned)) return false;
  return !/\bwhere\b/i.test(cleaned);
}
