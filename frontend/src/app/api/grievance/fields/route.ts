import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";

/**
 * Server API route /api/grievance/fields
 * Proxies to GET /grievances/{conversation_id}/fields on the Python backend.
 */
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const conversationId = searchParams.get("conversation_id");
  const language = searchParams.get("language") || "en";

  if (!conversationId) {
    return NextResponse.json({ error: "conversation_id is required" }, { status: 400 });
  }

  const { getToken } = await auth();
  const token = await getToken();
  const backendUrl = process.env.BACKEND_API_URL || "http://localhost:8000";

  try {
    const res = await fetch(
      `${backendUrl}/grievances/${encodeURIComponent(conversationId)}/fields?language=${encodeURIComponent(language)}`,
      {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      },
    );
    if (res.ok) {
      return NextResponse.json(await res.json());
    }
    return NextResponse.json(
      { error: "grievance_backend_error", detail: `backend responded ${res.status}` },
      { status: res.status === 404 ? 404 : 502 },
    );
  } catch {
    return NextResponse.json(
      { error: "grievance_backend_unavailable", detail: "backend unreachable" },
      { status: 503 },
    );
  }
}
