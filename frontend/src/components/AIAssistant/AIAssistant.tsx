import { useState } from 'react';
import { aiGenerate, aiFix } from '../../services/api';
import { AIResponse } from '../../types';
import { VscSparkle, VscClose, VscWand } from 'react-icons/vsc';
import './AIAssistant.css';

interface Props {
  serverId?: number;
  database?: string;
  currentSql: string;
  lastError?: string | null;
  onClose: () => void;
  onInsertSql: (sql: string) => void;
}

interface Turn {
  prompt: string;
  response: AIResponse | null;
  loading: boolean;
}

function AIAssistant({ serverId, database, currentSql, lastError, onClose, onInsertSql }: Props) {
  const [prompt, setPrompt] = useState('');
  const [history, setHistory] = useState<Turn[]>([]);

  const ready = !!serverId && !!database;

  const ask = async (kind: 'generate' | 'fix', userPrompt: string) => {
    if (!ready) return;
    const turn: Turn = { prompt: userPrompt, response: null, loading: true };
    setHistory((h) => [...h, turn]);
    try {
      const res =
        kind === 'generate'
          ? await aiGenerate(serverId!, database!, userPrompt, currentSql)
          : await aiFix(serverId!, database!, currentSql, lastError || '');
      setHistory((h) => h.map((t) => (t === turn ? { ...t, response: res, loading: false } : t)));
    } catch (err: any) {
      setHistory((h) =>
        h.map((t) =>
          t === turn
            ? {
                ...t,
                loading: false,
                response: {
                  sql: null,
                  explanation: '',
                  error: err.response?.data?.detail || err.message,
                },
              }
            : t
        )
      );
    }
  };

  const handleGenerate = () => {
    if (!prompt.trim()) return;
    ask('generate', prompt.trim());
    setPrompt('');
  };

  const handleFix = () => {
    if (!currentSql.trim() || !lastError) return;
    ask('fix', `Fix this query — error: ${lastError}`);
  };

  return (
    <div className="ai-assistant">
      <div className="ai-header">
        <div className="ai-title">
          <VscSparkle /> AI Assistant
        </div>
        <button className="ai-close" onClick={onClose} title="Close">
          <VscClose />
        </button>
      </div>

      {!ready && <div className="ai-warn">Select a server and database first.</div>}

      <div className="ai-history">
        {history.length === 0 && (
          <div className="ai-empty">
            Ask me to write a T-SQL query or fix one that errored. I can see your current editor
            contents and the database schema.
          </div>
        )}
        {history.map((t, i) => (
          <div key={i} className="ai-turn">
            <div className="ai-user">{t.prompt}</div>
            {t.loading && <div className="ai-loading">Thinking…</div>}
            {t.response?.error && <div className="ai-error">{t.response.error}</div>}
            {t.response && !t.response.error && (
              <div className="ai-reply">
                <pre className="ai-explain">{t.response.explanation}</pre>
                {t.response.sql && (
                  <button className="ai-insert" onClick={() => onInsertSql(t.response!.sql!)}>
                    <VscWand /> Insert into editor
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="ai-input">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. Top 10 sites by revenue last month"
          rows={3}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              handleGenerate();
            }
          }}
        />
        <div className="ai-actions">
          <button onClick={handleGenerate} disabled={!ready || !prompt.trim()}>
            Generate SQL
          </button>
          <button onClick={handleFix} disabled={!ready || !currentSql.trim() || !lastError}>
            Fix my query
          </button>
        </div>
      </div>
    </div>
  );
}

export default AIAssistant;
