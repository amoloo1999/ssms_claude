/**
 * The single registry of keyboard bindings.
 *
 * Before this, the only binding in the app (Ctrl+Enter) was declared inline in
 * `QueryEditor.handleEditorMount`. With a palette and a shortcuts sheet both
 * needing to know what the bindings *are*, they live here instead: the sheet
 * renders this list, the palette shows a hint per action, and the global key
 * handler matches against it.
 *
 * Only bindings that are actually wired appear here. A shortcuts sheet that
 * lists keys which do nothing is worse than no sheet.
 */

export type ShortcutGroup = 'Run' | 'Navigate' | 'Results' | 'AI';

export interface Shortcut {
  id: string;
  label: string;
  group: ShortcutGroup;
  /** Cmd on macOS, Ctrl elsewhere. */
  mod?: boolean;
  shift?: boolean;
  alt?: boolean;
  /** `KeyboardEvent.key`, compared case-insensitively. */
  key: string;
  /** Shown in the palette and the sheet when the plain key needs a name. */
  keyLabel?: string;
}

export const SHORTCUTS: Shortcut[] = [
  // — Run —
  { id: 'execute', label: 'Execute query', group: 'Run', mod: true, key: 'Enter' },
  {
    id: 'execute-selection',
    label: 'Execute selection',
    group: 'Run',
    mod: true,
    shift: true,
    key: 'Enter',
  },
  { id: 'cancel', label: 'Cancel running query', group: 'Run', mod: true, key: '.' },

  // — Navigate —
  { id: 'palette', label: 'Command palette', group: 'Navigate', mod: true, key: 'k' },
  { id: 'shortcuts', label: 'Keyboard shortcuts', group: 'Navigate', mod: true, key: '/' },
  { id: 'settings', label: 'Settings', group: 'Navigate', mod: true, key: ',' },
  { id: 'new-tab', label: 'New query tab', group: 'Navigate', mod: true, alt: true, key: 't' },
  { id: 'close-tab', label: 'Close query tab', group: 'Navigate', mod: true, alt: true, key: 'w' },
  {
    id: 'next-tab',
    label: 'Next query tab',
    group: 'Navigate',
    mod: true,
    alt: true,
    key: 'ArrowRight',
    keyLabel: '→',
  },
  {
    id: 'prev-tab',
    label: 'Previous query tab',
    group: 'Navigate',
    mod: true,
    alt: true,
    key: 'ArrowLeft',
    keyLabel: '←',
  },

  // — Results —
  { id: 'grid-tab', label: 'Show grid', group: 'Results', mod: true, shift: true, key: 'g' },
  { id: 'chart-tab', label: 'Show chart', group: 'Results', mod: true, shift: true, key: 'v' },
  { id: 'plan-tab', label: 'Show execution plan', group: 'Results', mod: true, key: 'e' },
  { id: 'diff-tab', label: 'Compare result sets', group: 'Results', mod: true, shift: true, key: 'd' },
  { id: 'inspect-cell', label: 'Inspect selected cell', group: 'Results', key: ' ', keyLabel: 'Space' },
  { id: 'export', label: 'Export results…', group: 'Results', mod: true, shift: true, key: 'e' },

  // — AI —
  { id: 'ai-panel', label: 'Toggle AI assistant', group: 'AI', mod: true, shift: true, key: 'a' },
];

const isMac = (): boolean =>
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

/** Render a binding the way this platform writes it. */
export function shortcutLabel(s: Shortcut): string {
  const mac = isMac();
  const parts: string[] = [];
  if (s.mod) parts.push(mac ? '⌘' : 'Ctrl');
  if (s.alt) parts.push(mac ? '⌥' : 'Alt');
  if (s.shift) parts.push(mac ? '⇧' : 'Shift');

  let key = s.keyLabel ?? s.key;
  if (key === 'Enter') key = mac ? '↵' : 'Enter';
  else if (key.length === 1) key = key.toUpperCase();
  parts.push(key);

  // macOS writes modifiers as adjacent glyphs; Windows and Linux join with '+'.
  return mac ? parts.join('') : parts.join('+');
}

export function labelFor(id: string): string {
  const s = SHORTCUTS.find((x) => x.id === id);
  return s ? shortcutLabel(s) : '';
}

/** True when the event is this binding. */
export function matches(e: KeyboardEvent, s: Shortcut): boolean {
  const mod = isMac() ? e.metaKey : e.ctrlKey;
  if (!!s.mod !== mod) return false;
  if (!!s.shift !== e.shiftKey) return false;
  if (!!s.alt !== e.altKey) return false;
  // On macOS a bare Ctrl press shouldn't satisfy a `mod` binding and vice
  // versa, so the unused modifier must be clear too.
  if (isMac() && !s.mod && e.metaKey) return false;
  return e.key.toLowerCase() === s.key.toLowerCase();
}

/** Find which registered binding an event corresponds to, if any. */
export function resolve(e: KeyboardEvent): string | null {
  for (const s of SHORTCUTS) {
    if (matches(e, s)) return s.id;
  }
  return null;
}

/**
 * Whether a keystroke should be ignored because the user is typing.
 *
 * Bare-key bindings (Space opens the cell inspector) must not fire while
 * someone is writing SQL or filling a field. Modified bindings still work
 * everywhere — Ctrl+Enter from inside the editor is the whole point.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    el.isContentEditable ||
    // Monaco renders its own textarea inside a .monaco-editor host.
    !!el.closest?.('.monaco-editor')
  );
}
