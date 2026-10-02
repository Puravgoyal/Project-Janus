import React, { createContext, useContext, useCallback, useState } from 'react';

/**
 * AppContext provides:
 * - incognito: boolean — session-wide privacy flag
 * - toggleIncognito: function
 * - assistantHistory: message array for the assistant chat (persistent across nav)
 * - personaHistories: map of personaId -> message array (persistent per persona)
 * - adventureIncognitoState: per-session adventure state for incognito (never shared globally)
 *
 * Navigation does NOT reset incognito. Incognito activity is held only in
 * separate history stores and never merged into persistent history.
 */

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [incognito, setIncognito] = useState(false);

  // Persistent assistant history survives navigation
  const [assistantHistory, setAssistantHistory] = useState([
    { id: 'init-0', role: 'assistant', content: 'Good morning, Demi. What is on the agenda?' }
  ]);

  // Per-persona conversation histories  { [personaId]: [{id, role, content}] }
  const [personaHistories, setPersonaHistories] = useState({});

  // Incognito chat histories — kept separate, never persisted
  const [incognitoAssistantHistory, setIncognitoAssistantHistory] = useState([]);
  const [incognitoPersonaHistories, setIncognitoPersonaHistories] = useState({});

  const toggleIncognito = useCallback(() => {
    setIncognito(prev => {
      if (!prev) {
        // Entering incognito: start fresh incognito histories
        setIncognitoAssistantHistory([]);
        setIncognitoPersonaHistories({});
      }
      return !prev;
    });
  }, []);

  const getAssistantHistory = useCallback(() => {
    return incognito ? incognitoAssistantHistory : assistantHistory;
  }, [incognito, incognitoAssistantHistory, assistantHistory]);

  const setAssistantHistoryForMode = useCallback((updater) => {
    if (incognito) {
      setIncognitoAssistantHistory(updater);
    } else {
      setAssistantHistory(updater);
    }
  }, [incognito]);

  const getPersonaHistory = useCallback((personaId) => {
    if (incognito) {
      return incognitoPersonaHistories[personaId] || [];
    }
    return personaHistories[personaId] || [];
  }, [incognito, incognitoPersonaHistories, personaHistories]);

  const setPersonaHistoryForMode = useCallback((personaId, updater) => {
    if (incognito) {
      setIncognitoPersonaHistories(prev => {
        const cur = prev[personaId] || [];
        const next = typeof updater === 'function' ? updater(cur) : updater;
        return { ...prev, [personaId]: next };
      });
    } else {
      setPersonaHistories(prev => {
        const cur = prev[personaId] || [];
        const next = typeof updater === 'function' ? updater(cur) : updater;
        return { ...prev, [personaId]: next };
      });
    }
  }, [incognito]);

  return (
    <AppContext.Provider value={{
      incognito,
      toggleIncognito,
      getAssistantHistory,
      setAssistantHistoryForMode,
      getPersonaHistory,
      setPersonaHistoryForMode,
    }}>
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}
