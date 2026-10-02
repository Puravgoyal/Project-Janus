import React, { useState, useEffect } from 'react';
import { Button, Slider, Input, Modal } from '../components/SkipperUI';
import { GlowingCard } from '../components/ReactBits';

const TRAIT_MAPPINGS = {
  order_chaos: [
    { threshold: 20, text: "Extremely ordered and methodical" },
    { threshold: 40, text: "Leans towards structure" },
    { threshold: 60, text: "Balanced and adaptable" },
    { threshold: 80, text: "Unpredictable and chaotic" },
    { threshold: 100, text: "Highly chaotic and erratic" }
  ],
  optimism_cynicism: [
    { threshold: 20, text: "Radiantly optimistic" },
    { threshold: 40, text: "Hopeful and positive" },
    { threshold: 60, text: "Realist with balanced views" },
    { threshold: 80, text: "Skeptical and cynical" },
    { threshold: 100, text: "Deeply pessimistic and bitter" }
  ],
  intro_extro: [
    { threshold: 20, text: "Deeply introverted and solitary" },
    { threshold: 40, text: "Reserved and quiet" },
    { threshold: 60, text: "Ambivert, socially flexible" },
    { threshold: 80, text: "Outgoing and sociable" },
    { threshold: 100, text: "Highly extroverted and loud" }
  ]
};

const mapSliderValue = (key, val) => {
  const map = TRAIT_MAPPINGS[key];
  for (const bucket of map) {
    if (val <= bucket.threshold) return bucket.text;
  }
  return map[map.length - 1].text;
};

const PersonaForge = () => {
  const [personas, setPersonas] = useState([]);
  const [editingPersona, setEditingPersona] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  
  // Forge State
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState('');
  const [suggestingTags, setSuggestingTags] = useState(false);
  const [sliders, setSliders] = useState({
    order_chaos: 50,
    optimism_cynicism: 50,
    intro_extro: 50
  });

  const fetchPersonas = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/personas');
      if (res.ok) setPersonas(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchPersonas();
  }, []);

  const handleSuggestTags = async () => {
    if (!description) return;
    setSuggestingTags(true);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/personas/suggest-tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description })
      });
      if (res.ok) {
        const generatedTags = await res.json();
        const currentTags = tags ? tags.split(',').map(t=>t.trim()) : [];
        const newTags = [...new Set([...currentTags, ...generatedTags])].filter(Boolean).join(', ');
        setTags(newTags);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setSuggestingTags(false);
    }
  };

  const handleSave = async () => {
    const personalityTraits = [
      mapSliderValue('order_chaos', sliders.order_chaos),
      mapSliderValue('optimism_cynicism', sliders.optimism_cynicism),
      mapSliderValue('intro_extro', sliders.intro_extro)
    ];

    const payload = {
      name,
      character_name: name,
      description,
      traits: tags ? tags.split(',').map(t=>t.trim()) : [],
      personality: personalityTraits,
      system_prompt: `You are ${name}. ${description}\nPersonality: ${personalityTraits.join('. ')}`
    };

    try {
      let url = 'http://127.0.0.1:8000/api/personas/forge';
      let method = 'POST';
      
      if (editingPersona && editingPersona.id) {
        url = `http://127.0.0.1:8000/api/personas/${editingPersona.id}`;
        method = 'PUT';
      }

      await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      
      setIsModalOpen(false);
      fetchPersonas();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDelete = async (id) => {
    if(!confirm("Delete this persona?")) return;
    try {
      await fetch(`http://127.0.0.1:8000/api/personas/${id}`, { method: 'DELETE' });
      fetchPersonas();
    } catch(e) {
      console.error(e);
    }
  }

  const openForge = (persona = null) => {
    if (persona) {
      setEditingPersona(persona);
      setName(persona.name || '');
      setDescription(persona.description || '');
      setTags((persona.traits || []).join(', '));
      setSliders({
        order_chaos: 50, optimism_cynicism: 50, intro_extro: 50 // In a real app we'd reverse-map this
      });
    } else {
      setEditingPersona(null);
      setName('');
      setDescription('');
      setTags('');
      setSliders({ order_chaos: 50, optimism_cynicism: 50, intro_extro: 50 });
    }
    setIsModalOpen(true);
  };

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-light text-white">Persona Studio & Forge</h1>
        <Button onClick={() => openForge()}>+ New Persona</Button>
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
                <button onClick={() => openForge(p)} className="text-gray-400 hover:text-indigo-400" title="Edit">✏️</button>
                {p.id !== 'janus' && <button onClick={() => handleDelete(p.id)} className="text-gray-400 hover:text-red-400" title="Delete">🗑️</button>}
              </div>
            </div>
            <p className="text-sm text-gray-400 line-clamp-3">{p.description || p.tagline}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {(p.traits || []).slice(0,3).map((t, i) => (
                <span key={i} className="px-2 py-1 bg-gray-700 rounded-md text-xs text-gray-300">{t}</span>
              ))}
            </div>
          </GlowingCard>
        ))}
      </div>

      <Modal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)} title={editingPersona ? "Edit Persona" : "Forge New Persona"}>
        <div className="space-y-4">
          <Input label="Character Name" value={name} onChange={setName} placeholder="e.g. Captain Vance" />
          
          <div className="flex flex-col mb-4">
            <label className="mb-1 text-sm font-medium text-gray-300">Description / Archetype</label>
            <textarea 
              value={description} onChange={e=>setDescription(e.target.value)}
              className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-md text-white focus:ring-2 focus:ring-indigo-500 max-h-32 min-h-[80px]"
            />
          </div>

          <div className="flex items-end space-x-2">
            <div className="flex-1">
              <Input label="Tags (Comma separated)" value={tags} onChange={setTags} placeholder="Cyberpunk, Pilot..." className="mb-0" />
            </div>
            <Button onClick={handleSuggestTags} variant="secondary" className="mb-[2px]">
              {suggestingTags ? "✨..." : "✨ Auto-Suggest"}
            </Button>
          </div>

          <div className="pt-4 border-t border-gray-700">
            <h4 className="text-sm font-medium text-indigo-400 mb-2">Mood & Trait Sliders</h4>
            <Slider 
              label="Order/Chaos" leftLabel="Order" rightLabel="Chaos"
              value={sliders.order_chaos} onChange={v => setSliders({...sliders, order_chaos: v})} 
            />
            <Slider 
              label="Optimism/Cynicism" leftLabel="Optimism" rightLabel="Cynicism"
              value={sliders.optimism_cynicism} onChange={v => setSliders({...sliders, optimism_cynicism: v})} 
            />
            <Slider 
              label="Introvert/Extrovert" leftLabel="Introvert" rightLabel="Extrovert"
              value={sliders.intro_extro} onChange={v => setSliders({...sliders, intro_extro: v})} 
            />
          </div>

          <div className="pt-4 flex justify-end space-x-3">
            <Button onClick={() => setIsModalOpen(false)} variant="ghost">Cancel</Button>
            <Button onClick={handleSave} variant="primary">Save to Forge</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default PersonaForge;
