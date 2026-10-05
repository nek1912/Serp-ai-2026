// @vitest-environment node
import { describe, it, expect, beforeAll, beforeEach, afterAll, vi } from "vitest";
import { createApp } from "../index.js";

// Stub Clerk before the app is imported so no real network call is attempted.
vi.mock("@clerk/express", () => ({
  clerkMiddleware: () => (_req, _res, next) => next(),
  getAuth: () => ({ getToken: async () => "test-jwt" }),
}));

/**
 * Captured before any spy is installed. The BFF's outbound calls go through
 * globalThis.fetch (mocked per test); the *test client* must keep using the
 * real one, otherwise fetch() is mocked too and every assertion below is
 * satisfied by the mock rather than by the server under test.
 */
const realFetch = globalThis.fetch;

// One server for the whole file. Creating one per test would leak a listening
// handle each time and leave Vitest unable to exit.
let server;
let base;

beforeAll(async () => {
  server = createApp().listen(0);
  await new Promise((r) => server.once("listening", r));
  base = `http://127.0.0.1:${server.address().port}`;
});

beforeEach(() => {
  vi.restoreAllMocks();
});

afterAll(async () => {
  await new Promise((r) => server.close(r));
});

const json = (path, body, init = {}) =>
  realFetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...init,
  });

describe("auth header forwarding", () => {
  it("attaches the Clerk bearer token to the backend call", async () => {
    const upstream = new Response(JSON.stringify({ ok: true }), { status: 200 });
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(upstream);

    const res = await json("/api/grievance/answer", { conversation_id: "c1" });

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    const [, init] = spy.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer test-jwt");
  });
});

describe("grievance handlers", () => {
  it.each([
    ["/api/grievance/answer", "/grievances/answer"],
    ["/api/grievance/clarify", "/grievances/clarify"],
    ["/api/grievance/detect", "/grievances"],
    ["/api/grievance/finalize", "/grievances/finalize"],
  ])("%s proxies to %s", async (route, backendPath) => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));

    const res = await json(route, { conversation_id: "c1" });

    expect(res.status).toBe(200);
    expect(spy.mock.calls[0][0]).toContain(backendPath);
  });

  it("forwards the request body as JSON, not as a stringified object", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));

    await json("/api/grievance/answer", { conversation_id: "c1", field: "farmer_name", value: "Ramesh" });

    const [, init] = spy.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      conversation_id: "c1",
      field: "farmer_name",
      value: "Ramesh",
    });
  });

  it("returns 400 on invalid JSON", async () => {
    const res = await realFetch(`${base}/api/grievance/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{not json",
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Invalid JSON" });
  });

  it("returns 502 when the backend responds non-ok", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 500 }));
    const res = await json("/api/grievance/answer", { conversation_id: "c1" });
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("grievance_backend_error");
  });

  it("returns 503 when the backend is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));
    const res = await json("/api/grievance/answer", { conversation_id: "c1" });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("grievance_backend_unavailable");
  });
});

describe("GET /api/grievance/fields", () => {
  it("requires conversation_id", async () => {
    const res = await realFetch(`${base}/api/grievance/fields`);
    expect(res.status).toBe(400);
  });

  it("passes a 404 through as 404", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    const res = await realFetch(`${base}/api/grievance/fields?conversation_id=abc`);
    expect(res.status).toBe(404);
  });

  it("maps other upstream errors to 502", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 500 }));
    const res = await realFetch(`${base}/api/grievance/fields?conversation_id=abc`);
    expect(res.status).toBe(502);
  });

  it("returns 503 when the backend is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    const res = await realFetch(`${base}/api/grievance/fields?conversation_id=abc`);
    expect(res.status).toBe(503);
  });

  it("defaults language to en", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ fields: [] }), { status: 200 }));
    await realFetch(`${base}/api/grievance/fields?conversation_id=abc`);
    expect(spy.mock.calls[0][0]).toContain("language=en");
  });
});

describe("POST /api/translate", () => {
  it("short-circuits on an empty texts array", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    const res = await json("/api/translate", { texts: [] });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ translations: [] });
    expect(spy).not.toHaveBeenCalled();
  });

  it("falls back to the source texts when the backend is down", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    const res = await json("/api/translate", { texts: ["hello"] });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ translations: ["hello"] });
  });

  it("does not send an Authorization header", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ translations: ["x"] }), { status: 200 }));
    await json("/api/translate", { texts: ["hello"] });
    const [, init] = spy.mock.calls[0];
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("ignores the `to` field and falls back to target_language=hi", async () => {
    // Pre-existing defect, ported as-is from the Next handler: ChatWindow.tsx
    // and translator.ts send { texts, to }, the handler only reads
    // target_language. Documented so it is not mistaken for a porting bug.
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ translations: ["x"] }), { status: 200 }));
    await json("/api/translate", { texts: ["hello"], to: "ta" });
    const [, init] = spy.mock.calls[0];
    expect(JSON.parse(init.body).target_language).toBe("hi");
  });
});

describe("SSE chat streaming", () => {
  it("streams chunks through with the SSE content type", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode("data: a\n\n"));
        controller.enqueue(encoder.encode("data: b\n\n"));
        controller.close();
      },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    const res = await json("/api/chat", { question: "hi" });

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(await res.text()).toContain("data: a");
  });

  it("forwards the question as a JSON body to the streaming backend", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.close();
      },
    });
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(body, { status: 200 }));

    await json("/api/chat/stream", { question: "hi", session_id: "s1" });

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("/chat/stream");
    expect(JSON.parse(init.body)).toEqual({ question: "hi", session_id: "s1" });
    expect(init.headers.Authorization).toBe("Bearer test-jwt");
  });

  it("returns 400 on invalid JSON", async () => {
    const res = await realFetch(`${base}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{not json",
    });
    expect(res.status).toBe(400);
  });

  it("returns 502 when the backend rejects the stream", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 500 }));
    const res = await json("/api/chat/stream", { question: "hi" });
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("retrieval_backend_error");
  });

  it("returns 503 when the backend is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    const res = await json("/api/chat", { question: "hi" });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("retrieval_backend_unavailable");
  });
});

describe("GET /api/documents/pdf/:filename", () => {
  it("url-encodes the filename and forwards the content type", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(Buffer.from("%PDF-1.4"), {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    );

    const res = await realFetch(`${base}/api/documents/pdf/pmfby%20guide.pdf`);

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("cache-control")).toBe("public, max-age=86400");
    expect(spy.mock.calls[0][0]).toContain("pmfby%20guide.pdf");
  });

  it("passes an upstream failure status through with Document not found", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    const res = await realFetch(`${base}/api/documents/pdf/missing.pdf`);
    expect(res.status).toBe(404);
    expect((await res.json()).error).toBe("Document not found");
  });

  it("returns 503 when unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    const res = await realFetch(`${base}/api/documents/pdf/x.pdf`);
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("Document service unavailable");
  });
});

describe("POST /api/speak", () => {
  const multipart = (fields, boundary = "----b") => {
    const parts = Object.entries(fields).map(
      ([name, value]) =>
        `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`,
    );
    return Buffer.from(`${parts.join("")}--${boundary}--\r\n`, "utf8");
  };

  it("decodes multipart text as UTF-8 and defaults language to hi", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ audio: "494433" }), { status: 200 }));

    const res = await realFetch(`${base}/api/speak`, {
      method: "POST",
      headers: { "Content-Type": "multipart/form-data; boundary=----b" },
      body: multipart({ text: "नमस्ते दुनिया", language: "hi" }),
    });

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("audio/mpeg");
    expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({
      text: "नमस्ते दुनिया",
      language: "hi",
    });
  });

  it("defaults language to hi when the field is absent", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ audio: "494433" }), { status: 200 }));

    await realFetch(`${base}/api/speak`, {
      method: "POST",
      headers: { "Content-Type": "multipart/form-data; boundary=----b" },
      body: multipart({ text: "hello" }),
    });

    expect(JSON.parse(spy.mock.calls[0][1].body).language).toBe("hi");
  });

  it("returns 400 when text is missing", async () => {
    const res = await realFetch(`${base}/api/speak`, {
      method: "POST",
      headers: { "Content-Type": "multipart/form-data; boundary=----b" },
      body: multipart({ language: "hi" }),
    });
    expect(res.status).toBe(400);
  });

  it("returns an empty audio/mpeg 503 when the backend is down", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    const res = await realFetch(`${base}/api/speak`, {
      method: "POST",
      headers: { "Content-Type": "multipart/form-data; boundary=----b" },
      body: multipart({ text: "hello" }),
    });
    expect(res.status).toBe(503);
    expect(res.headers.get("content-type")).toBe("audio/mpeg");
    expect((await res.arrayBuffer()).byteLength).toBe(0);
  });
});

describe("POST /api/voice/speak", () => {
  it("returns the hex audio and the upstream language", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ audio: "494433", language: "ta" }), { status: 200 }),
    );
    const res = await json("/api/voice/speak", {
      segments: [{ text: "வணக்கம்", language: "ta" }],
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ audio: "494433", language: "ta" });
  });

  it("falls back to the first segment's language", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ audio: "494433" }), { status: 200 }),
    );
    const res = await json("/api/voice/speak", {
      segments: [{ text: "வணக்கம்", language: "ta" }],
    });
    expect((await res.json()).language).toBe("ta");
  });

  it("returns 400 for empty segments", async () => {
    const res = await json("/api/voice/speak", { segments: [] });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("Missing segments");
  });

  it("returns 503 when the backend is down", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    const res = await json("/api/voice/speak", {
      segments: [{ text: "hi", language: "hi" }],
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("Backend unavailable");
  });
});

describe("routing", () => {
  it("mounts all 11 endpoints", () => {
    const routes = createApp().router.stack
      .filter((l) => l.route)
      .map((l) => `${Object.keys(l.route.methods).join(",")} ${l.route.path}`);
    expect(routes).toEqual([
      "post /api/chat",
      "post /api/chat/stream",
      "get /api/documents/pdf/:filename",
      "post /api/grievance/answer",
      "post /api/grievance/clarify",
      "post /api/grievance/detect",
      "post /api/grievance/finalize",
      "get /api/grievance/fields",
      "post /api/speak",
      "post /api/voice/speak",
      "post /api/translate",
    ]);
  });

  it("404s unknown /api paths instead of serving the SPA shell", async () => {
    const res = await realFetch(`${base}/api/nope`);
    expect(res.status).toBe(404);
  });
});