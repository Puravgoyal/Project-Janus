import React, { useState, useEffect, useCallback } from 'react';
import { Button, Slider, Input, Modal } from '../components/SkipperUI';
import { GlowingCard } from '../components/ReactBits';
import { API_BASE, sseStream } from '../api';
import { useApp } from '../AppContext';

const TRAIT_MAPPINGS = {
  order_chaos: [
    { threshold: 20, text: 'Extremely ordered and methodical' },
    { threshold: 40, text: 'Leans towards structure' },
    { threshold: 60, text: 'Balanced and adaptable' },
    { threshold: 80, text: 'Unpredictable and chaotic' },
    { threshold: 100, text: 'Highly chaotic and erratic' },
  ],
  optimism_cynicism: [
    { threshold: 20, text: 'Radiantly optimistic' },
    { threshold: 40, text: 'Hopeful and positive' },
    { threshold: 60, text: 'Realist with balanced views' },
    { threshold: 80, text: 'Skeptical and cynical' },
    { threshold: 100, text: 'Deeply pessimistic and bitter' },
  ],
  intro_extro: [
    { threshold: 20, text: 'Deeply introverted and solitary' },
    { threshold: 40, text: 'Reserved and quiet' },
    { threshold: 60, text: 'Ambivert, socially flexible' },
    { threshold: 80, text: 'Outgoing and sociable' },
    { threshold: 100, text: 'Highly extroverted and loud' },
  ],
};

const mapSliderValue = (key, val) => {
  const map = TRAIT_MAPPINGS[key];
  for (const bucket of map) {
    if (val <= bucket.threshold) return bucket.text;
  }
  return map[map.length - 1].text;
};

const reverseMapSlider = (key, text) => {
  if (!text) return 50;
  const map = TRAIT_MAPPINGS[key];
  for (const bucket of map) {
    if (bucket.text.toLowerCase() === text.toLowerCase()) {
      return Math.round((bucket.threshold + (map[map.indexOf(bucket) - 1]?.threshold ?? 0)) / 2);
    }
  }
  return 50;
};

const emptySliders = () => ({ order_chaos: 50, optimism_cynicism: 50, intro_extro: 50 });

const msgId = () => `msg_${Date.now()}_${Math.random().toString(36).slice(2)}`;

/**
 * PersonaChat — a per-persona inline chat panel
 */
function PersonaChat({ persona, onClose }) {
  const { incognito: globalIncognito, getPersonaHistory, setPersonaHistoryForMode } = useApp();
  const isDisposable = Boolean(persona.is_disposable || persona.incognito);
  const effectiveIncognito = isDisposable || globalIncognito;

  // Disposable conversations use isolated local history that never writes to persistent persona storage
  const [localHistory, setLocalHistory] = useState(() => (
    persona.greeting ? [{ id: msgId(), role: 'assistant', content: persona.greeting }] : []
  ));

  const history = isDisposable ? localHistory : getPersonaHistory(persona.id);
  const setHistory = isDisposable ? setLocalHistory : ((updater) => setPersonaHistoryForMode(persona.id, updater));

  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const abortRef = React.useRef(null);
  const isLoadingRef = React.useRef(false);
  const bottomRef = React.useRef(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [history]);

  React.useEffect(() => {
    return () => { if (abortRef.current) abortRef.current.abort(); };
  }, []);

  React.useEffect(() => {
    if (history.length === 0 && persona.greeting) {
      setHistory([{ id: msgId(), role: 'assistant', content: persona.greeting }]);
    }
  }, [persona.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSend = async () => {
    const text = input.trim();
    if (!text || isLoadingRef.current) return;

    isLoadingRef.current = true;
    setLoading(true);
    setError('');

    const userMsgId = msgId();
    const assistantMsgId = msgId();
    setHistory(prev => [
      ...prev,
      { id: userMsgId, role: 'user', content: text },
      { id: assistantMsgId, role: 'assistant', content: '' },
    ]);
    setInput('');

    const historyToSend = history
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .map(m => ({ role: m.role, content: m.content }));

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_BASE}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          message: text,
          mode: 'persona',
          persona_id: persona.id,
          persona,
          incognito: effectiveIncognito,
          history: historyToSend,
        }),
      });

      if (!response.ok) {
        const errBody = await response.json().catch(() => ({}));
        throw new Error(errBody.detail || `HTTP ${response.status}`);
      }

      let gotToken = false;
      for await (const data of sseStream(response)) {
        if (controller.signal.aborted) break;
        if (data.error) {
          setError(data.error);
          setHistory(prev => prev.map(m =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: m.content
                    ? `${m.content}\n\n*[Interrupted: ${data.error}]*`
                    : `*Error: ${data.error}*`,
                }
              : m
          ));
          gotToken = true;
          break;
        }
        if (data.token) {
          gotToken = true;
          setHistory(prev => prev.map(m =>
            m.id === assistantMsgId
              ? { ...m, content: m.content + data.token }
              : m
          ));
        }
        if (data.done) break;
      }
      if (!gotToken) {
        setHistory(prev => prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, content: `*${persona.name} is not responding.*` }
            : m
        ));
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        setHistory(prev => prev.filter(m => m.id !== assistantMsgId));
      } else {
        setError(err.message || 'Connection error');
        setHistory(prev => prev.map(m =>
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-2xl h-[80vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div className="flex items-center space-x-3">
            <span className="text-2xl">{persona.avatar || '👤'}</span>
            <div>
              <h3 className="text-lg font-medium text-white">{persona.name}</h3>
              <p className="text-xs text-gray-400">{persona.tagline}</p>
            </div>
          </div>
          <div className="flex items-center space-x-2">
            {error && <span className="text-xs text-rose-400" title={error}>⚠ Error</span>}
            <button onClick={onClose} className="text-gray-400 hover:text-white p-1 rounded">✕</button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {history.map(msg => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[80%] rounded-2xl p-4 text-sm ${
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
              <div className="bg-gray-800 border border-gray-700 rounded-2xl p-4 flex items-center space-x-2">
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
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            placeholder={`Speak to ${persona.name}...`}
            className="flex-1 bg-gray-800 text-gray-100 border border-gray-700 rounded-xl p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none max-h-24"
            rows="1"
            disabled={loading}
          />
          <Button onClick={handleSend} variant="primary" className="rounded-xl" disabled={!input.trim() || loading}>
            <svg className="w-4 h-4 transform rotate-90" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
            </svg>
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function PersonaForge() {
  const { incognito } = useApp();
  const [personas, setPersonas] = useState([]);
  const [editingPersona, setEditingPersona] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isWikiModalOpen, setIsWikiModalOpen] = useState(false);
  const [chatPersona, setChatPersona] = useState(null);
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [enhancing, setEnhancing] = useState(false);
  const [enhancedCharData, setEnhancedCharData] = useState(null);

  // Forge form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState('');
  const [roleplayStyle, setRoleplayStyle] = useState('');
  const [suggestingTags, setSuggestingTags] = useState(false);
  const [sliders, setSliders] = useState(emptySliders());
  const [slidersTouched, setSlidersTouched] = useState(false);

  // Wiki Ingest State
  const [wikiName, setWikiName] = useState('');
  const [wikiText, setWikiText] = useState('');
  const [wikiLoading, setWikiLoading] = useState(false);
  const [wikiError, setWikiError] = useState('');

  const fetchPersonas = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/personas`);
      if (res.ok) setPersonas(await res.json());
    } catch (e) {
      console.error('fetchPersonas:', e);
    }
  }, []);

  useEffect(() => { fetchPersonas(); }, [fetchPersonas]);

  const handleSuggestTags = async () => {
    if (!description) return;
    setSuggestingTags(true);
    try {
      const res = await fetch(`${API_BASE}/api/personas/suggest-tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description }),
      });
      if (res.ok) {
        const generatedTags = await res.json();
        if (Array.isArray(generatedTags)) {
          const currentTags = tags ? tags.split(',').map(t => t.trim()) : [];
          const newTags = [...new Set([...currentTags, ...generatedTags])].filter(Boolean).join(', ');
          setTags(newTags);
        }
      }
    } catch (e) {
      console.error('suggestTags:', e);
    } finally {
      setSuggestingTags(false);
    }
  };

  const handleEnhance = async () => {
    const prompt = description.trim() || name.trim();
    if (!prompt) {
      setFormError('Please enter a Name or Description before enhancing.');
      return;
    }
    setEnhancing(true);
    setFormError('');
    try {
      const res = await fetch(`${API_BASE}/api/personas/enhance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_prompt: prompt, allow_nsfw: false }),
      });
      if (res.ok) {
        const data = await res.json();
        const char = data.character || {};
        setEnhancedCharData(char);
        setSlidersTouched(false);
        if (char.name) setName(char.name);
        if (char.personality?.archetype) setDescription(char.personality.archetype);
        if (char.personality?.core_traits) {
          setTags(char.personality.core_traits.join(', '));
        }
        if (char.roleplay_style !== undefined && char.roleplay_style !== null) {
          setRoleplayStyle(char.roleplay_style);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setFormError(err.detail || 'Enhancement failed.');
      }
    } catch (e) {
      setFormError(`Enhancement error: ${e.message}`);
    } finally {
      setEnhancing(false);
    }
  };

  const handleIngestWiki = async () => {
    if (!wikiName.trim() || !wikiText.trim()) {
      setWikiError('Both Character Name and Biographical Text are required.');
      return;
    }
    setWikiLoading(true);
    setWikiError('');
    try {
      const res = await fetch(`${API_BASE}/api/personas/compile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character_name: wikiName.trim(),
          raw_text: wikiText.trim(),
          incognito: Boolean(incognito),
        }),
      });
      if (res.ok) {
        setIsWikiModalOpen(false);
        setWikiName('');
        setWikiText('');
        fetchPersonas();
      } else {
        const err = await res.json().catch(() => ({}));
        setWikiError(err.detail || 'Compilation failed.');
      }
    } catch (e) {
      setWikiError(`Ingestion error: ${e.message}`);
    } finally {
      setWikiLoading(false);
    }
  };

  const buildCharacterData = (targetName, targetDesc, targetTags, targetSliders, targetRoleplay) => {
    const sliderDerivedTraits = [
      mapSliderValue('order_chaos', targetSliders.order_chaos),
      mapSliderValue('optimism_cynicism', targetSliders.optimism_cynicism),
      mapSliderValue('intro_extro', targetSliders.intro_extro),
    ];

    // Destructure editingPersona to avoid recursively nesting previous forge_schema
    const { forge_schema: _prevForge, ...cleanBaseFields } = editingPersona || {};
    const enhanced = enhancedCharData || {};

    const parsedTags = targetTags
      ? targetTags.split(',').map(t => t.trim()).filter(Boolean)
      : [];

    // Core traits resolution:
    // If the user explicitly moved sliders (slidersTouched === true), use slider traits.
    // Otherwise, preserve enhanced traits, user-entered tags, or previous saved traits.
    let resolvedCoreTraits;
    if (slidersTouched) {
      resolvedCoreTraits = sliderDerivedTraits;
    } else if (Array.isArray(enhanced.personality?.core_traits) && enhanced.personality.core_traits.length > 0) {
      resolvedCoreTraits = enhanced.personality.core_traits;
    } else if (parsedTags.length > 0) {
      resolvedCoreTraits = parsedTags;
    } else if (Array.isArray(_prevForge?.personality?.core_traits) && _prevForge.personality.core_traits.length > 0) {
      resolvedCoreTraits = _prevForge.personality.core_traits;
    } else if (Array.isArray(cleanBaseFields.traits) && cleanBaseFields.traits.length > 0) {
      resolvedCoreTraits = cleanBaseFields.traits;
    } else {
      resolvedCoreTraits = sliderDerivedTraits;
    }

    const personalityObj = {
      archetype: targetDesc || enhanced.personality?.archetype || _prevForge?.personality?.archetype || cleanBaseFields.personality?.archetype || targetName.trim(),
      core_traits: resolvedCoreTraits,
      flaws: enhanced.personality?.flaws || _prevForge?.personality?.flaws || cleanBaseFields.personality?.flaws || ['Reserved'],
    };

    const emotionObj = enhanced.emotion || _prevForge?.emotion || cleanBaseFields.emotion || {
      default_mood: 'Composed',
      reaction_to_stress: 'Focused',
      speech_style: 'Clear and deliberate',
    };

    const physicalityObj = enhanced.physicality || _prevForge?.physicality || cleanBaseFields.physicality || {
      appearance: 'Distinctive demeanor',
      body_language: 'Balanced and purposeful',
    };

    const matureObj = enhanced.mature_themes || _prevForge?.mature_themes || cleanBaseFields.mature_themes || {
      nsfw_enabled: false,
      boundaries: '',
      mature_dynamics: '',
    };

    // Roleplay style: distinguish intentionally cleared string from undefined/null
    const cleanRoleplay = (targetRoleplay !== undefined && targetRoleplay !== null)
      ? targetRoleplay
      : (enhanced.roleplay_style ?? cleanBaseFields.roleplay_style ?? '');

    const characterObj = {
      ...cleanBaseFields,
      name: targetName.trim(),
      description: targetDesc,
      traits: resolvedCoreTraits,
      personality_traits: resolvedCoreTraits,
      personality: personalityObj,
      emotion: emotionObj,
      physicality: physicalityObj,
      mature_themes: matureObj,
      roleplay_style: cleanRoleplay,
      _slider_values: targetSliders,
    };

    // Bounded schema: never recursively nest characterObj or previous forge_schema
    characterObj.forge_schema = {
      name: characterObj.name,
      personality: personalityObj,
      emotion: emotionObj,
      physicality: physicalityObj,
      mature_themes: matureObj,
      roleplay_style: cleanRoleplay,
      _slider_values: targetSliders,
    };

    return characterObj;
  };

  const handleSave = async () => {
    if (!name.trim()) {
      setFormError('Character name is required.');
      return;
    }
    if (saving) return;

    const isEdit = Boolean(editingPersona?.id);
    if (isEdit && incognito) {
      setFormError('Persistent persona editing is disabled while Incognito is active to protect stored character data. Use "Launch Disposable" instead to test changes.');
      return;
    }

    setFormError('');
    setSaving(true);

    const characterData = buildCharacterData(name, description, tags, sliders, roleplayStyle);
    const url = isEdit
      ? `${API_BASE}/api/personas/${editingPersona.id}`
      : `${API_BASE}/api/personas/forge`;
    const method = isEdit ? 'PUT' : 'POST';
    const body = isEdit
      ? characterData
      : JSON.stringify({ character_data: characterData, incognito });

    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: typeof body === 'string' ? body : JSON.stringify(body),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setFormError(errData.detail || `Save failed (HTTP ${res.status})`);
        setSaving(false);
        return;
      }

      setIsModalOpen(false);
      fetchPersonas();
    } catch (e) {
      setFormError(e.message || 'Network error — could not save persona.');
    } finally {
      setSaving(false);
    }
  };

  const handleLaunchDisposable = async () => {
    if (!name.trim()) {
      setFormError('Character name is required to launch.');
      return;
    }
    setSaving(true);
    setFormError('');
    try {
      const characterData = buildCharacterData(name, description, tags, sliders, roleplayStyle);
      const res = await fetch(`${API_BASE}/api/personas/forge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_data: characterData, incognito: true }),
      });
      if (res.ok) {
        const data = await res.json();
        const ephemeral = data.character || characterData;
        ephemeral.is_disposable = true;
        ephemeral.incognito = true;
        setIsModalOpen(false);
        setChatPersona(ephemeral);
      } else {
        const err = await res.json().catch(() => ({}));
        setFormError(err.detail || 'Disposable launch failed.');
      }
    } catch (e) {
      setFormError(`Launch error: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id, personaName) => {
    if (id === 'janus') {
      alert('The core Janus persona cannot be deleted.');
      return;
    }
    if (!confirm(`Delete persona "${personaName}"? This cannot be undone.`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/personas/${id}`, { method: 'DELETE' });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        alert(errData.detail || `Delete failed (HTTP ${res.status})`);
        return;
      }
      fetchPersonas();
    } catch (e) {
      alert(`Network error: ${e.message}`);
    }
  };

  const openForge = (persona = null) => {
    setFormError('');
    setEnhancedCharData(null);
    setSlidersTouched(false);
    if (persona) {
      setEditingPersona(persona);
      setName(persona.name || '');
      setDescription(persona.description || '');
      setTags((persona.traits || []).join(', '));
      setRoleplayStyle(persona.roleplay_style || '');

      const stored = persona._slider_values || persona.forge_schema?._slider_values;
      if (stored && typeof stored === 'object') {
        setSliders({
          order_chaos: stored.order_chaos ?? 50,
          optimism_cynicism: stored.optimism_cynicism ?? 50,
          intro_extro: stored.intro_extro ?? 50,
        });
      } else {
        const traits = Array.isArray(persona.personality?.core_traits)
          ? persona.personality.core_traits
          : Array.isArray(persona.personality)
          ? persona.personality
          : [];
        setSliders({
          order_chaos: reverseMapSlider('order_chaos', traits[0]) ?? 50,
          optimism_cynicism: reverseMapSlider('optimism_cynicism', traits[1]) ?? 50,
          intro_extro: reverseMapSlider('intro_extro', traits[2]) ?? 50,
        });
      }
    } else {
      setEditingPersona(null);
      setName('');
      setDescription('');
      setTags('');
      setRoleplayStyle('');
      setSliders(emptySliders());
    }
    setIsModalOpen(true);
  };

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-light text-white">Persona Studio &amp; Forge</h1>
        <div className="flex space-x-3">
          <Button onClick={() => { setWikiError(''); setIsWikiModalOpen(true); }} variant="secondary">
            📖 Ingest Wiki/Text
          </Button>
          <Button onClick={() => openForge()}>+ New Persona</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {personas.map(p => (
          <GlowingCard key={p.id} active={false} className="group">
            <div className="flex justify-between items-start mb-4">
              <h3 className="text-xl font-medium text-white flex items-center">
                <span className="mr-2 text-2xl">{p.avatar || '👤'}</span>
                {p.name}
              </h3>
              <div className="opacity-0 group-hover:opacity-100 transition-opacity flex space-x-2">
                <button
                  onClick={() => setChatPersona(p)}
                  className="text-gray-400 hover:text-green-400"
                  title="Chat"
                >💬</button>
                <button
                  onClick={() => openForge(p)}
                  className="text-gray-400 hover:text-indigo-400"
                  title="Edit"
                >✏️</button>
                {p.id !== 'janus' && (
                  <button
                    onClick={() => handleDelete(p.id, p.name)}
                    className="text-gray-400 hover:text-red-400"
                    title="Delete"
                  >🗑️</button>
                )}
              </div>
            </div>
            <p className="text-sm text-gray-400 line-clamp-3">{p.description || p.tagline}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {(p.traits || []).slice(0, 3).map((t, i) => (
                <span key={i} className="px-2 py-1 bg-gray-700 rounded-md text-xs text-gray-300">{t}</span>
              ))}
            </div>
          </GlowingCard>
        ))}
      </div>

      {/* Forge / Edit Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => !saving && setIsModalOpen(false)}
        title={editingPersona ? `Edit: ${editingPersona.name}` : 'Forge New Persona'}
      >
        <div className="space-y-4">
          {formError && (
            <div className="bg-rose-900/30 border border-rose-500/50 rounded-lg p-3 text-rose-300 text-sm">
              ⚠ {formError}
            </div>
          )}

          <div className="flex items-end space-x-2">
            <div className="flex-1">
              <Input
                label="Character Name"
                value={name}
                onChange={setName}
                placeholder="e.g. Captain Vance"
                className="mb-0"
              />
            </div>
            <Button
              onClick={handleEnhance}
              variant="secondary"
              className="mb-[2px] whitespace-nowrap"
              disabled={enhancing || (!name.trim() && !description.trim())}
            >
              {enhancing ? 'Enhancing...' : '⚡ AI Enhance'}
            </Button>
          </div>

          <div className="flex flex-col mb-4">
            <label className="mb-1 text-sm font-medium text-gray-300">Description / Archetype</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-white focus:ring-2 focus:ring-indigo-500 max-h-24 min-h-[60px] resize-none"
            />
          </div>

          <div className="flex items-end space-x-2">
            <div className="flex-1">
              <Input
                label="Tags (Comma separated)"
                value={tags}
                onChange={setTags}
                placeholder="Cyberpunk, Pilot..."
                className="mb-0"
              />
            </div>
            <Button onClick={handleSuggestTags} variant="secondary" className="mb-[2px]" disabled={!description || suggestingTags}>
              {suggestingTags ? '✨...' : '✨ Auto-Suggest'}
            </Button>
          </div>

          <div className="flex flex-col mb-2">
            <label className="mb-1 text-sm font-medium text-gray-300">Roleplay Style &amp; Formatting Directives</label>
            <textarea
              value={roleplayStyle}
              onChange={e => setRoleplayStyle(e.target.value)}
              placeholder="e.g. Use asterisks for actions (*glances around*), sharp technical vocabulary..."
              className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-white focus:ring-2 focus:ring-indigo-500 max-h-20 min-h-[50px] resize-none text-xs"
            />
          </div>

          <div className="pt-2 border-t border-gray-700">
            <h4 className="text-sm font-medium text-indigo-400 mb-2">Mood &amp; Trait Sliders</h4>
            <Slider
              label="Order/Chaos" leftLabel="Order" rightLabel="Chaos"
              value={sliders.order_chaos}
              onChange={v => { setSlidersTouched(true); setSliders(s => ({ ...s, order_chaos: v })); }}
            />
            <Slider
              label="Optimism/Cynicism" leftLabel="Optimism" rightLabel="Cynicism"
              value={sliders.optimism_cynicism}
              onChange={v => { setSlidersTouched(true); setSliders(s => ({ ...s, optimism_cynicism: v })); }}
            />
            <Slider
              label="Introvert/Extrovert" leftLabel="Introvert" rightLabel="Extrovert"
              value={sliders.intro_extro}
              onChange={v => { setSlidersTouched(true); setSliders(s => ({ ...s, intro_extro: v })); }}
            />
          </div>

          <div className="pt-4 flex justify-between items-center">
            <Button
              onClick={handleLaunchDisposable}
              variant="secondary"
              className="text-xs text-rose-300 border-rose-500/30"
              disabled={saving || !name.trim()}
              title="Launch directly in incognito without saving to disk"
            >
              🕶 Launch Disposable
            </Button>
            <div className="flex space-x-2">
              <Button onClick={() => setIsModalOpen(false)} variant="ghost" disabled={saving}>Cancel</Button>
              <Button onClick={handleSave} variant="primary" disabled={saving || !name.trim()}>
                {saving ? 'Saving...' : 'Save to Forge'}
              </Button>
            </div>
          </div>
        </div>
      </Modal>

      {/* Ingest Wiki/Text Modal */}
      <Modal
        isOpen={isWikiModalOpen}
        onClose={() => !wikiLoading && setIsWikiModalOpen(false)}
        title="Ingest Biographical / Wiki Text"
      >
        <div className="space-y-4">
          {wikiError && (
            <div className="bg-rose-900/30 border border-rose-500/50 rounded-lg p-3 text-rose-300 text-sm">
              ⚠ {wikiError}
            </div>
          )}

          <Input
            label="Character Name"
            value={wikiName}
            onChange={setWikiName}
            placeholder="e.g. Hypatia of Alexandria"
          />

          <div className="flex flex-col">
            <label className="mb-1 text-sm font-medium text-gray-300">Biographical / Documentation Text</label>
            <textarea
              value={wikiText}
              onChange={e => setWikiText(e.target.value)}
              placeholder="Paste Wikipedia article, biography, or lore notes here..."
              className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-white focus:ring-2 focus:ring-indigo-500 min-h-[160px] max-h-48 resize-none text-sm"
            />
          </div>

          <div className="pt-4 flex justify-end space-x-3">
            <Button onClick={() => setIsWikiModalOpen(false)} variant="ghost" disabled={wikiLoading}>Cancel</Button>
            <Button onClick={handleIngestWiki} variant="primary" disabled={wikiLoading || !wikiName.trim() || !wikiText.trim()}>
              {wikiLoading ? 'Compiling Persona...' : 'Compile Persona Card'}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Persona Chat Panel */}
      {chatPersona && (
        <PersonaChat persona={chatPersona} onClose={() => setChatPersona(null)} />
      )}
    </div>
  );
}
