# JanSahay Frontend — React + Vite

Multilingual citizen-assistance frontend for cooperative governance, PMFBY, PACS,
financial literacy and grievance redressal.

React 19 + Vite 8 single-page app, with a small Express 5 backend-for-frontend
(BFF) that injects a Clerk bearer token and forwards requests to the Python
FastAPI service.

---

## Requirements

- **Node 20.19+ or 22.12+** (Vite 8 engine floor; developed on 24.13)
- A running Python backend (`backend/` on FastAPI, default `http://localhost:8000`)
- A Clerk application with a publishable and secret key

## Setup

```bash
npm install
cp .env.example .env    # then fill in the four values
```

| Variable | Scope | Purpose |
|---|---|---|
| `VITE_CLERK_PUBLISHABLE_KEY` | browser | Clerk session in the browser |
| `VITE_CLERK_SIGN_IN_URL` | browser | Sign-in redirect target |
| `VITE_CLERK_SIGN_UP_URL` | browser | Sign-up redirect target |
| `CLERK_PUBLISHABLE_KEY` | **server only** | Read by `@clerk/express` at runtime. May be named `VITE_CLERK_PUBLISHABLE_KEY` instead, but the server reads the conventional name first |
| `CLERK_SECRET_KEY` | **server only** | Mints the bearer token forwarded to FastAPI |
| `BACKEND_API_URL` | **server only** | Python backend base URL (`http://localhost:8000`) |
| `VITE_PORT` | dev only | Vite dev-server port (default 5173) |
| `BFF_PORT` | dev only | BFF port that Vite proxies `/api` to (default 8787) |

Only `VITE_`-prefixed variables reach the browser bundle. `CLERK_SECRET_KEY` is
read exclusively in `server/` — never in `src/`.

Add your sign-in and sign-up URLs as **redirect URLs** in the Clerk dashboard.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server on `:5173` (set `VITE_PORT`), proxying `/api` → `:8787` |
| `npm run dev:server` | Express BFF alone on `:8787` |
| `npm run dev:all` | Both, via `concurrently` |
| `npm run build` | Type-check, then emit static assets to `dist/` |
| `npm start` / `npm run preview` | Serve `dist/` **and** `/api` from one Express process |
| `npm run lint` | ESLint over `src/` (TS) and `server/` (JS) |
| `npm test` | Vitest — 127 tests across 19 files |
| `npm run i18n:coverage` | Report per-locale translation coverage |

`.env` is loaded automatically by both dev and start via Node's
`--env-file-if-exists`, matching what `next dev` used to do. No `dotenv`
dependency.

## Architecture

```
Development                          Production
───────────                          ───────────
Browser :5173                        Browser
   │                                    │
   ├─ /api/* ──proxy──► Express :8787   └─► Express (single process)
   │                          │                    ├─ /api/* ──► FastAPI
   │                          └─► FastAPI :8000    └─ /*     ──► dist/
   │                             (+ Clerk Bearer)
   └─ /*    ──► src/ via Vite
```

Same origin in both environments, so no CORS configuration is needed anywhere.

### Why there is a server at all

The frontend needs a Clerk session token attached to backend requests.
`CLERK_SECRET_KEY` cannot be placed in a browser bundle, so this layer cannot be
removed without either dropping authentication or moving token minting to the
client. It is deliberately tiny — `server/` is about 300 lines across four
files:

| File | Responsibility |
|---|---|
| `server/index.js` | The URL surface — 11 endpoints, static serving, SPA fallback |
| `server/clerk.js` | Clerk middleware and bearer-token extraction |
| `server/proxy.js` | Calling FastAPI with a timeout; mapping failures to the contract |
| `server/sse.js` | Streaming `/chat` without buffering |

`src/lib/api.ts` calls the same paths it always did, so it needed no changes.

### Endpoint contract

Status codes are part of the frontend's contract — `ChatWindow` and `speech`
branch on them to trigger fallbacks. Do not change them casually.

| Meaning | Status | Body |
|---|---|---|
| Upstream answered but not OK | `502` | `{error: "*_backend_error"}` |
| Upstream unreachable or unreadable | `503` | `{error: "*_backend_unavailable"}` |
| Upstream 404 on grievance fields | `404` | passed through |
| TTS unavailable | `503` | empty `audio/mpeg` body → browser `speechSynthesis` fallback |

Clerk middleware is mounted on **`/api` only**. A malformed key then degrades the
API alone; a globally-mounted middleware would return a 500 HTML page for every
route including `/` and the static assets.

## Deploying

One process serves everything, so this deploys as a single service.

```bash
npm ci
npm run build
npm start          # honours $PORT
```

`render.yaml` at the repo root currently configures only the Python backend and
has **not** been updated for this frontend. Adding a frontend service to it is a
separate, deliberate step.

## Known limitations

- **Not a PWA.** Despite what older docs in this repo claim, there is no web app
  manifest and no service worker. The app is not installable and does not work
  offline.
- **No dark mode.** The palette is light-only.
- **Translation coverage is incomplete.** 11 locales are wired up, but 791 of
  3,740 possible strings are untranslated (78.9% coverage; `bn` is lowest at
  65.2%). Missing keys fall back to English via `translate()`'s `en[key]`
  lookup. Run `npm run i18n:coverage` for current numbers.
- **`/api/translate` ignores its `to` field.** Two call sites
  (`ChatWindow.tsx`, `lib/translator.ts`) send `{texts, to}` while the handler
  reads `target_language`, so the target silently defaults to `"hi"`. This
  predates the migration and is unchanged by it; it needs its own fix.
- **The main JS bundle is ~1.5 MB** (~400 kB gzipped), mostly `gsap`,
  `react-markdown` and Clerk. Not code-split.

## Conventions

- `@/` resolves to `src/` in `tsconfig.json`, `vite.config.ts` and
  `vitest.config.mjs`. All three must stay in agreement.
- `src/styles/globals.css` is the design system and is kept byte-identical to
  the original. Its 12 `--font-*` variables are declared in `index.html`, and
  `globals.css` composes font stacks from them by name — do not rename them.
- Components carry no `"use client"` directive; there is no such thing in a Vite
  SPA.
- ESLint reports 46 warnings, all pre-existing in the copied source (unused
  imports, `set-state-in-effect`). They are demoted to `warn` rather than
  refactored, so the debt stays visible without failing the gate.