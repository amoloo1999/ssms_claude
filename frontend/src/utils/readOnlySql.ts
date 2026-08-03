/**
 * Client-side "is this a read?" check for the mobile companion.
 *
 * The handoff's mobile screen is read-only: it may RUN a saved query and show
 * its rows, but it must never write. A RevMan's saved snippet could contain an
 * UPDATE, so the phone refuses to run anything that isn't a plain read.
 *
 * This mirrors the verb list in backend services/permissions._FORBIDDEN_RE. It
 * is a convenience guard, not the enforcement — the server still decides what
 * any given user may execute. Its job is to stop a phone tap from firing a
 * write that the server would happily allow for a RevMan.
 */

const FORBIDDEN = new RegExp(
  '\\b(' +
    'INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|' +
    'EXEC|EXECUTE|CALL|GRANT|REVOKE|DENY|BACKUP|RESTORE|' +
    'BULK\\s+INSERT|' +
    'COPY|LOAD\\s+DATA|LOAD\\s+XML|REPLACE\\s+INTO|' +
    'SELECT\\b[\\s\\S]*?\\bINTO\\b' +
    ')\\b',
  'i'
);

/** Strip string literals and comments so a verb inside them can't trip the check. */
function clean(sql: string): string {
  return (sql || '')
    .replace(/'(?:''|[^'])*'/g, "''")
    .replace(/--[^\n]*/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');
}

export function isReadOnlySql(sql: string): boolean {
  return !FORBIDDEN.test(clean(sql));
}

/** The offending verb, for a message that says what was actually wrong. */
export function writeVerb(sql: string): string | null {
  const m = FORBIDDEN.exec(clean(sql));
  return m ? m[1].toUpperCase().split(/\s+/)[0] : null;
}
