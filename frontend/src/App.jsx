import React, { useState, useEffect } from 'react';
import Home from './views/Home';
import PersonaForge from './views/PersonaForge';
import AdventureLobby from './views/AdventureLobby';
import { AppProvider, useApp } from './AppContext';
import { API_BASE } from './api';

function AppShell() {
  const [currentView, setCurrentView] = useState('home');
  // Poll health endpoint (not /api/state) for status only
  const [engineStatus, setEngineStatus] = useState({ gpu: null, cpu: null });
  const { incognito } = useApp();

  useEffect(() => {
    let active = true;
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/health`);
        if (!active) return;
        if (res.ok) {
          const data = await res.json();
          setEngineStatus(data.engines || { gpu: false, cpu: false });
        } else {
          // Clear indicators on non-200 response
          setEngineStatus({ gpu: false, cpu: false });
        }
      } catch {
        if (!active) return;
        setEngineStatus({ gpu: false, cpu: false });
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const renderView = () => {
    switch (currentView) {
      case 'home': return <Home />;
      case 'forge': return <PersonaForge />;
      case 'adventure': return <AdventureLobby />;
      default: return <Home />;
    }
  };

  const navBtn = (view, label) => (
    <button
      key={view}
      onClick={() => setCurrentView(view)}
      className={`w-full text-left px-4 py-3 rounded-xl transition-all ${
        currentView === view
          ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
          : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
      }`}
    >
      {label}
    </button>
  );

  const engineDot = (online) => {
    if (online === null) return 'bg-gray-600'; // unknown / loading
    return online ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : 'bg-red-500';
  };

  return (
    <div className="flex min-h-screen">
      {/* Sidebar Navigation */}
      <nav className="w-64 bg-gray-900 border-r border-gray-800 p-6 flex flex-col shadow-xl z-10">
        <div className="flex items-center space-x-2 mb-8">
          <div className="text-2xl font-light text-indigo-400 tracking-widest">JANUS</div>
          {incognito && (
            <span className="text-xs bg-rose-900/30 border border-rose-500/30 text-rose-400 px-1.5 py-0.5 rounded-full">
              🕶 Incognito
            </span>
          )}
        </div>

        <div className="space-y-4">
          {navBtn('home', 'System Core')}
          {navBtn('forge', 'Persona Forge')}
          {navBtn('adventure', 'Adventure RPG')}
        </div>

        {/* Engine Status */}
        <div className="mt-auto space-y-2 pt-8 border-t border-gray-800">
          <div className="text-xs text-gray-500 uppercase tracking-widest mb-3">Engine Status</div>
          <div className="flex items-center text-sm">
            <span className={`w-2 h-2 rounded-full mr-3 transition-colors ${engineDot(engineStatus.gpu)}`} />
            <span className={engineStatus.gpu ? 'text-gray-300' : 'text-gray-600'}>GPU (11434)</span>
          </div>
          <div className="flex items-center text-sm">
            <span className={`w-2 h-2 rounded-full mr-3 transition-colors ${engineDot(engineStatus.cpu)}`} />
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

export default function App() {
  return (
    <AppProvider>
      <AppShell />
    </AppProvider>
  );
}
