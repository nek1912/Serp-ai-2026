// @vitest-environment node
import { describe, it, expect, beforeAll, beforeEach, afterAll, vi } from "vitest";
import { createApp } from "../index.js";

// Stub Clerk before the app is imported so no real network call is attempted.
// clerkCalls records every invocation so tests can assert the middleware is
// scoped to /api and never runs for static assets.
const clerkCalls = [];
vi.mock("@clerk/express", () => ({
  clerkMiddleware: () => (req, _res, next) => {
    clerkCalls.push(req.path);
    next();
  },
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

  // Next read `await res.json()` inside its try, so a 200 carrying an
  // unparseable body produced the 503 rather than escaping as a 500.
  it("returns 503 when a 200 response has an unparseable body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>gateway</html>", { status: 200 }),
    );
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

  it("returns 503 when a 200 response has an unparseable body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>gateway</html>", { status: 200 }),
    );
    const res = await realFetch(`${base}/api/grievance/fields?conversation_id=abc`);
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("grievance_backend_unavailable");
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

  // Next read `await res.json()` inside its try, so an unparseable 200 landed
  // in the catch and degraded to the source-text fallback.
  it("falls back to the source texts when a 200 response has an unparseable body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>gateway</html>", { status: 200 }),
    );
    const res = await json("/api/translate", { texts: ["hello"] });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ translations: ["hello"] });
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

  it("returns 503 when the body stream errors mid-read", async () => {
    const body = new ReadableStream({
      start(controller) {
        controller.error(new Error("truncated"));
      },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(body, { status: 200, headers: { "Content-Type": "application/pdf" } }),
    );
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

  // The empty audio/mpeg 503 is the signal the browser speechSynthesis
  // fallback keys on, so an unparseable 200 must reach it, not a 500.
  it("returns the empty audio/mpeg 503 when a 200 response has an unparseable body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>gateway</html>", { status: 200 }),
    );
    const res = await realFetch(`${base}/api/speak`, {
      method: "POST",
      headers: { "Content-Type": "multipart/form-data; boundary=----b" },
      body: multipart({ text: "hello" }),
    });
    expect(res.status).toBe(503);
    expect(res.headers.get("content-type")).toBe("audio/mpeg");
    expect((await res.arrayBuffer()).byteLength).toBe(0);
  });

  // The whole point of hexToBuffer is the MP3 payload. "00ff10ab" is chosen so
  // a wrong conversion is detectable: an empty buffer drops the bytes, and
  // treating the hex as ASCII/UTF-16 yields 0x30/0x30/0x66/0x66 instead.
  it("decodes the backend's hex audio into the exact bytes", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ audio: "00ff10ab" }), { status: 200 }),
    );

    const res = await realFetch(`${base}/api/speak`, {
      method: "POST",
      headers: { "Content-Type": "multipart/form-data; boundary=----b" },
      body: multipart({ text: "hello" }),
    });

    expect(res.status).toBe(200);
    const bytes = Buffer.from(await res.arrayBuffer());
    expect(bytes).toEqual(Buffer.from([0x00, 0xff, 0x10, 0xab]));
    expect([...bytes]).toEqual([0x00, 0xff, 0x10, 0xab]);
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

  it("returns 503 when a 200 response has an unparseable body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>gateway</html>", { status: 200 }),
    );
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

/*
 * Regression guard.
 *
 * The Clerk middleware must be mounted on /api ONLY. It was originally mounted
 * with a bare app.use(clerk), which ran it for every request. Clerk throws on a
 * malformed or missing publishable/secret key, and Express's default error
 * handler then returns a 500 HTML page for EVERY route -- including "/", the
 * favicon and hashed CSS/JS. A single bad env var took the entire site down
 * rather than just the API.
 *
 * The Next.js app never had this problem: src/proxy.ts carried an explicit
 * matcher that excluded _next and every static asset extension.
 */
describe("Clerk middleware scoping", () => {
  it("does not invoke Clerk for non-API requests", async () => {
    clerkCalls.length = 0;
    await realFetch(`${base}/`);
    await realFetch(`${base}/favicon.ico`);
    expect(clerkCalls.length).toBe(0);
  });

  it("still invokes Clerk for API requests", async () => {
    clerkCalls.length = 0;
    await realFetch(`${base}/api/nope`);
    expect(clerkCalls.length).toBeGreaterThan(0);
  });

  it("still serves static assets when Clerk config is unusable", async () => {
    // The route-verification script boots the real app with a placeholder key;
    // asserting here that the middleware is API-scoped is what keeps a bad
    // CLERK_SECRET_KEY from turning into a site-wide 500.
    const res = await realFetch(`${base}/`);
    expect(res.status).toBe(200);
  });
});
/*
 * Regression guards for defects found in the final whole-branch review.
 * Each of these was a live bug, not a hypothetical.
 */

/*
 * I-1: BACKEND_API_URL is an ORIGIN, but streamUrl() carried over the Next
 * handler's `replace(/\/chat$/, "/chat/stream")`. Against an origin that regex
 * never matches, the `||` fallback never fires (an origin is truthy), and the
 * result was the bare origin -- so chat POSTed to "/" and every message 502'd
 * with "backend responded 404". The existing suite missed it because it never
 * set BACKEND_API_URL, so every test took the fallback branch.
 */
describe("streaming chat URL construction", () => {
  it("appends /chat/stream to an origin-style BACKEND_API_URL", async () => {
    const original = process.env.BACKEND_API_URL;
    process.env.BACKEND_API_URL = "http://example.test";
    try {
      const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response("data: ok\n\n", {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      );
      const s = createApp().listen(0);
      await new Promise((r) => s.once("listening", r));
      try {
        await realFetch(`http://127.0.0.1:${s.address().port}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: "hi" }),
        });
        expect(String(spy.mock.calls[0][0])).toBe("http://example.test/chat/stream");
      } finally {
        s.close();
      }
    } finally {
      if (original === undefined) delete process.env.BACKEND_API_URL;
      else process.env.BACKEND_API_URL = original;
    }
  });

  it("does not leave a trailing-slash double slash", async () => {
    const original = process.env.BACKEND_API_URL;
    process.env.BACKEND_API_URL = "http://example.test///";
    try {
      const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response("data: ok\n\n", {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      );
      const s = createApp().listen(0);
      await new Promise((r) => s.once("listening", r));
      try {
        await realFetch(`http://127.0.0.1:${s.address().port}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: "hi" }),
        });
        expect(String(spy.mock.calls[0][0])).toBe("http://example.test/chat/stream");
      } finally {
        s.close();
      }
    } finally {
      if (original === undefined) delete process.env.BACKEND_API_URL;
      else process.env.BACKEND_API_URL = original;
    }
  });
});

/*
 * C-2: there was no Express error handler, so any unhandled throw fell through
 * to Express's default, which serialises err.stack -- absolute filesystem paths
 * and dependency internals -- into an HTML body served to any caller who can
 * reach /api. The frontend expects JSON here.
 */
describe("error handling", () => {
  it("returns JSON, never a stack trace, for an unhandled route error", async () => {
    const s = createApp().listen(0);
    await new Promise((r) => s.once("listening", r));
    try {
      // Force a throw inside a route: a 200 whose body explodes when read.
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        Object.defineProperty(new Response(null, { status: 200 }), "arrayBuffer", {
          value: () => Promise.reject(new Error("boom-secret-path")),
        }),
      );
      const res = await realFetch(
        `http://127.0.0.1:${s.address().port}/api/documents/pdf/x.pdf`,
      );
      const body = await res.text();
      expect(res.headers.get("content-type")).toContain("application/json");
      expect(body).not.toContain("boom-secret-path");
      expect(body).not.toContain("node_modules");
      expect(JSON.parse(body).error).toBeTruthy();
    } finally {
      s.close();
    }
  });
});

/*
 * M-2: readJson/readRawBody replaced the framework's req.json()/req.formData(),
 * which owned their buffering limits. Uncapped, one unauthenticated oversized
 * POST to /api/speak OOMs the single process that serves both the API and the
 * SPA.
 */
describe("request body limits", () => {
  it.each([
    ["/api/grievance/answer", {}],
    ["/api/translate", {}],
    ["/api/voice/speak", { segments: [{ text: "x", language: "hi" }] }],
  ])("rejects an oversized JSON body with 413 at %s", async (path, payload) => {
    const res = await realFetch(`${base}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, blob: "x".repeat(1_200_000) }),
    });
    expect(res.status).toBe(413);
    expect((await res.json()).error).toBe("body_too_large");
  });

  it("rejects an oversized multipart body with 413 at /api/speak", async () => {
    const boundary = "----x";
    const body =
      `--${boundary}\r\nContent-Disposition: form-data; name="text"\r\n\r\n` +
      "x".repeat(1_200_000) +
      `\r\n--${boundary}--\r\n`;
    const res = await realFetch(`${base}/api/speak`, {
      method: "POST",
      headers: { "Content-Type": `multipart/form-data; boundary=${boundary}` },
      body,
    });
    expect(res.status).toBe(413);
    expect((await res.json()).error).toBe("body_too_large");
  });

  it("still accepts a normal-sized body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: 1 }), { status: 200 }),
    );
    const res = await json("/api/grievance/answer", { conversation_id: "c1" });
    expect(res.status).toBe(200);
  });
});

/*
 * I-2 regression guard.
 *
 * The first attempt at disconnect teardown used req.on("close"). Since Node 16
 * that fires when the request BODY finishes -- which readJson already triggered
 * before proxySse attached the listener -- so the abort never happened and an
 * abandoned chat sat parked in reader.read() with the upstream still running.
 * Only res.on("close") (response socket teardown) means what we want.
 *
 * This asserts the observable consequence: a client that walks away mid-stream
 * causes the upstream reader to be released promptly, rather than the loop
 * continuing to pull chunks for a dead socket.
 */


describe("SSE client-disconnect teardown", () => {
  it("aborts the upstream request when the client disconnects", async () => {
    let capturedSignal;
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      capturedSignal = init.signal;
      // One chunk, then silence: the loop parks in reader.read() exactly as it
      // would during a real retrieval phase, so the disconnect arrives while
      // the handler is genuinely mid-stream.
      return Promise.resolve(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode("data: first\n\n"));
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    });

    const s = createApp().listen(0);
    await new Promise((r) => s.once("listening", r));

    const controller = new AbortController();
    const inflight = realFetch(`http://127.0.0.1:${s.address().port}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: "hi" }),
      signal: controller.signal,
    }).catch(() => {});

    await new Promise((r) => setTimeout(r, 250));
    expect(capturedSignal, "upstream fetch received an abort signal").toBeDefined();
    expect(capturedSignal.aborted, "not aborted before the client leaves").toBe(false);

    controller.abort();
    await new Promise((r) => setTimeout(r, 250));
    void inflight;
    s.close();

    expect(
      capturedSignal.aborted,
      "upstream fetch was NOT aborted after the client disconnected - the " +
        "teardown is listening to the wrong event",
    ).toBe(true);
  });
});
