import express from "express";
import path from "node:path";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

import { clerk } from "./clerk.js";
import {
  BACKEND,
  authHeader,
  proxyFetch,
  proxyJson,
  readJson,
  hexToBuffer,
  readUpstreamJson,
  readUpstreamBuffer,
  BODY_UNREADABLE,
} from "./proxy.js";
import { proxySse } from "./sse.js";

/**
 * Both /api/chat and /api/chat/stream resolve to the streaming endpoint.
 *
 * Ported from the Next handler's
 *   process.env.BACKEND_API_URL?.replace(/\/chat$/, "/chat/stream")
 *     || "http://localhost:8000/chat/stream"
 * so the returned value is an absolute URL: proxySse passes it straight to
 * fetch() rather than re-prefixing BACKEND(), which would otherwise yield
 * "http://localhost:8000http://localhost:8000".
 */
const streamUrl = () =>
  process.env.BACKEND_API_URL?.replace(/\/+$/, "").replace(/\/chat$/, "/chat/stream") ||
  `${BACKEND()}/chat/stream`;

export function createApp() {
  const app = express();

  app.use(clerk);

  // ── Chat (SSE) ────────────────────────────────────────────────────────
  const chatHandler = async (req, res) => {
    let body;
    try {
      body = await readJson(req);
    } catch {
      return res
        .status(400)
        .json({ error: "Invalid JSON" });
    }
    return proxySse(req, res, { path: streamUrl(), body });
  };
  app.post("/api/chat", chatHandler);
  app.post("/api/chat/stream", chatHandler);

  // ── PDF proxy ─────────────────────────────────────────────────────────
  app.get("/api/documents/pdf/:filename", async (req, res) => {
    const filename = req.params.filename;
    if (!filename) {
      return res.status(400).json({ error: "Missing filename" });
    }

    const upstream = await proxyFetch(
      `/documents/pdf/${encodeURIComponent(filename)}`,
      { method: "GET" },
      30000,
    );

    if (!upstream) {
      return res.status(503).json({
        error: "Document service unavailable",
        detail: "backend unreachable",
      });
    }
    if (!upstream.ok) {
      return res.status(upstream.status).json({
        error: "Document not found",
        detail: `backend responded ${upstream.status}`,
      });
    }

    const headers = {
      "Content-Type": upstream.headers.get("content-type") || "application/pdf",
      "Cache-Control": "public, max-age=86400",
    };
    const length = upstream.headers.get("content-length");
    if (length) headers["Content-Length"] = length;

    // Buffered before writeHead: once the 200 headers are flushed a mid-stream
    // read failure can only produce a truncated response, so the failure has to
    // be caught while the status is still writable. Next streamed res.body
    // straight through, but buffering is what the port requires, and the only
    // documented failure for this endpoint is the 503 below.
    const body = await readUpstreamBuffer(upstream);
    if (body === BODY_UNREADABLE) {
      return res.status(503).json({
        error: "Document service unavailable",
        detail: "backend unreachable",
      });
    }

    res.writeHead(200, headers);
    res.end(body);
  });

  // ── Grievance JSON handlers ───────────────────────────────────────────
  app.post("/api/grievance/answer", (req, res) =>
    proxyJson(req, res, { path: "/grievances/answer", errorKey: "grievance_backend" }),
  );
  app.post("/api/grievance/clarify", (req, res) =>
    proxyJson(req, res, { path: "/grievances/clarify", errorKey: "grievance_backend" }),
  );
  app.post("/api/grievance/detect", (req, res) =>
    proxyJson(req, res, { path: "/grievances", errorKey: "grievance_backend" }),
  );
  app.post("/api/grievance/finalize", (req, res) =>
    proxyJson(req, res, { path: "/grievances/finalize", errorKey: "grievance_backend" }),
  );

  // ── Grievance fields (GET, 404-preserving) ────────────────────────────
  app.get("/api/grievance/fields", async (req, res) => {
    const conversationId = req.query.conversation_id;
    const language = req.query.language || "en";
    if (!conversationId) {
      return res.status(400).json({ error: "conversation_id is required" });
    }

    const upstream = await proxyFetch(
      `/grievances/${encodeURIComponent(conversationId)}/fields?language=${encodeURIComponent(language)}`,
      { method: "GET", headers: await authHeader(req) },
    );

    if (!upstream) {
      return res.status(503).json({
        error: "grievance_backend_unavailable",
        detail: "backend unreachable",
      });
    }
    if (upstream.ok) {
      const data = await readUpstreamJson(upstream);
      if (data !== BODY_UNREADABLE) return res.json(data);
      // Unparseable 200 must land on the same 503 as an unreachable backend,
      // not on the non-ok 502 mapping below (upstream.status is 200 there).
      return res.status(503).json({
        error: "grievance_backend_unavailable",
        detail: "backend unreachable",
      });
    }

    return res.status(upstream.status === 404 ? 404 : 502).json({
      error: "grievance_backend_error",
      detail: `backend responded ${upstream.status}`,
    });
  });

  // ── TTS: form-encoded, returns binary audio ───────────────────────────
  app.post("/api/speak", async (req, res) => {
    let text;
    let language;
    try {
      // multer is not used; busboy-free parsing via express.urlencoded would
      // lose the file semantics, so parse multipart with the raw body.
      const raw = await readRawBody(req);
      const parsed = parseMultipart(raw, req.headers["content-type"] || "");
      text = parsed.text;
      language = parsed.language || "hi";
    } catch {
      return res.status(400).send("Missing text");
    }
    if (!text) return res.status(400).send("Missing text");

    const upstream = await proxyFetch(
      "/voice/speak",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await authHeader(req)) },
        body: JSON.stringify({ text, language }),
      },
      30000,
    );

    if (upstream && upstream.ok) {
      // An unparseable 200 must fall through to the empty 503 below rather than
      // escaping as a 500, matching Next where `await res.json()` was inside
      // the try and the catch fell through to the speechSynthesis fallback.
      const data = await readUpstreamJson(upstream);
      if (data !== BODY_UNREADABLE && data.audio) {
        return res
          .status(200)
          .set("Content-Type", "audio/mpeg")
          .send(hexToBuffer(data.audio));
      }
    }
    // Empty 503 makes the client fall back to the browser's speechSynthesis.
    // .end() rather than .send(""): Express appends "; charset=utf-8" to the
    // body path, which Next's `new Response("", ...)` did not.
    return res.status(503).set("Content-Type", "audio/mpeg").end();
  });

  // ── TTS: segment-based, returns hex JSON ──────────────────────────────
  app.post("/api/voice/speak", async (req, res) => {
    let body;
    try {
      body = await readJson(req);
    } catch {
      return res.status(400).json({ error: "Missing segments" });
    }
    const segments = body.segments;
    if (!segments || segments.length === 0) {
      return res.status(400).json({ error: "Missing segments" });
    }

    const upstream = await proxyFetch(
      "/voice/speak",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await authHeader(req)) },
        body: JSON.stringify({ segments }),
      },
      30000,
    );

    if (upstream && upstream.ok) {
      // Unparseable 200 falls through to the 503, as in Next where
      // `await res.json()` was inside the try.
      const data = await readUpstreamJson(upstream);
      if (data !== BODY_UNREADABLE && data.audio) {
        return res.json({
          audio: data.audio,
          language: data.language || segments[0].language,
        });
      }
    }
    return res.status(503).json({ error: "Backend unavailable" });
  });

  // ── Translation (unauthenticated, degrades to source text) ────────────
  app.post("/api/translate", async (req, res) => {
    let body;
    try {
      body = await readJson(req);
    } catch {
      return res.status(400).json({ error: "Invalid JSON" });
    }

    const texts = body.texts ?? [];
    if (texts.length === 0) return res.json({ translations: [] });

    const upstream = await proxyFetch(
      "/translate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          texts,
          source_language: body.source_language ?? "en",
          target_language: body.target_language ?? "hi",
        }),
      },
      10000,
    );

    if (upstream && upstream.ok) {
      // An unparseable 200 degrades to the source-text fallback below, exactly
      // as it did in Next where `await res.json()` was inside the try.
      const data = await readUpstreamJson(upstream);
      if (data !== BODY_UNREADABLE) return res.json(data);
    }
    // Backend offline: return the originals so the UI degrades to English.
    return res.json({ translations: texts });
  });

  // ── Static assets + SPA fallback (production only) ────────────────────
  const dist = path.resolve(import.meta.dirname, "..", "dist");
  if (fs.existsSync(dist)) {
    app.use(express.static(dist));
    // Registered last so API 404s are not swallowed. app.use() rather than
    // app.get("*") because "*" is invalid in Express 5.
    app.use((req, res, next) => {
      if (req.method !== "GET" || req.path.startsWith("/api")) return next();
      res.sendFile(path.join(dist, "index.html"));
    });
  }

  return app;
}

// ── Multipart helpers (no external dependency) ─────────────────────────
function readRawBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function parseMultipart(buffer, contentType) {
  const match = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType);
  if (!match) throw new Error("no multipart boundary");
  const boundary = `--${match[1] || match[2]}`;
  // "binary" is latin1: one char per byte, so the split is byte-exact and the
  // original bytes are recoverable. The value is then decoded as UTF-8, which
  // is what req.formData() did under Next — Devanagari/Tamil/Bengali text would
  // arrive mojibake if the latin1 string were used directly.
  const parts = buffer.toString("binary").split(boundary);
  const out = {};
  for (const part of parts) {
    const nameMatch = /name="([^"]+)"/.exec(part);
    if (!nameMatch) continue;
    const valuePart = part.split("\r\n\r\n").slice(1).join("\r\n\r\n");
    out[nameMatch[1]] = Buffer.from(valuePart, "binary").toString("utf8").replace(/\r\n$/, "");
  }
  return out;
}

// ── Entry point ─────────────────────────────────────────────────────────
const isDirectRun =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isDirectRun) {
  createApp().listen(process.env.PORT || 8787, () => {
    console.log(`[bff] listening on :${process.env.PORT || 8787}`);
  });
}