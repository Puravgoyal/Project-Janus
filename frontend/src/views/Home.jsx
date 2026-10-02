import React, { useState, useEffect, useRef } from 'react';
import { AnimatedTextReveal } from '../components/ReactBits';
import { Button } from '../components/SkipperUI';

const Home = () => {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [incognito, setIncognito] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    // Initial Greeting
    setMessages([
      { role: 'assistant', content: "Good morning, Demi. What is on the agenda?" }
    ]);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim()) return;
    const userMsg = { role: 'user', content: input };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const response = await fetch('http://127.0.0.1:8000/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg.content, mode: 'assistant', incognito })
      });

      if (!response.ok) throw new Error("Network response was not ok");
      
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      
      setMessages(prev => [...prev, { role: 'assistant', content: '' }]);

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.replace('data: ', '').trim();
            if (dataStr) {
              try {
                const data = JSON.parse(dataStr);
                if (data.token) {
                  setMessages(prev => {
                    const newMessages = [...prev];
                    const lastIndex = newMessages.length - 1;
                    newMessages[lastIndex] = {
                      ...newMessages[lastIndex],
                      content: newMessages[lastIndex].content + data.token
                    };
                    return newMessages;
                  });
                }
              } catch (e) {
                console.error("Error parsing SSE JSON", e);
              }
            }
          }
        }
      }
    } catch (error) {
      console.error("Chat error:", error);
      setMessages(prev => [...prev, { role: 'assistant', content: '*Communication error. Janus core unreachable.*' }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-screen max-w-4xl mx-auto px-4 py-8">
      
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-light text-indigo-300">System Core</h2>
        <div className="flex items-center space-x-3">
          <span className={`text-sm ${incognito ? 'text-rose-400' : 'text-gray-500'}`}>
            {incognito ? 'Incognito Active' : 'Incognito Off'}
          </span>
          <button 
            onClick={() => setIncognito(!incognito)}
            className={`w-12 h-6 rounded-full p-1 transition-colors ${incognito ? 'bg-rose-500/20 border border-rose-500/50' : 'bg-gray-800 border border-gray-700'}`}
          >
            <div className={`w-4 h-4 rounded-full transition-transform ${incognito ? 'translate-x-6 bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]' : 'bg-gray-500'}`}></div>
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto mb-8 space-y-6 pr-2 scrollbar-thin">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-2xl p-5 ${msg.role === 'user' ? 'bg-indigo-600/20 border border-indigo-500/30 text-indigo-50' : 'bg-gray-800 border border-gray-700 text-gray-200'}`}>
              {msg.role === 'assistant' && i === 0 ? (
                <AnimatedTextReveal text={msg.content} className="text-lg font-light tracking-wide" />
              ) : (
                <div className="whitespace-pre-wrap leading-relaxed font-light">{msg.content}</div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 border border-gray-700 rounded-2xl p-5 animate-pulse text-gray-500">
              Processing...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="relative">
        <div className="absolute inset-0 bg-gradient-to-t from-gray-900 via-gray-900 to-transparent -top-10 h-10 pointer-events-none"></div>
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
          />
          <Button onClick={handleSend} variant="primary" className="mb-1 mr-1 rounded-xl">
            <svg className="w-5 h-5 transform rotate-90" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
            </svg>
          </Button>
        </div>
      </div>
      
    </div>
  );
};

export default Home;
