import { NextResponse } from "next/server";

/**
 * Server API route /api/translate
 * Proxies translation requests to the Python backend (Sarvam Mayura v1).
 */

export async function POST(req: Request) {
  let body: { texts?: string[]; source_language?: string; target_language?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const texts = body.texts ?? [];
  const sourceLanguage = body.source_language ?? "en";
  const targetLanguage = body.target_language ?? "hi";

  if (texts.length === 0) {
    return NextResponse.json({ translations: [] });
  }

  const backendUrl = process.env.BACKEND_API_URL || "http://localhost:8000";

  // Try backend first
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(`${backendUrl}/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        texts,
        source_language: sourceLanguage,
        target_language: targetLanguage,
      }),
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (res.ok) {
      const data = await res.json();
      return NextResponse.json(data);
    }
  } catch {
    // Backend offline
  }

  // Fallback: return original texts (no translation)
  return NextResponse.json({
    translations: texts,
  });
}
