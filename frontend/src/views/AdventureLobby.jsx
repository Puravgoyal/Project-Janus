import React, { useState } from 'react';
import { Button, Input, Slider } from '../components/SkipperUI';
import { AnimatedBackground, AnimatedTextReveal } from '../components/ReactBits';
import Home from './Home';

const AdventureLobby = () => {
  const [prompt, setPrompt] = useState('');
  const [genres, setGenres] = useState('');
  const [tone, setTone] = useState('');
  const [equipment, setEquipment] = useState('');
  const [forbidden, setForbidden] = useState('');
  const [pacing, setPacing] = useState(50);
  
  const [loading, setLoading] = useState(false);
  const [inGame, setInGame] = useState(false);
  const [openingScene, setOpeningScene] = useState('');

  const handleStart = async () => {
    setLoading(true);
    
    let pacingText = "Balanced pacing";
    if (pacing < 30) pacingText = "Slow Burn";
    else if (pacing > 70) pacingText = "Action-Packed";

    const payload = {
      prompt,
      genres: genres ? genres.split(',').map(g=>g.trim()) : [],
      tone,
      starting_equipment: equipment,
      forbidden_magic_tech: forbidden,
      pacing: pacingText,
      nsfw_enabled: false
    };

    try {
      const res = await fetch('http://127.0.0.1:8000/api/adventure/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const data = await res.json();
        setOpeningScene(data.opening_scene || "The adventure begins...");
        setInGame(true);
      }
    } catch (e) {
      console.error(e);
      setOpeningScene("*The DM dropped their notes. Connection lost.*");
      setInGame(true);
    } finally {
      setLoading(false);
    }
  };

  // If in game, we ideally switch to a specialized RPG Chat View. 
  // For simplicity, we'll render a modified chat or redirect.
  // The directive says: "return the generated Opening Narrative Paragraph as the first message in the chat array."
  if (inGame) {
    return (
      <AnimatedBackground className="p-8">
        <div className="max-w-4xl mx-auto bg-gray-900/90 rounded-2xl shadow-2xl p-6 border border-gray-700 min-h-[600px]">
          <h2 className="text-2xl font-light text-indigo-400 mb-6 border-b border-gray-700 pb-4">Campaign Started</h2>
          <div className="bg-gray-800/80 p-5 rounded-xl border border-gray-700">
             <AnimatedTextReveal text={openingScene} className="text-gray-200 leading-relaxed font-serif" />
          </div>
          {/* Note: In a complete implementation, this would render a full RPG chat interface similar to Home but pointing to /api/adventure/action */}
          <div className="mt-8 text-center text-gray-500 text-sm">
            (Chat interface follows... "What do you do, Demi?")
          </div>
        </div>
      </AnimatedBackground>
    )
  }

  return (
    <AnimatedBackground className="flex items-center justify-center p-4">
      <div className="bg-gray-900/80 backdrop-blur-md border border-gray-700 p-8 rounded-2xl w-full max-w-2xl shadow-2xl">
        <h1 className="text-3xl font-light text-white mb-6 text-center">Create Campaign</h1>
        
        {loading ? (
          <div className="py-20 text-center flex flex-col items-center">
            <div className="w-16 h-16 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mb-6"></div>
            <AnimatedTextReveal text="The Game Master is preparing your world..." className="text-xl text-indigo-300" />
          </div>
        ) : (
          <div className="space-y-4">
            <Input label="World Premise" value={prompt} onChange={setPrompt} placeholder="A cyberpunk city under the ocean..." />
            <div className="grid grid-cols-2 gap-4">
              <Input label="Genres (Comma separated)" value={genres} onChange={setGenres} placeholder="Sci-Fi, Mystery" />
              <Input label="Tone" value={tone} onChange={setTone} placeholder="Gritty, Noir" />
            </div>
            
            <div className="grid grid-cols-2 gap-4">
              <Input label="Starting Equipment" value={equipment} onChange={setEquipment} placeholder="A rusty dagger, 50 credits" />
              <Input label="Forbidden Magic/Tech" value={forbidden} onChange={setForbidden} placeholder="AI constructs, Time travel" />
            </div>

            <Slider 
              label="Pacing" leftLabel="Slow Burn" rightLabel="Action-Packed"
              value={pacing} onChange={setPacing}
            />

            <Button onClick={handleStart} className="w-full mt-6 text-lg py-3">Start Campaign</Button>
          </div>
        )}
      </div>
    </AnimatedBackground>
  );
};

export default AdventureLobby;
