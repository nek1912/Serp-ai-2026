import { auth } from "@clerk/nextjs/server";

/**
 * Server API route /api/chat
 * JSON proxy to the Python RAG backend non-streaming endpoint.
 */

export async function POST(req: Request) {
  let body: { question?: string; session_id?: string; language?: string; state?: string | null };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const { getToken } = await auth();
  const token = await getToken();
  // BACKEND_API_URL is the base (e.g. http://localhost:8000). Strip any
  // legacy /chat or /chat/stream suffix so both old and new env values work.
  const rawBase = process.env.BACKEND_API_URL || "http://localhost:8000";
  const backendUrl = `${rawBase.replace(/\/chat(\/stream)?$/, "")}/chat`;

  try {
    const backendRes = await fetch(backendUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });

    if (!backendRes.ok) {
      return new Response(
        JSON.stringify({ error: "retrieval_backend_error", detail: `backend responded ${backendRes.status}` }),
        { status: 502, headers: { "Content-Type": "application/json" } },
      );
    }

    // Stream the SSE response through
    return new Response(backendRes.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (err) {
    console.error(`[api/chat] backend unreachable: ${backendUrl}`, err);
    return new Response(
      JSON.stringify({ error: "retrieval_backend_unavailable", detail: `backend unreachable: ${backendUrl}` }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    );
  }
}
