import React, { useState, useEffect } from 'react';
import Home from './views/Home';
import PersonaForge from './views/PersonaForge';
import AdventureLobby from './views/AdventureLobby';

function App() {
  const [currentView, setCurrentView] = useState('home');
  const [engineStatus, setEngineStatus] = useState({ gpu: false, cpu: false });

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch('http://127.0.0.1:8000/api/state');
        if (res.ok) {
          const data = await res.json();
          setEngineStatus(data.engines || { gpu: false, cpu: false });
        }
      } catch (e) {
        console.error(e);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const renderView = () => {
    switch (currentView) {
      case 'home': return <Home />;
      case 'forge': return <PersonaForge />;
      case 'adventure': return <AdventureLobby />;
      default: return <Home />;
    }
  };

  return (
    <div className="flex min-h-screen">
      {/* Sidebar Navigation */}
      <nav className="w-64 bg-gray-900 border-r border-gray-800 p-6 flex flex-col shadow-xl z-10">
        <div className="text-2xl font-light text-indigo-400 mb-8 tracking-widest">JANUS</div>
        <div className="space-y-4">
          <button 
            onClick={() => setCurrentView('home')} 
            className={`w-full text-left px-4 py-3 rounded-xl transition-all ${currentView === 'home' ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}
          >
            System Core
          </button>
          <button 
            onClick={() => setCurrentView('forge')} 
            className={`w-full text-left px-4 py-3 rounded-xl transition-all ${currentView === 'forge' ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}
          >
            Persona Forge
          </button>
          <button 
            onClick={() => setCurrentView('adventure')} 
            className={`w-full text-left px-4 py-3 rounded-xl transition-all ${currentView === 'adventure' ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}
          >
            Adventure RPG
          </button>
        </div>

        <div className="mt-auto space-y-2 pt-8 border-t border-gray-800">
          <div className="text-xs text-gray-500 uppercase tracking-widest mb-3">Engine Status</div>
          <div className="flex items-center text-sm">
            <span className={`w-2 h-2 rounded-full mr-3 ${engineStatus.gpu ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : 'bg-red-500'}`}></span>
            <span className={engineStatus.gpu ? 'text-gray-300' : 'text-gray-600'}>GPU (11434)</span>
          </div>
          <div className="flex items-center text-sm">
            <span className={`w-2 h-2 rounded-full mr-3 ${engineStatus.cpu ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : 'bg-red-500'}`}></span>
            <span className={engineStatus.cpu ? 'text-gray-300' : 'text-gray-600'}>CPU (11435)</span>
          </div>
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 bg-gray-950 relative overflow-hidden">
        {renderView()}
      </main>
    </div>
  );
}

export default App;
