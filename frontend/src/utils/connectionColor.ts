import { Server } from '../types';

/**
 * The connection colour system.
 *
 * Per the Nocturne handoff, a connection's colour is a system rather than a
 * decoration: one colour paints its status-bar edge, its tree dot, its tab
 * accent and every dialog that runs against it, so you can tell at a glance
 * which server a statement is about to hit.
 *
 * The colour is derived from the server's `kind`, which is the only
 * environment signal the API carries today:
 *   - `main` — the production MSSQL box → red
 *   - `gp`   — the Great Plains warehouse → sage
 *   - anything else (user-added servers) → accent
 */
export function connectionColor(server: Server | null | undefined): string {
  if (!server) return 'var(--color-neutral-700)';
  switch (server.kind) {
    case 'main':
      return 'var(--conn-prod)';
    case 'gp':
      return 'var(--conn-warehouse)';
    default:
      return 'var(--conn-sandbox)';
  }
}

/** The all-caps environment word shown beside the connection pill. */
export function connectionEnv(server: Server | null | undefined): string {
  if (!server) return 'UNKNOWN';
  switch (server.kind) {
    case 'main':
      return 'PRODUCTION';
    case 'gp':
      return 'WAREHOUSE';
    default:
      return 'SECONDARY';
  }
}
