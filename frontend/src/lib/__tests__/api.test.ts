import { test, expect, vi, afterEach } from "vitest";
import { sendChat, fetchVoiceSpeak, type SpeechSegment } from "../api";

type FetchMock = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<{ ok: boolean; json: () => Promise<unknown> }>;

afterEach(() => {
  vi.unstubAllGlobals();
});

test("fetchVoiceSpeak posts segments to /api/voice/speak and returns audio hex", async () => {
  const segments: SpeechSegment[] = [{ text: "hello", language: "en" }];
  const fetchMock = vi.fn<FetchMock>(async () => ({
    ok: true,
    json: async () => ({ audio: "48656c6c6f", language: "en" }),
  }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await fetchVoiceSpeak(segments);

  expect(fetchMock).toHaveBeenCalledTimes(1);
  const call = fetchMock.mock.calls[0];
  const [url, init] = call as [RequestInfo | URL, RequestInit];
  expect(url).toBe("/api/voice/speak");
  expect(init.method).toBe("POST");
  const body = JSON.parse(init.body as string);
  expect(body.segments).toEqual(segments);
  expect(result).toEqual({ audio: "48656c6c6f", language: "en" });
});

test("sendChat serializes ui_language_explicit when provided", async () => {
  const fetchMock = vi.fn<FetchMock>(async () => ({
    ok: true,
    json: async () => ({
      answer: "a",
      language: "hi",
      domain: "d",
      intent: "i",
      entities: [],
      confidence: 0.5,
      confidence_level: "moderate",
      citations: [],
      abstained: false,
      follow_up_question: null,
    }),
  }));
  vi.stubGlobal("fetch", fetchMock);

  await sendChat({
    question: "q",
    session_id: "s",
    language: "hi",
    state: null,
    ui_language_explicit: true,
  });

  const call = fetchMock.mock.calls[0] as [RequestInfo | URL, RequestInit];
  const body = JSON.parse(call[1].body as string);
  expect(body.ui_language_explicit).toBe(true);

  fetchMock.mockClear();
  await sendChat({
    question: "q",
    session_id: "s",
    language: "hi",
    state: null,
  });
  const call2 = fetchMock.mock.calls[0] as [RequestInfo | URL, RequestInit];
  const body2 = JSON.parse(call2[1].body as string);
  expect(body2.ui_language_explicit).toBeUndefined();
});
