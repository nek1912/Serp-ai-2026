import { auth } from "@clerk/nextjs/server";

/**
 * Server API route /api/chat/stream
 * SSE proxy to the Python RAG backend streaming endpoint.
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
  const backendUrl = process.env.BACKEND_API_URL?.replace(/\/chat$/, "/chat/stream")
    || "http://localhost:8000/chat/stream";

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
  } catch {
    return new Response(
      JSON.stringify({ error: "retrieval_backend_unavailable", detail: "backend unreachable" }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    );
  }
}
