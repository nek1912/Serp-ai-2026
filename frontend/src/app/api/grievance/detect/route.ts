import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";

/**
 * Server API route /api/grievance/detect
 * Proxies a grievance complaint message (first submission, or a
 * "No, let me clarify" resubmission with a fresh conversation_id) to
 * the Python backend's 9-stage grievance workflow.
 */
export async function POST(req: Request) {
  let body: { message?: string; conversation_id?: string; user_id?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const { getToken } = await auth();
  const token = await getToken();
  const backendUrl = process.env.BACKEND_API_URL || "http://localhost:8000";

  try {
    const res = await fetch(`${backendUrl}/grievances`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      return NextResponse.json(await res.json());
    }
    return NextResponse.json(
      { error: "grievance_backend_error", detail: `backend responded ${res.status}` },
      { status: 502 },
    );
  } catch {
    return NextResponse.json(
      { error: "grievance_backend_unavailable", detail: "backend unreachable" },
      { status: 503 },
    );
  }
}
