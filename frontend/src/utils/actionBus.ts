/**
 * A tiny action bus.
 *
 * The command palette and the global key handler live in the shell (Layout),
 * but most of what they trigger — execute, switch result tab, open the export
 * dialog — is state inside QueryEditor. Rather than lifting all of that state
 * up or threading callbacks through the tree, QueryEditor registers handlers
 * for the action ids it owns and the shell emits them.
 *
 * Handler ids are the same strings as the shortcut ids in `utils/shortcuts.ts`,
 * so one registration covers both the keystroke and the palette entry.
 */

type Handler = (payload?: unknown) => void;

const handlers = new Map<string, Set<Handler>>();

/** Register a handler. Returns an unsubscribe function for effect cleanup. */
export function on(id: string, fn: Handler): () => void {
  let set = handlers.get(id);
  if (!set) {
    set = new Set();
    handlers.set(id, set);
  }
  set.add(fn);
  return () => {
    set!.delete(fn);
    if (set!.size === 0) handlers.delete(id);
  };
}

/** Fire an action. Returns false when nothing was listening. */
export function emit(id: string, payload?: unknown): boolean {
  const set = handlers.get(id);
  if (!set || set.size === 0) return false;
  for (const fn of [...set]) fn(payload);
  return true;
}

/** Whether anything is currently listening — used to grey out palette rows. */
export function has(id: string): boolean {
  const set = handlers.get(id);
  return !!set && set.size > 0;
}

/**
 * Register several handlers at once and return a single cleanup.
 * Convenience for a component that owns a batch of actions.
 */
export function onAll(map: Record<string, Handler>): () => void {
  const offs = Object.entries(map).map(([id, fn]) => on(id, fn));
  return () => offs.forEach((off) => off());
}
