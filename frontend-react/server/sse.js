import { authHeader, BACKEND } from "./proxy.js";

/** Accepts either an absolute URL or a path relative to BACKEND(). */
const target = (path) => (/^https?:\/\//i.test(path) ? path : `${BACKEND()}${path}`);

/**
 * Ceiling on one streaming answer. A dual static+web RAG pipeline can legitimately
 * take a while, but not forever: without this a hung backend holds a response
 * open indefinitely, and this route is the only one with no timeout at all.
 */
const STREAM_TIMEOUT_MS = 120000;

/**
 * Streams FastAPI's SSE response through to the browser.
 *
 * Buffering here silently breaks the single most visible feature of the app, so:
 * no compression middleware anywhere in this process, flushHeaders() before the
 * first chunk, and X-Accel-Buffering: no so no upstream proxy buffers it.
 *
 * The stream is also torn down when the client goes away. res.write() on a
 * destroyed response neither throws nor emits "error", so a browser that hits
 * Back, starts a new chat or loses mobile signal would otherwise leave this loop
 * draining the upstream to completion -- FastAPI still running retrieval and LLM
 * generation for an answer nobody will read, and this process holding the socket
 * for the duration.
 */
export async function proxySse(req, res, { path, body }) {
  const controller = new AbortController();
  const onClientGone = () => controller.abort();
  /*
   * res.on("close"), NOT req.on("close").
   *
   * Since Node 16, `req` emits "close" when the request BODY finishes being
   * consumed -- which readJson already triggered before this handler runs. So a
   * req listener attached here never fires again, and the abort never happened:
   * an abandoned chat sat parked in reader.read() with the upstream still
   * burning retrieval and LLM tokens for an answer nobody would read.
   *
   * `res` emits "close" when the RESPONSE socket is torn down, which is the
   * event we actually mean. It also fires on normal completion, so the
   * `finally` block removes the listener to avoid an abort after the fact.
   */
  res.on("close", onClientGone);
  const timer = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS);

  try {
    let headers;
    try {
      headers = { "Content-Type": "application/json", ...(await authHeader(req)) };
    } catch (err) {
      // AuthUnavailableError from getToken: we could not establish who this is,
      // so we must not forward it. Fail closed.
      return res.status(err?.status ?? 503).json({ error: "auth_unavailable" });
    }

    let upstream;
    try {
      upstream = await fetch(target(path), {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch {
      if (res.headersSent) {
        // The stream already started, so the status line is long gone.
        return res.end();
      }
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
        // Belt and braces alongside the abort above: a destroyed response must
        // never be written to again.
        if (res.destroyed) break;
        /*
         * Node's res.write on an HTTP/1.1 response without content-length writes
         * straight to the socket, so tokens reach the browser as they arrive.
         * Express's res has no `flush` method -- only the `compression` middleware
         * adds one, and that is deliberately NOT a dependency of this process,
         * because it would buffer the stream and destroy the incremental
         * rendering this endpoint exists for.
         *
         * Do not "fix" this by adding compression or an explicit flush call.
         */
        res.write(Buffer.from(value));
      }
    } finally {
      // Release the upstream connection instead of leaving it pinned until the
      // response completes on its own.
      reader.cancel().catch(() => {});
    }
  } catch {
    // Client disconnected or upstream aborted mid-stream. Nothing to do; the
    // browser already has whatever partial answer arrived.
  } finally {
    clearTimeout(timer);
    res.off("close", onClientGone);
    if (!res.headersSent) {
      res.status(503).json({ error: "retrieval_backend_unavailable" });
    } else if (!res.writableEnded) {
      res.end();
    }
  }
}
