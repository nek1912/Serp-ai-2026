"use client";

import { useState, useEffect } from "react";

const THINKING_WORDS: Record<string, string[]> = {
  en: ["Thinking", "Searching", "Analyzing", "Processing"],
  hi: ["सोच रहे हैं", "खोज रहे हैं", "विश्लेषण कर रहे हैं", "प्रसंस्करण"],
  gu: ["વિચારી રહ્યા છીએ", "શોધી રહ્યા છીએ", "વિશ્લેષણ કરી રહ્યા છીએ", "પ્રક્રિયા કરી રહ્યા છીએ"],
};

export function ThinkingBubble({ thinkingText, lang = "en" }: { thinkingText: string; lang?: string }) {
  const [dotCount, setDotCount] = useState(0);
  const [wordIndex, setWordIndex] = useState(0);
  const words = THINKING_WORDS[lang] || THINKING_WORDS.en;

  useEffect(() => {
    const dotTimer = setInterval(() => {
      setDotCount((prev) => (prev + 1) % 4);
    }, 400);
    return () => clearInterval(dotTimer);
  }, []);

  useEffect(() => {
    const wordTimer = setInterval(() => {
      setWordIndex((prev) => (prev + 1) % words.length);
    }, 2000);
    return () => clearInterval(wordTimer);
  }, [words.length]);

  return (
    <div className="flex gap-3">
      <div className="flex flex-col gap-1">
        <div className="rounded-2xl bg-[var(--surface-card)] px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--body)]">
              {thinkingText || words[wordIndex]}
            </span>
            <span className="flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className={`inline-block h-1.5 w-1.5 rounded-full bg-[var(--muted-soft)] transition-opacity duration-300 ${
                    i <= dotCount - 1 ? "opacity-100" : "opacity-30"
                  }`}
                  style={{
                    animation: `pulse 1.4s ease-in-out ${i * 0.2}s infinite`,
                  }}
                />
              ))}
            </span>
          </div>
        </div>
      </div>
      <style jsx>{`
        @keyframes pulse {
          0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </div>
  );
}
