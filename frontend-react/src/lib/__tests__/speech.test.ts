import { describe, it, expect, vi, beforeEach } from "vitest";
import * as api from "../api";
import { partitionRuns, hasVoice, pickVoice, speakSegments, createSpeechService } from "../speech";

// Minimal WAV hex (doesn't need to be valid audio for the test; we stub Audio).
const FAKE_HEX = "000000";

beforeEach(() => {
  vi.resetAllMocks();
  // Stub Audio so play() resolves and onended fires, letting playAzure settle.
  class StubAudio {
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(_src?: string) {
      void _src;
    }
    play() {
      Promise.resolve().then(() => this.onended?.());
      return Promise.resolve();
    }
  }
  (globalThis as unknown as { Audio: typeof StubAudio }).Audio = StubAudio;
});

describe("partitionRuns", () => {
  it("merges contiguous same-language segments", () => {
    const out = partitionRuns([
      { text: "Hello ", language: "en" },
      { text: "world", language: "en" },
      { text: "नमस्ते", language: "hi" },
    ]);
    expect(out).toEqual([
      { text: "Hello world", language: "en" },
      { text: "नमस्ते", language: "hi" },
    ]);
  });

  it("returns empty array for empty input", () => {
    expect(partitionRuns([])).toEqual([]);
  });
});

describe("hasVoice / pickVoice", () => {
  it("returns false/undefined when no speechSynthesis (jsdom/node)", () => {
    expect(hasVoice("en")).toBe(false);
    expect(pickVoice("hi")).toBeUndefined();
  });
});

describe("speakSegments", () => {
  it("sends a single run to the backend and resolves", async () => {
    const spy = vi
      .spyOn(api, "fetchVoiceSpeak")
      .mockResolvedValue({ audio: FAKE_HEX, language: "hi" });

    await speakSegments([{ text: "नमस्ते", language: "hi" }]);

    expect(spy).toHaveBeenCalledWith([{ text: "नमस्ते", language: "hi" }]);
  });

  it("batches every language run into a single backend call", async () => {
    const spy = vi
      .spyOn(api, "fetchVoiceSpeak")
      .mockResolvedValue({ audio: FAKE_HEX, language: "en" });

    await speakSegments([
      { text: "Hello ", language: "en" },
      { text: "world", language: "en" },
      { text: "नमस्ते", language: "hi" },
    ]);

    /*
     * The whole answer is one request. partitionRuns merges the two English
     * segments into a single run, and the backend's /voice/speak accepts a
     * `segments` array and handles segmentation itself -- one network round
     * trip per utterance, not one per language run. The previous expectation of
     * two calls encoded an older per-run design that the code no longer uses.
     */
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith([
      { text: "Hello world", language: "en" },
      { text: "नमस्ते", language: "hi" },
    ]);
  });
});

describe("speakSegments cancellation", () => {
  // Stub Audio whose play() resolves but whose onended NEVER fires — this
  // reproduces the dangling-promise bug where pause() does not settle the
  // awaiting playAzure promise.
  beforeEach(() => {
    vi.resetAllMocks();
    class NonEndingAudio {
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(_src?: string) {
        void _src;
      }
      play() {
        return Promise.resolve();
      }
    }
    (globalThis as unknown as { Audio: typeof NonEndingAudio }).Audio =
      NonEndingAudio;
  });

  it("settles the in-flight Azure promise when a newer call cancels it", async () => {
    vi.spyOn(api, "fetchVoiceSpeak").mockResolvedValue({
      audio: FAKE_HEX,
      language: "hi",
    });

    const first = speakSegments([{ text: "नमस्ते", language: "hi" }]);
    // Second call cancels the first via stopAllPlayback().
    speakSegments([{ text: "अलविदा", language: "hi" }]);

    await expect(first).resolves.toBeUndefined();
  });

  it("settles the in-flight Azure promise when stopSpeaking is called", async () => {
    vi.spyOn(api, "fetchVoiceSpeak").mockResolvedValue({
      audio: FAKE_HEX,
      language: "hi",
    });

    const first = speakSegments([{ text: "नमस्ते", language: "hi" }]);
    createSpeechService().stopSpeaking();

    await expect(first).resolves.toBeUndefined();
  });
});
