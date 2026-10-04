/**
 * Server API route /api/documents/pdf/[filename]
 * Proxies PDF file requests to the Python backend's /documents/pdf/{filename} endpoint.
 */

export async function GET(
  req: Request,
  { params }: { params: Promise<{ filename: string }> },
) {
  const { filename } = await params;

  if (!filename) {
    return new Response(JSON.stringify({ error: "Missing filename" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const backendUrl = process.env.BACKEND_API_URL || "http://localhost:8000";

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);

    const res = await fetch(`${backendUrl}/documents/pdf/${encodeURIComponent(filename)}`, {
      method: "GET",
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (!res.ok) {
      return new Response(
        JSON.stringify({ error: "Document not found", detail: `backend responded ${res.status}` }),
        { status: res.status, headers: { "Content-Type": "application/json" } },
      );
    }

    const contentType = res.headers.get("content-type") || "application/pdf";
    const contentLength = res.headers.get("content-length");

    const headers: Record<string, string> = {
      "Content-Type": contentType,
      "Cache-Control": "public, max-age=86400",
    };
    if (contentLength) {
      headers["Content-Length"] = contentLength;
    }

    return new Response(res.body, {
      status: 200,
      headers,
    });
  } catch {
    return new Response(
      JSON.stringify({ error: "Document service unavailable", detail: "backend unreachable" }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    );
  }
}
