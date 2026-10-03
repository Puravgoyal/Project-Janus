import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Button, Modal } from '../components/SkipperUI';
import { API_BASE, sseStream } from '../api';
import { useApp } from '../AppContext';


const msgId = () => `msg_${Date.now()}_${Math.random().toString(36).slice(2)}`;

/**
 * Minimal inline Markdown renderer — supports **bold**, *italic*, `code`, ```blocks```.
 * Does NOT use dangerouslySetInnerHTML.
 */
function MarkdownText({ content }) {
  if (!content) return null;

  const codeBlockRe = /```([\s\S]*?)```/g;
  const parts = [];
  let lastIdx = 0;
  let match;

  while ((match = codeBlockRe.exec(content)) !== null) {
    if (match.index > lastIdx) {
      parts.push({ type: 'text', value: content.slice(lastIdx, match.index) });
    }
    parts.push({ type: 'code-block', value: match[1] });
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < content.length) {
    parts.push({ type: 'text', value: content.slice(lastIdx) });
  }

  return (
    <div className="whitespace-pre-wrap leading-relaxed font-light">
      {parts.map((part, i) => {
        if (part.type === 'code-block') {
          return (
            <pre key={i} className="bg-gray-900 border border-gray-700 rounded-lg p-3 my-2 overflow-x-auto text-sm text-green-300 font-mono">
              {part.value.trim()}
            </pre>
          );
        }
        const segments = [];
        const inlineRe = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
        let iLast = 0;
        let iMatch;
        const txt = part.value;
        while ((iMatch = inlineRe.exec(txt)) !== null) {
          if (iMatch.index > iLast) {
            segments.push(<span key={`t${iLast}`}>{txt.slice(iLast, iMatch.index)}</span>);
          }
          const raw = iMatch[0];
          if (raw.startsWith('`')) {
            segments.push(<code key={`c${iMatch.index}`} className="bg-gray-900 text-green-300 px-1 rounded text-sm font-mono">{raw.slice(1, -1)}</code>);
          } else if (raw.startsWith('**')) {
            segments.push(<strong key={`b${iMatch.index}`}>{raw.slice(2, -2)}</strong>);
          } else {
            segments.push(<em key={`e${iMatch.index}`}>{raw.slice(1, -1)}</em>);
          }
          iLast = iMatch.index + raw.length;
        }
        if (iLast < txt.length) {
          segments.push(<span key={`t${iLast}`}>{txt.slice(iLast)}</span>);
        }
        return <span key={i}>{segments}</span>;
      })}
    </div>
  );
}

export default function Home() {
  const { incognito, toggleIncognito, getAssistantHistory, setAssistantHistoryForMode } = useApp();
  const messages = getAssistantHistory();
  const setMessages = setAssistantHistoryForMode;

  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const bottomRef = useRef(null);
  const abortRef = useRef(null);
  const isLoadingRef = useRef(false);

  // Reminders panel state
  const [showReminders, setShowReminders] = useState(false);
  const [reminders, setReminders] = useState([]);
  const [newReminderText, setNewReminderText] = useState('');
  const [newReminderPriority, setNewReminderPriority] = useState('medium');
  const [addingReminder, setAddingReminder] = useState(false);

  // Memory Inspector state
  const [showMemory, setShowMemory] = useState(false);
  const [memoryState, setMemoryState] = useState(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  const fetchReminders = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/reminders`);
      if (res.ok) {
        setReminders(await res.json());
      }
    } catch { /* non-fatal */ }
  }, []);

  const fetchMemory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/state`);
      if (res.ok) {
        setMemoryState(await res.json());
      }
    } catch { /* non-fatal */ }
  }, []);

  useEffect(() => {
    if (showReminders) fetchReminders();
  }, [showReminders, fetchReminders]);

  useEffect(() => {
    if (showMemory) fetchMemory();
  }, [showMemory, fetchMemory]);

  const handleToggleReminder = async (id, currentCompleted) => {
    try {
      const res = await fetch(`${API_BASE}/api/reminders/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ completed: !currentCompleted }),
      });
      if (res.ok) {
        fetchReminders();
      }
    } catch { /* non-fatal */ }
  };

  const handleAddReminder = async () => {
    if (!newReminderText.trim()) return;
    setAddingReminder(true);
    try {
      const res = await fetch(`${API_BASE}/api/reminders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: newReminderText.trim(), priority: newReminderPriority }),
      });
      if (res.ok) {
        setNewReminderText('');
        fetchReminders();
      }
    } catch { /* non-fatal */ } finally {
      setAddingReminder(false);
    }
  };

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isLoadingRef.current) return;

    setError('');
    isLoadingRef.current = true;
    setLoading(true);

    const userMsgId = msgId();
    const userMsg = { id: userMsgId, role: 'user', content: text };
    setMessages(prev => [...prev, userMsg]);
    setInput('');

    const historyToSend = messages
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .map(m => ({ role: m.role, content: m.content }));

    const assistantMsgId = msgId();
    setMessages(prev => [...prev, { id: assistantMsgId, role: 'assistant', content: '' }]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_BASE}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          message: text,
          mode: 'assistant',
          incognito,
          history: historyToSend,
        }),
      });

      if (!response.ok) {
        const errBody = await response.json().catch(() => ({}));
        throw new Error(errBody.detail || `HTTP ${response.status}`);
      }

      let gotToken = false;
      let streamInterrupted = false;
      let streamErrorMessage = '';

      for await (const data of sseStream(response)) {
        if (controller.signal.aborted) break;
        if (data.error) {
          streamInterrupted = true;
          streamErrorMessage = data.error;
          setError(data.error);
        }
        if (data.token) {
          gotToken = true;
          setMessages(prev => prev.map(m =>
            m.id === assistantMsgId
              ? { ...m, content: m.content + data.token }
              : m
          ));
        }
        if (data.done) break;
      }

      if (streamInterrupted) {
        setMessages(prev => prev.map(m =>
          m.id === assistantMsgId
            ? {
                ...m,
                content: (m.content ? `${m.content}\n\n` : '') + `*[Interrupted: ${streamErrorMessage || 'Stream terminated prematurely'}]*`,
              }
            : m
        ));
      } else if (!gotToken) {
        setMessages(prev => prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, content: '*Janus is offline or did not respond.*' }
            : m
        ));
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        setMessages(prev => prev.filter(m => m.id !== assistantMsgId));
      } else {
        setError(err.message || 'Connection error');
        setMessages(prev => prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, content: `*Error: ${err.message || 'Janus core unreachable'}*` }
            : m
        ));
      }
    } finally {
      abortRef.current = null;
      isLoadingRef.current = false;
      setLoading(false);
    }
  }, [input, incognito, messages, setMessages]);


  const handleCancel = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
  }, []);

  return (
    <div className="flex flex-col h-screen max-w-4xl mx-auto px-4 py-8">

      {/* Header with Navigation Controls */}
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center space-x-3">
          <h2 className="text-xl font-light text-indigo-300">System Core</h2>
          <button
            onClick={() => setShowReminders(prev => !prev)}
            className={`text-xs px-2.5 py-1 rounded-lg border transition-colors ${
              showReminders
                ? 'bg-indigo-600/30 border-indigo-500/50 text-indigo-200'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white'
            }`}
          >
            📋 Reminders
          </button>
          <button
            onClick={() => setShowMemory(prev => !prev)}
            className={`text-xs px-2.5 py-1 rounded-lg border transition-colors ${
              showMemory
                ? 'bg-indigo-600/30 border-indigo-500/50 text-indigo-200'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white'
            }`}
          >
            🧠 Memory
          </button>
        </div>

        <div className="flex items-center space-x-3">
          {error && (
            <span className="text-xs text-rose-400 mr-2 max-w-xs truncate" title={error}>
              ⚠ {error}
            </span>
          )}
          <span className={`text-sm ${incognito ? 'text-rose-400' : 'text-gray-500'}`}>
            {incognito ? 'Incognito Active' : 'Incognito Off'}
          </span>
          <button
            onClick={toggleIncognito}
            aria-label="Toggle incognito mode"
            className={`w-12 h-6 rounded-full p-1 transition-colors ${incognito ? 'bg-rose-500/20 border border-rose-500/50' : 'bg-gray-800 border border-gray-700'}`}
          >
            <div className={`w-4 h-4 rounded-full transition-transform ${incognito ? 'translate-x-6 bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]' : 'bg-gray-500'}`} />
          </button>
        </div>
      </div>

      {/* Reminders Drawer/Panel */}
      {showReminders && (
        <div className="bg-gray-900 border border-gray-700 rounded-xl p-4 mb-4 text-sm shadow-xl">
          <div className="flex justify-between items-center mb-3">
            <h3 className="font-medium text-indigo-300">Pending &amp; Active Reminders</h3>
            <button onClick={() => setShowReminders(false)} className="text-gray-400 hover:text-white text-xs">✕ Close</button>
          </div>

          <div className="space-y-2 max-h-48 overflow-y-auto mb-3 pr-1">
            {reminders.length === 0 ? (
              <p className="text-gray-500 text-xs italic">No reminders scheduled.</p>
            ) : (
              reminders.map(r => (
                <div key={r.id} className="flex items-center justify-between bg-gray-800/60 p-2 rounded-lg border border-gray-700/60">
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      checked={Boolean(r.completed)}
                      onChange={() => handleToggleReminder(r.id, r.completed)}
                      className="rounded accent-indigo-500 cursor-pointer"
                    />
                    <span className={`text-xs ${r.completed ? 'line-through text-gray-500' : 'text-gray-200'}`}>
                      {r.text}
                    </span>
                  </div>
                  <div className="flex items-center space-x-2 text-xs">
                    {r.priority && (
                      <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-semibold ${
                        r.priority === 'high' ? 'bg-rose-900/40 text-rose-300 border border-rose-700/40' : 'bg-gray-700 text-gray-300'
                      }`}>
                        {r.priority}
                      </span>
                    )}
                    {r.due_date && <span className="text-gray-500 text-[11px]">{r.due_date}</span>}
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="flex items-center space-x-2 pt-2 border-t border-gray-800">
            <input
              type="text"
              value={newReminderText}
              onChange={e => setNewReminderText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleAddReminder(); }}
              placeholder="Add reminder..."
              className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            <select
              value={newReminderPriority}
              onChange={e => setNewReminderPriority(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300"
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
            <Button onClick={handleAddReminder} variant="primary" className="py-1 px-3 text-xs" disabled={addingReminder || !newReminderText.trim()}>
              Add
            </Button>
          </div>
        </div>
      )}

      {/* Memory Inspector Modal */}
      <Modal isOpen={showMemory} onClose={() => setShowMemory(false)} title="System Memory &amp; Cognitive State">
        <div className="space-y-4 max-h-[60vh] overflow-y-auto text-sm pr-1">
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-indigo-400 mb-2">User Facts</h4>
            {memoryState?.facts?.length ? (
              <ul className="space-y-1">
                {memoryState.facts.map((f, i) => (
                  <li key={i} className="text-gray-300 text-xs bg-gray-800/80 p-2 rounded border border-gray-700/60">• {f}</li>
                ))}
              </ul>
            ) : (
              <p className="text-gray-500 text-xs italic">No personal facts recorded.</p>
            )}
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-indigo-400 mb-2">Active Work Context (Rolling Notes)</h4>
            {memoryState?.work_notes?.length ? (
              <ul className="space-y-1">
                {memoryState.work_notes.map((w, i) => (
                  <li key={i} className="text-gray-300 text-xs bg-gray-800/80 p-2 rounded border border-gray-700/60">
                    <span className="text-indigo-300 font-mono text-[10px]">[{w.category || 'note'}]</span> {w.note}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-gray-500 text-xs italic">No active work context recorded.</p>
            )}
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-indigo-400 mb-2">Profile Attributes</h4>
            <div className="bg-gray-800/80 p-2 rounded border border-gray-700/60 text-xs text-gray-300 space-y-1">
              <div><span className="text-gray-500">Name:</span> {memoryState?.user_profile?.name || 'Demi'}</div>
              <div><span className="text-gray-500">Role:</span> {memoryState?.user_profile?.role || 'Lead Architect'}</div>
              {memoryState?.user_profile?.preferences?.length ? (
                <div>
                  <span className="text-gray-500">Preferences:</span> {memoryState.user_profile.preferences.join(', ')}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </Modal>

      {/* Messages Thread */}
      <div className="flex-1 overflow-y-auto mb-4 space-y-6 pr-2">
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-2xl p-5 ${
              msg.role === 'user'
                ? 'bg-indigo-600/20 border border-indigo-500/30 text-indigo-50'
                : 'bg-gray-800 border border-gray-700 text-gray-200'
            }`}>
              <MarkdownText content={msg.content} />
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 border border-gray-700 rounded-2xl p-5 flex items-center space-x-2">
              <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input Command Bar */}
      <div className="bg-gray-800/80 backdrop-blur-md rounded-2xl p-2 border border-gray-700 shadow-2xl flex items-end">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Command Janus..."
          className="w-full bg-transparent text-gray-100 p-3 max-h-32 focus:outline-none resize-none"
          rows="1"
          disabled={loading}
        />
        {loading ? (
          <button
            onClick={handleCancel}
            title="Cancel"
            className="mb-1 mr-1 px-3 py-2 bg-rose-600/20 border border-rose-500/30 rounded-xl text-rose-400 hover:bg-rose-600/30 transition-colors"
          >
            ✕
          </button>
        ) : (
          <Button
            onClick={handleSend}
            variant="primary"
            className="mb-1 mr-1 rounded-xl"
            disabled={!input.trim()}
          >
            <svg className="w-5 h-5 transform rotate-90" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
            </svg>
          </Button>
        )}
      </div>

    </div>
  );
}
