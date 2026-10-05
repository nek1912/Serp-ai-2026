import { clerkMiddleware, getAuth } from "@clerk/express";

/**
 * Mounted by index.js. Attaches Clerk's Auth object to every request,
 * exactly as src/proxy.ts's clerkMiddleware() did under Next.
 */
export const clerk = clerkMiddleware({
  publishableKey: process.env.VITE_CLERK_PUBLISHABLE_KEY,
  secretKey: process.env.CLERK_SECRET_KEY,
});

/**
 * Returns the caller's Clerk session JWT, or null when signed out.
 * Replaces the Next.js `const { getToken } = await auth(); await getToken();`
 * pair. The token is forwarded to FastAPI as `Authorization: Bearer <token>`,
 * which is what the Python backend's auth dependency expects.
 */
export async function getToken(req) {
  try {
    const { getToken: readToken } = getAuth(req);
    if (typeof readToken !== "function") return null;
    const token = await readToken();
    return token ?? null;
  } catch {
    // Signed-out or malformed session. The backend treats a missing token as
    // anonymous, which matches the Next.js behaviour where auth() returns a
    // null token rather than throwing.
    return null;
  }
}