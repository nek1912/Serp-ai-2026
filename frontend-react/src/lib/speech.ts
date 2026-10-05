import { fetchVoiceSpeak, type SpeechSegment } from "./api";

export type { SpeechSegment };

export interface SpeechService {
  supported: boolean;
  listen: (locale: string, onTranscript: (text: string) => void) => () => void;
  speak: (text: string, locale: string) => Promise<void>;
  stopSpeaking: () => void;
}

let currentAudio: HTMLAudioElement | null = null;
let currentObjectUrl: string | null = null;

function stopAll() {
  if (currentAudio) {
    try {
      currentAudio.pause();
    } catch {
      /* noop */
    }
    currentAudio = null;
  }

  if (currentObjectUrl) {
    try {
      URL.revokeObjectURL(currentObjectUrl);
    } catch {
      /* noop */
    }
    currentObjectUrl = null;
  }

  if (typeof window !== "undefined" && window.speechSynthesis) {
    try {
      window.speechSynthesis.cancel();
    } catch {
      /* noop */
    }
  }
}

// Monotonic token for cancelling in-flight playback when a newer request starts
// or the user stops. Incrementing this invalidates any pending utterance/audio.
let _speakToken = 0;
let _activeAudio: HTMLAudioElement | null = null;
let _activeAudioResolve: (() => void) | null = null;

function stopAllPlayback(): void {
  stopAll();
  _speakToken++;

  if (_activeAudio) {
    try {
      _activeAudio.pause();
    } catch {
      /* noop */
    }
    _activeAudio = null;
  }

  if (_activeAudioResolve) {
    const resolve = _activeAudioResolve;
    _activeAudioResolve = null;
    resolve();
  }
}

export function hasVoice(lang: string): boolean {
  if (typeof window === "undefined" || !window.speechSynthesis) return false;

  const voices = window.speechSynthesis.getVoices();
  const prefix = lang === "en" ? "en" : lang;

  return voices.some((v) => v.lang.startsWith(prefix));
}

export function pickVoice(lang: string): SpeechSynthesisVoice | undefined {
  if (typeof window === "undefined" || !window.speechSynthesis) return undefined;

  const voices = window.speechSynthesis.getVoices();
  const prefix = lang === "en" ? "en" : lang;

  return voices.find((v) => v.lang.startsWith(prefix));
}

export function partitionRuns(segments: SpeechSegment[]): SpeechSegment[] {
  const runs: SpeechSegment[] = [];

  for (const seg of segments) {
    const last = runs[runs.length - 1];

    if (last && last.language === seg.language) {
      const sep =
        last.text.endsWith(" ") || seg.text.startsWith(" ") ? "" : " ";
      last.text += sep + seg.text;
    } else {
      runs.push({
        language: seg.language,
        text: seg.text,
      });
    }
  }

  return runs;
}

function hexToBase64(hex: string): string {
  let bin = "";

  for (let i = 0; i < hex.length; i += 2) {
    bin += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
  }

  return btoa(bin);
}

function playBackendAudio(
  hex: string,
  token: number,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (token !== _speakToken) {
      resolve();
      return;
    }

    let audio: HTMLAudioElement;

    try {
      audio = new Audio(`data:audio/wav;base64,${hexToBase64(hex)}`);
    } catch (error) {
      reject(error);
      return;
    }

    _activeAudio = audio;
    _activeAudioResolve = resolve;

    const cleanup = () => {
      audio.onended = null;
      audio.onerror = null;

      if (_activeAudio === audio) {
        _activeAudio = null;
      }

      if (_activeAudioResolve === resolve) {
        _activeAudioResolve = null;
      }
    };

    audio.onended = () => {
      cleanup();
      resolve();
    };

    audio.onerror = () => {
      cleanup();
      reject(new Error("Backend TTS audio playback failed"));
    };

    audio.play().catch((error) => {
      cleanup();
      reject(error);
    });
  });
}

async function speakBackendSegments(
  segments: SpeechSegment[],
): Promise<void> {
  const response = await fetchVoiceSpeak(segments);

  if (!response.audio) {
    throw new Error("Backend TTS returned empty audio");
  }

  const token = _speakToken;

  await playBackendAudio(response.audio, token);
}

export async function speakSegments(
  segments: SpeechSegment[],
  opts?: { onToken?: (token: number) => void },
): Promise<void> {
  stopAllPlayback();

  const token = ++_speakToken;
  opts?.onToken?.(token);

  const runs = partitionRuns(segments || []);

  if (runs.length === 0) return;

  /*
   * IMPORTANT:
   *
   * Backend TTS is intentionally the PRIMARY path.
   *
   * /api/voice/speak -> /voice/speak -> VoiceService
   * -> Sarvam TTS -> Azure fallback
   *
   * Browser SpeechSynthesis must NOT be selected merely because Chrome
   * has a local voice available. Doing that would bypass Sarvam entirely.
   */
  try {
    if (token !== _speakToken) return;

    await speakBackendSegments(runs);

    if (token !== _speakToken) return;

    return;
  } catch {
    /*
     * Backend unavailable / failed.
     *
     * At this point the backend has already attempted:
     * Sarvam -> Azure.
     *
     * Browser TTS is therefore the final emergency fallback only.
     */
  }

  for (const run of runs) {
    if (token !== _speakToken) return;

    const voice = pickVoice(run.language);

    if (!voice) continue;

    await playBrowser(run.text, voice, token);

    if (token !== _speakToken) return;
  }
}

function playBrowser(
  text: string,
  voice: SpeechSynthesisVoice,
  token: number,
): Promise<void> {
  return new Promise((resolve) => {
    if (
      token !== _speakToken ||
      typeof window === "undefined" ||
      !window.speechSynthesis
    ) {
      resolve();
      return;
    }

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang;

    const done = () => {
      utterance.onend = null;
      utterance.onerror = null;
      resolve();
    };

    utterance.onend = done;
    utterance.onerror = done;

    try {
      window.speechSynthesis.speak(utterance);
    } catch {
      resolve();
    }
  });
}

export function createSpeechService(): SpeechService {
  const isBrowser = typeof window !== "undefined";

  const SpeechRecognition =
    isBrowser
      ? (window.SpeechRecognition || window.webkitSpeechRecognition)
      : undefined;

  return {
    get supported() {
      if (!isBrowser) return false;

      const sr =
        window.SpeechRecognition ||
        window.webkitSpeechRecognition;

      return Boolean(sr) && Boolean(window.speechSynthesis);
    },

    listen(locale, onTranscript) {
      if (!SpeechRecognition) return () => {};

      const rec = new SpeechRecognition();

      rec.lang = locale === "en" ? "en-IN" : locale + "-IN";
      rec.interimResults = false;
      rec.maxAlternatives = 1;

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      rec.onresult = (e: any) => {
        const text = e.results?.[0]?.[0]?.transcript;

        if (text) {
          onTranscript(text);
        }
      };

      rec.onerror = () => {};

      rec.start();

      return () => {
        try {
          rec.stop();
        } catch {
          /* noop */
        }
      };
    },

    async speak(text, locale) {
      stopAllPlayback();

      const cleanText = text
        .replace(/\[chunk:[a-f0-9]+\]/g, "")
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/\n+/g, ". ")
        .trim();

      if (!cleanText) return;

      /*
       * Normal text follows the same priority:
       *
       * Backend -> Sarvam -> Azure -> browser TTS
       */
      try {
        await speakBackend(cleanText, locale);
        return;
      } catch {
        // Final fallback: browser SpeechSynthesis.
      }

      await speakBrowser(cleanText, locale);
    },

    stopSpeaking() {
      stopAllPlayback();
    },
  };
}

function speakBackend(
  text: string,
  locale: string,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();

    formData.append("text", text);
    formData.append("language", locale);

    fetch("/api/speak", {
      method: "POST",
      body: formData,
    })
      .then((res) => {
        if (!res.ok) {
          throw new Error(`Speak API ${res.status}`);
        }

        return res.arrayBuffer();
      })
      .then((buffer) => {
        if (buffer.byteLength < 100) {
          throw new Error("Audio too small");
        }

        const blob = new Blob([buffer], {
          type: "audio/mpeg",
        });

        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);

        currentAudio = audio;
        currentObjectUrl = url;

        audio.onended = () => {
          stopAll();
          resolve();
        };

        audio.onerror = () => {
          stopAll();
          reject(new Error("Audio playback failed"));
        };

        audio.play().catch((error) => {
          stopAll();
          reject(error);
        });
      })
      .catch(reject);
  });
}

function speakBrowser(
  text: string,
  locale: string,
): Promise<void> {
  return new Promise((resolve) => {
    const synthesis = window.speechSynthesis;

    if (!synthesis) {
      resolve();
      return;
    }

    const langTags: Record<string, string> = {
      en: "en-IN",
      hi: "hi-IN",
      gu: "gu-IN",
      mr: "mr-IN",
      bn: "bn-IN",
    };

    const targetLang = langTags[locale] || locale;

    const doSpeak = () => {
      const utterance = new SpeechSynthesisUtterance(text);

      utterance.lang = targetLang;
      utterance.rate = 1;
      utterance.pitch = 1;

      const voices = synthesis.getVoices();

      const voice =
        voices.find((v) => v.lang === targetLang) ||
        voices.find((v) => v.lang.startsWith(locale)) ||
        voices.find((v) => v.lang.startsWith("en"));

      if (voice) {
        utterance.voice = voice;
        utterance.lang = voice.lang;
      }

      utterance.onend = () => resolve();
      utterance.onerror = () => resolve();

      synthesis.cancel();

      setTimeout(() => {
        synthesis.speak(utterance);
      }, 50);
    };

    if (synthesis.getVoices().length === 0) {
      const onLoaded = () => {
        synthesis.removeEventListener("voiceschanged", onLoaded);
        doSpeak();
      };

      synthesis.addEventListener("voiceschanged", onLoaded);
    } else {
      doSpeak();
    }
  });
}