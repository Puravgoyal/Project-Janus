import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Button, Input, Slider } from '../components/SkipperUI';
import { API_BASE, sseStream } from '../api';
import { useApp } from '../AppContext';


const msgId = () => `msg_${Date.now()}_${Math.random().toString(36).slice(2)}`;

/**
 * AdventureChat — full RPG chat that connects to /api/adventure/action (streaming).
 * Displays state (health, inventory, quests) and supports resuming an adventure.
 */
function AdventureChat({ openingScene, incognito, sessionToken, onRestart }) {
  const [messages, setMessages] = useState([
    { id: msgId(), role: 'assistant', content: openingScene },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [state, setState] = useState(null);

  const abortRef = useRef(null);
  const isLoadingRef = useRef(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    return () => { if (abortRef.current) abortRef.current.abort(); };
  }, []);

  // Load current adventure state (health, inventory, quests)
  const refreshState = useCallback(async () => {
    try {
      const tokenParam = sessionToken ? `&session_token=${encodeURIComponent(sessionToken)}` : '';
      const res = await fetch(`${API_BASE}/api/adventure/state?incognito=${incognito}${tokenParam}`);
      if (res.ok) setState(await res.json());
    } catch { /* non-fatal */ }
  }, [incognito, sessionToken]);

  useEffect(() => { refreshState(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleAction = async () => {
    const text = input.trim();
    if (!text || isLoadingRef.current) return;

    isLoadingRef.current = true;
    setLoading(true);
    setError('');

    const userMsgId = msgId();
    const assistantMsgId = msgId();
    setMessages(prev => [
      ...prev,
      { id: userMsgId, role: 'user', content: text },
      { id: assistantMsgId, role: 'assistant', content: '' },
    ]);
    setInput('');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_BASE}/api/adventure/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({ action: text, incognito, session_token: sessionToken }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${response.status}`);
      }

      let gotToken = false;
      for await (const data of sseStream(response)) {
        if (controller.signal.aborted) break;
        if (data.error) {
          setError(data.error);
          setMessages(prev => prev.map(m =>
            m.id === assistantMsgId
              ? { ...m, content: m.content ? `${m.content}\n\n*[Interrupted: ${data.error}]*` : `*Error: ${data.error}*` }
              : m
          ));
          gotToken = true;
          break;
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

      if (!gotToken) {
        setMessages(prev => prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, content: '*The Dungeon Master is not responding. Check engine status.*' }
            : m
        ));
      }

      // Refresh state immediately, then schedule syncs after background extraction completes on CPU
      await refreshState();
      setTimeout(() => refreshState(), 1800);
      setTimeout(() => refreshState(), 4000);
    } catch (err) {
      if (err.name === 'AbortError') {
        setMessages(prev => prev.filter(m => m.id !== assistantMsgId));
      } else {
        setError(err.message || 'Action failed');
        setMessages(prev => prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, content: `*Error: ${err.message}*` }
            : m
        ));
      }
    } finally {
      abortRef.current = null;
      isLoadingRef.current = false;
      setLoading(false);
    }
  };

  const charState = state?.character_state;

  return (
    <div className="flex h-full">
      {/* Chat column */}
      <div className="flex-1 flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <h2 className="text-lg font-light text-indigo-300">Campaign in Progress</h2>
          <div className="flex items-center space-x-2">
            {error && <span className="text-xs text-rose-400">⚠ {error}</span>}
            <button
              onClick={onRestart}
              className="text-xs text-gray-500 hover:text-gray-300 px-2 py-1 rounded border border-gray-700 hover:border-gray-500 transition-colors"
            >
              ↩ New Campaign
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map(msg => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[80%] rounded-xl p-4 text-sm ${
                msg.role === 'user'
                  ? 'bg-indigo-600/20 border border-indigo-500/30 text-indigo-50'
                  : 'bg-gray-800 border border-gray-700 text-gray-200'
              }`}>
                <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 flex items-center space-x-2">
                <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="p-3 border-t border-gray-700 flex items-end space-x-2">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAction(); } }}
            placeholder="What do you do, Demi?"
            className="flex-1 bg-gray-800 text-gray-100 border border-gray-700 rounded-xl p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none max-h-24"
            rows="1"
            disabled={loading}
          />
          <Button onClick={handleAction} variant="primary" className="rounded-xl" disabled={!input.trim() || loading}>
            <svg className="w-4 h-4 transform rotate-90" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
            </svg>
          </Button>
        </div>
      </div>

      {/* State sidebar */}
      {charState && (
        <div className="w-56 border-l border-gray-700 p-4 space-y-4 text-sm overflow-y-auto">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-2">Status</div>
            <div className="text-green-400 font-medium">{charState.health || 'Healthy'}</div>
          </div>
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-2">Inventory</div>
            <ul className="space-y-1">
              {(charState.inventory || []).map((item, i) => (
                <li key={i} className="text-gray-300">• {item}</li>
              ))}
              {(!charState.inventory || charState.inventory.length === 0) && (
                <li className="text-gray-600 italic">Empty</li>
              )}
            </ul>
          </div>
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-2">Quests</div>
            <ul className="space-y-1">
              {(charState.active_quests || []).map((q, i) => (
                <li key={i} className="text-yellow-300 text-xs">◆ {q}</li>
              ))}
              {(!charState.active_quests || charState.active_quests.length === 0) && (
                <li className="text-gray-600 italic text-xs">None</li>
              )}
            </ul>
          </div>
          <button
            onClick={refreshState}
            className="text-xs text-gray-600 hover:text-gray-400 transition-colors"
          >
            ↻ Refresh state
          </button>
        </div>
      )}
    </div>
  );
}

export default function AdventureLobby() {
  const { incognito } = useApp();
  const [prompt, setPrompt] = useState('');
  const [genres, setGenres] = useState('');
  const [tone, setTone] = useState('');
  const [equipment, setEquipment] = useState('');
  const [forbidden, setForbidden] = useState('');
  const [pacing, setPacing] = useState(50);

  const [loading, setLoading] = useState(false);
  const [startError, setStartError] = useState('');
  const [inGame, setInGame] = useState(false);
  const [openingScene, setOpeningScene] = useState('');
  // Private session token — kept only in memory, never written to disk or localStorage
  const [sessionToken, setSessionToken] = useState(null);

  // Check for resumable state on mount
  useEffect(() => {
    const checkResume = async () => {
      if (incognito) return; // incognito never resumes persistent state
      try {
        const res = await fetch(`${API_BASE}/api/adventure/state?incognito=false`);
        if (res.ok) {
          const data = await res.json();
          // Resume if history is non-empty
          if (data.history && data.history.length > 0) {
            const lastAssistant = [...data.history].reverse().find(m => m.role === 'assistant');
            if (lastAssistant?.content) {
              setOpeningScene(lastAssistant.content);
              setInGame(true);
            }
          }
        }
      } catch { /* non-fatal */ }
    };
    checkResume();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleStart = async () => {
    if (!prompt.trim()) {
      setStartError('Please provide a world premise to start the campaign.');
      return;
    }
    setLoading(true);
    setStartError('');

    let pacingText = 'Balanced pacing';
    if (pacing < 30) pacingText = 'Slow Burn';
    else if (pacing > 70) pacingText = 'Action-Packed';

    const payload = {
      prompt: prompt.trim(),
      genres: genres ? genres.split(',').map(g => g.trim()).filter(Boolean) : [],
      tone: tone.trim(),
      starting_equipment: equipment.trim(),
      forbidden_magic_tech: forbidden.trim(),
      pacing: pacingText,
      nsfw_enabled: false,
      incognito,
    };

    try {
      const res = await fetch(`${API_BASE}/api/adventure/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setStartError(errData.detail || `Failed to start campaign (HTTP ${res.status})`);
        setLoading(false);
        return;
      }

      const data = await res.json();
      const scene = data.opening_scene || '';

      // Only enter game state if we actually got content from the AI
      if (!scene || scene === '*The campaign begins...*') {
        setStartError('The Game Master did not generate an opening scene. Check that the GPU engine is online and the janus-chat model is loaded.');
        setLoading(false);
        return;
      }

      // Save session token (non-null only for incognito campaigns)
      setSessionToken(data.session_token || null);
      setOpeningScene(scene);
      setInGame(true);
    } catch (e) {
      setStartError(e.message || 'Network error starting campaign.');
    } finally {
      setLoading(false);
    }
  };

  const handleRestart = () => {
    setInGame(false);
    setOpeningScene('');
    setStartError('');
    setSessionToken(null); // clear private session token; never persist to storage
    setPrompt('');
    setGenres('');
    setTone('');
    setEquipment('');
    setForbidden('');
    setPacing(50);
  };

  if (inGame) {
    return (
      <div className="h-screen flex flex-col">
        <AdventureChat
          openingScene={openingScene}
          incognito={incognito}
          sessionToken={sessionToken}
          onRestart={handleRestart}
        />
      </div>
    );
  }

  return (
    <div className="flex items-start justify-center p-8 min-h-screen overflow-y-auto">
      <div className="bg-gray-900/80 backdrop-blur-md border border-gray-700 p-8 rounded-2xl w-full max-w-2xl shadow-2xl">
        <h1 className="text-3xl font-light text-white mb-2 text-center">Create Campaign</h1>
        <p className="text-sm text-gray-500 text-center mb-6">
          Forge your world, then let the Game Master open the scene.
        </p>

        {startError && (
          <div className="bg-rose-900/30 border border-rose-500/50 rounded-lg p-3 text-rose-300 text-sm mb-4">
            ⚠ {startError}
          </div>
        )}

        {loading ? (
          <div className="py-16 text-center flex flex-col items-center">
            <div className="w-16 h-16 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mb-6" />
            <p className="text-xl text-indigo-300">The Game Master is preparing your world...</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-col">
              <label className="mb-1 text-sm font-medium text-gray-300">World Premise <span className="text-rose-400">*</span></label>
              <textarea
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                placeholder="A neon-soaked megacity where corporations control water access..."
                className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[80px] resize-none"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Input label="Genres (Comma separated)" value={genres} onChange={setGenres} placeholder="Sci-Fi, Mystery" />
              <Input label="Tone" value={tone} onChange={setTone} placeholder="Gritty, Noir" />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Input label="Starting Equipment" value={equipment} onChange={setEquipment} placeholder="A rusty dagger, 50 credits" />
              <Input label="Forbidden Magic/Tech" value={forbidden} onChange={setForbidden} placeholder="Time travel, Mind control" />
            </div>

            <Slider
              label="Pacing" leftLabel="Slow Burn" rightLabel="Action-Packed"
              value={pacing} onChange={setPacing}
            />

            <Button onClick={handleStart} className="w-full mt-6 text-lg py-3" disabled={!prompt.trim()}>
              ▶ Begin Campaign
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
