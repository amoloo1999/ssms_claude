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
    'DBCC|KILL|SHUTDOWN|RECONFIGURE|WAITFOR|DISABLE\\s+TRIGGER|ENABLE\\s+TRIGGER|' +
    'OPENROWSET|OPENQUERY|OPENDATASOURCE|' +
    'COPY|LOAD\\s+DATA|LOAD\\s+XML|REPLACE\\s+INTO|' +
    'SELECT\\b[\\s\\S]*?\\bINTO\\b' +
    ')\\b',
  'i'
);

/** A read batch must open with one of these (optional leading `(`). Mirrors
 *  _READ_LEADING_RE in backend services/permissions.py. */
const READ_LEADING = /^\s*\(*\s*(SELECT|WITH)\b/i;

/** Client-side batch separator, mirroring the mssql driver's GO split. */
const GO_SPLIT = /^[ \t]*GO(?:[ \t]+\d+)?[ \t]*$/im;

/** Strip string literals and comments so a verb inside them can't trip the check. */
function clean(sql: string): string {
  return (sql || '')
    .replace(/'(?:''|[^'])*'/g, "''")
    .replace(/--[^\n]*/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');
}

export function isReadOnlySql(sql: string): boolean {
  // Every executed batch must START with a read keyword and contain no write
  // verb. The leading check is what blocks a bare stored-procedure call, which
  // is only legal as the first statement of a batch.
  for (const batch of (sql || '').split(GO_SPLIT)) {
    const c = clean(batch);
    if (!c.trim()) continue;
    if (!READ_LEADING.test(c)) return false;
    if (FORBIDDEN.test(c)) return false;
  }
  return true;
}

/** A short reason a statement isn't a read, for the message. */
export function writeVerb(sql: string): string | null {
  for (const batch of (sql || '').split(GO_SPLIT)) {
    const c = clean(batch);
    if (!c.trim()) continue;
    if (!READ_LEADING.test(c)) {
      const m = /\s*\(*\s*([A-Za-z_@#][\w@#$]*)/.exec(c);
      return m ? m[1].toUpperCase() : 'non-SELECT';
    }
    const f = FORBIDDEN.exec(c);
    if (f) return f[1].toUpperCase().split(/\s+/)[0];
  }
  return null;
}
