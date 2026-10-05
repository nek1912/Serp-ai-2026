import { authHeader, BACKEND } from "./proxy.js";

/** Accepts either an absolute URL or a path relative to BACKEND(). */
const target = (path) => (/^https?:\/\//i.test(path) ? path : `${BACKEND()}${path}`);

/**
 * Streams FastAPI's SSE response through to the browser.
 *
 * Buffering here silently breaks the single most visible feature of the app,
 * so: no compression middleware anywhere in this process, flushHeaders() before
 * the first chunk, and X-Accel-Buffering: no so no upstream proxy buffers it.
 */
export async function proxySse(req, res, { path, body }) {
  const headers = { "Content-Type": "application/json", ...(await authHeader(req)) };

  let upstream;
  try {
    upstream = await fetch(target(path), {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    return res.status(503).json({
      error: "retrieval_backend_unavailable",
      detail: "backend unreachable",
    });
  }

  if (!upstream.ok) {
    return res.status(502).json({
      error: "retrieval_backend_error",
      detail: `backend responded ${upstream.status}`,
    });
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.flushHeaders?.();

  const reader = upstream.body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
      // Flush each chunk so the browser renders tokens as they arrive.
      res.flush?.();
    }
  } catch {
    // Client disconnected or upstream aborted mid-stream. Nothing to do;
    // the browser has already received a partial answer.
  } finally {
    res.end();
  }
}