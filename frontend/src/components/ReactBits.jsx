import React, { useState, useEffect } from 'react';

export const AnimatedTextReveal = ({ text, className = "" }) => {
  const [revealed, setRevealed] = useState(false);
  
  useEffect(() => {
    // Reset animation on text change
    setRevealed(false);
    const timer = setTimeout(() => setRevealed(true), 50);
    return () => clearTimeout(timer);
  }, [text]);

  if (!revealed) return <span className="opacity-0">{text}</span>;

  return (
    <div className={`animate-reveal ${className}`}>
      {text}
    </div>
  );
};

export const GlowingCard = ({ children, active, className = "" }) => {
  return (
    <div className={`relative rounded-xl bg-gray-800 border transition-all duration-300 ${active ? 'glowing-border' : 'border-gray-700 hover:border-gray-600'} ${className}`}>
      <div className="p-5 h-full w-full">
        {children}
      </div>
    </div>
  );
};

export const AnimatedBackground = ({ children, className = "" }) => {
  return (
    <div className={`animated-bg w-full h-full min-h-screen ${className}`}>
      {children}
    </div>
  );
};
