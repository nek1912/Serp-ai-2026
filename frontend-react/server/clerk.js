import { clerkMiddleware, getAuth } from "@clerk/express";

/*
 * Clerk key resolution.
 *
 * `@clerk/express` reads its own publishable key from process.env.CLERK_PUBLISHABLE_KEY
 * (see node_modules/@clerk/express/dist/utils-*.mjs). It does NOT know about the
 * VITE_ prefix, which is a Vite build-time convention with no meaning to Node at
 * runtime.
 *
 * An earlier version of this file passed VITE_CLERK_PUBLISHABLE_KEY explicitly,
 * which overrode Clerk's own lookup with a name it never reads. The result was
 * that an operator who set exactly the two documented server variables
 * (CLERK_SECRET_KEY and BACKEND_API_URL) got `publishableKey: undefined`, Clerk
 * threw "Publishable key is missing", and EVERY /api route returned a 500 HTML
 * page -- i.e. the whole product, dead, while the process booted cleanly.
 *
 * So: read the conventional name first, accept the VITE_ name as a convenience
 * fallback, and fail loudly at boot rather than at the first request.
 */
const publishableKey =
  process.env.CLERK_PUBLISHABLE_KEY || process.env.VITE_CLERK_PUBLISHABLE_KEY;
const secretKey = process.env.CLERK_SECRET_KEY;

if (!publishableKey || !secretKey) {
  throw new Error(
    "[bff] Missing Clerk configuration. Set CLERK_PUBLISHABLE_KEY and " +
      "CLERK_SECRET_KEY for the server (CLERK_PUBLISHABLE_KEY may also be " +
      "named VITE_CLERK_PUBLISHABLE_KEY). VITE_ variables are a browser-build " +
      "convention and are NOT what @clerk/express reads at runtime.",
  );
}

/**
 * Mounted by index.js on /api only. Attaches Clerk's Auth object to the request,
 * the analogue of src/proxy.ts's clerkMiddleware() under Next.
 */
export const clerk = clerkMiddleware({ publishableKey, secretKey });

/**
 * Raised when a session exists but we cannot mint a token for it -- a Clerk
 * outage, a JWKS rotation miss, an expired session. Distinct from "signed out",
 * and must NOT be treated as anonymous: see getToken.
 */
export class AuthUnavailableError extends Error {
  status = 503;
  constructor(cause) {
    super("auth_unavailable");
    this.name = "AuthUnavailableError";
    this.cause = cause;
  }
}

/**
 * Returns the caller's Clerk session JWT, or null when genuinely signed out.
 * Replaces the Next.js `const { getToken } = await auth(); await getToken();`
 * pair, whose result is forwarded to FastAPI as `Authorization: Bearer <token>`.
 *
 * FAILS CLOSED. Under Next this pair sat OUTSIDE any try block and a failure
 * produced a 500. A blanket `catch { return null }` would instead be
 * indistinguishable from "signed out", and would forward an unverifiable
 * identity to the backend as an anonymous request -- detaching the citizen's
 * conversation mid-stream and attaching their grievance draft to nobody, with
 * nothing logged. For a grievance-redressal product that is worse than an
 * error, so we refuse to proceed instead.
 */
export async function getToken(req) {
  let readToken;
  try {
    ({ getToken: readToken } = getAuth(req));
  } catch {
    // Clerk middleware never attached an Auth object. Genuinely anonymous.
    return null;
  }

  if (typeof readToken !== "function") return null;

  try {
    return (await readToken()) ?? null;
  } catch (err) {
    console.error(
      { err: err?.message },
      "[bff] clerk token minting failed - refusing to forward an unverified identity",
    );
    throw new AuthUnavailableError(err);
  }
}