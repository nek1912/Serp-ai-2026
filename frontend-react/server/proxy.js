import { getToken } from "./clerk.js";

// Exported: sse.js and index.js both resolve the backend origin through it.
export const BACKEND = () =>
  (process.env.BACKEND_API_URL || "http://localhost:8000").replace(/\/+$/, "");

/**
 * Builds the Authorization header set, mirroring the Next handlers exactly.
 * A signed-out request yields no header at all, which is what the Next.js code
 * did with `...(token ? { Authorization: ... } : {})`.
 */
export async function authHeader(req) {
  const token = await getToken(req);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Calls FastAPI with a timeout. Resolves to null on any network failure or
 * timeout so callers can emit their own 503 — the Next handlers wrapped
 * fetch() in try/catch and returned 503, never letting the error escape.
 */
export async function proxyFetch(path, init = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${BACKEND()}${path}`, { ...init, signal: controller.signal });
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The shape shared by all six grievance JSON handlers:
 * parse JSON body (400 on failure) -> forward with auth -> 200 pass-through,
 * 502 when upstream is not ok, 503 when unreachable.
 *
 * `errorKey` is the shared prefix ("grievance_backend") so the two failure
 * codes stay the exact strings the Next handlers emitted:
 * 502 -> grievance_backend_error, 503 -> grievance_backend_unavailable.
 */
export async function proxyJson(req, res, { path, errorKey }) {
  let body;
  try {
    body = await readJson(req);
  } catch {
    return res.status(400).json({ error: "Invalid JSON" });
  }

  const headers = { "Content-Type": "application/json", ...(await authHeader(req)) };
  // Stringify here, not in proxyFetch: a plain object passed as fetch()'s body
  // is coerced to the string "[object Object]", which FastAPI rejects. The
  // Next handlers sent JSON.stringify(body).
  const upstream = await proxyFetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (!upstream) {
    return res.status(503).json({ error: `${errorKey}_unavailable`, detail: "backend unreachable" });
  }
  if (!upstream.ok) {
    return res.status(502).json({ error: `${errorKey}_error`, detail: `backend responded ${upstream.status}` });
  }
  return res.json(await upstream.json());
}

/** Buffers and parses a JSON request body. Throws on malformed JSON. */
export function readJson(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
    });
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

/** Converts the backend's hex-encoded audio into a binary Buffer. */
export function hexToBuffer(hex) {
  return Buffer.from(hex, "hex");
}