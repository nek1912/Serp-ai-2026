import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";

/**
 * Server API route /api/speak
 * Proxies TTS requests to the Python backend (Sarvam Bulbul v3 → Azure fallback).
 */

export async function POST(req: Request) {
  const formData = await req.formData();
  const text = formData.get("text") as string;
  const language = (formData.get("language") as string) || "hi";

  if (!text) {
    return new Response("Missing text", { status: 400 });
  }

  const { getToken } = await auth();
  const token = await getToken();
  const backendUrl = process.env.BACKEND_API_URL || "http://localhost:8000";

  // Try backend first
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);

    const res = await fetch(`${backendUrl}/voice/speak`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ text, language }),
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (res.ok) {
      const data = await res.json();
      if (data.audio) {
        // Convert hex to binary
        const hex = data.audio;
        const bytes = new Uint8Array(hex.length / 2);
        for (let i = 0; i < hex.length; i += 2) {
          bytes[i / 2] = parseInt(hex.substr(i, 2), 16);
        }
        return new Response(bytes, {
          headers: { "Content-Type": "audio/mpeg" },
        });
      }
    }
  } catch {
    // Backend offline
  }

  // Fallback: return empty (client falls back to browser TTS)
  return new Response("", { status: 503, headers: { "Content-Type": "audio/mpeg" } });
}
