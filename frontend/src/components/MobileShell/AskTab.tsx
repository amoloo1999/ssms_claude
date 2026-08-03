import { useEffect, useRef, useState } from 'react';
import { aiGenerate, aiFindData } from '../../services/api';
import { isReadOnlySql, writeVerb } from '../../utils/readOnlySql';

interface Props {
  serverId: number | null;
  database: string;
  /** Whether this user may run writes from a phone (server enforces it too). */
  canWrite: boolean;
  onRun: (title: string, sql: string) => void;
}

interface Turn {
  id: number;
  prompt: string;
  status: 'thinking' | 'done' | 'error';
  sql?: string | null;
  explanation?: string;
  error?: string;
}

const SUGGESTIONS = [
  'Top 10 sites by revenue last month',
  'Which sites had occupancy below 80% this week?',
  'Where does occupancy data live?',
];

/**
 * The AI chat — the phone's substitute for an editor.
 *
 * There is no SQL text box here on purpose: you describe what you want, the
 * model writes the query, and you read it before running it. That keeps the
 * "no typing SQL on a phone" property while removing the ceiling that made the
 * mobile view feel like a dead end — you can now reach data nobody thought to
 * save as a snippet.
 *
 * The generated SQL is always shown before it runs. A generated write is
 * refused for anyone the server wouldn't let write from this surface anyway,
 * and the refusal names the verb rather than failing vaguely.
 */
function AskTab({ serverId, database, canWrite, onRun }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  const ask = async (text: string, mode: 'sql' | 'find' = 'sql') => {
    const q = text.trim();
    if (!q || busy) return;
    if (!serverId || !database) {
      alert('Pick a connection first — tap the server name at the top.');
      return;
    }
    const id = Date.now();
    setTurns((t) => [...t, { id, prompt: q, status: 'thinking' }]);
    setPrompt('');
    setBusy(true);
    try {
      const res =
        mode === 'find'
          ? await aiFindData(serverId, q)
          : await aiGenerate(serverId, database, q);
      setTurns((t) =>
        t.map((x) =>
          x.id === id
            ? {
                ...x,
                status: res.error ? 'error' : 'done',
                sql: res.sql,
                explanation: res.explanation,
                error: res.error || undefined,
              }
            : x
        )
      );
    } catch (err: any) {
      const msg =
        err.response?.data?.detail?.detail || err.response?.data?.detail || err.message;
      setTurns((t) =>
        t.map((x) => (x.id === id ? { ...x, status: 'error', error: msg } : x))
      );
    } finally {
      setBusy(false);
    }
  };

  const runTurn = (t: Turn) => {
    if (!t.sql) return;
    const verb = writeVerb(t.sql);
    if (verb && !canWrite) {
      alert(
        `That query contains ${verb}, and writes aren't allowed from a phone or ` +
          'tablet for your account.\n\nOpen it on the desktop app instead.'
      );
      return;
    }
    onRun(t.prompt.slice(0, 60), t.sql);
  };

  return (
    <div className="ask">
      <div className="ask-log">
        {turns.length === 0 && (
          <div className="ask-intro">
            <p>
              Describe what you want and the assistant writes the query. You see
              the SQL before anything runs.
            </p>
            <div className="mob-kicker">Try</div>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="ask-suggestion" onClick={() => ask(s)}>
                {s}
              </button>
            ))}
            <p className="ask-note">
              {canWrite
                ? 'You can run writes from here, but the connection still decides — a read-only server refuses them.'
                : 'This view reads only. A generated query that writes will not run here.'}
            </p>
          </div>
        )}

        {turns.map((t) => (
          <div key={t.id} className="ask-turn">
            <div className="ask-prompt">{t.prompt}</div>

            {t.status === 'thinking' && <div className="ask-thinking">Thinking…</div>}

            {t.status === 'error' && <div className="mob-bad">{t.error}</div>}

            {t.status === 'done' && (
              <div className="ask-reply">
                {t.explanation && <p className="ask-explain">{t.explanation}</p>}
                {t.sql && (
                  <>
                    <pre className="mob-sql mono">{t.sql}</pre>
                    <div className="mob-actions">
                      <button
                        className="mob-btn primary"
                        onClick={() => runTurn(t)}
                        disabled={!isReadOnlySql(t.sql) && !canWrite}
                      >
                        Run
                      </button>
                      {!isReadOnlySql(t.sql) && (
                        <span className="mob-note">
                          {canWrite ? 'This writes — check it first' : 'Writes — desktop only'}
                        </span>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="ask-composer">
        <input
          className="ask-input"
          value={prompt}
          placeholder="Ask about the data…"
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') ask(prompt);
          }}
        />
        <button className="mob-btn primary ask-send" onClick={() => ask(prompt)} disabled={busy}>
          {busy ? '…' : 'Ask'}
        </button>
      </div>
      <button
        className="ask-find"
        onClick={() => ask(prompt || 'Where does occupancy data live?', 'find')}
        disabled={busy}
      >
        Find where data lives instead
      </button>
    </div>
  );
}

export default AskTab;
