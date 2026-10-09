# JanSahay Frontend — React + Vite

Multilingual citizen-assistance frontend for cooperative governance, PMFBY, PACS,
financial literacy and grievance redressal.

React 19 + Vite 8 single-page app. The browser talks **directly** to the Python
FastAPI backend and attaches Clerk's own session token to every request. There is
no proxy server and no second port.

---

## Requirements

- **Node 20.19+ or 22.12+** (Vite 8 engine floor; developed on 24.13)
- A running Python backend (default `http://localhost:8000`)
- A Clerk application with a publishable key

## Setup

```bash
npm install
cp .env.example .env    # then fill in the values
```

| Variable | Scope | Purpose |
|---|---|---|
| `VITE_CLERK_PUBLISHABLE_KEY` | browser | Clerk session in the browser |
| `VITE_CLERK_SIGN_IN_URL` | browser | Sign-in redirect target |
| `VITE_CLERK_SIGN_UP_URL` | browser | Sign-up redirect target |
| `VITE_BACKEND_API_URL` | browser | FastAPI base URL, e.g. `http://localhost:8000` |
| `VITE_PORT` | dev only | Vite dev-server port (default 5173) |

Only `VITE_`-prefixed variables reach the browser bundle. `CLERK_SECRET_KEY` is
never needed here; it lives in `backend/.env` (the backend's only config source)
and stays inside the backend.

Add your sign-in and sign-up URLs as **redirect URLs** in the Clerk dashboard.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server on `:5173` (set `VITE_PORT`) |
| `npm run build` | Type-check, then emit static assets to `dist/` |
| `npm run lint` | ESLint over `src/` |
| `npm test` | Vitest — 84 tests across 18 files |
| `npm run i18n:coverage` | Report per-locale translation coverage |

## Architecture

```
Browser :5173
   |
   |-- Clerk session token (Authorization: Bearer)
   |
   +-- POST /chat/stream  (SSE)  -----------------------> FastAPI :8000
   +-- POST /grievances/*                            ---> FastAPI
   +-- POST /voice/speak, /translate, /documents/pdf/* ---> FastAPI
```

Two ports only: the dev server and FastAPI. Cross-origin, so the backend's
`ALLOWED_ORIGINS` must list this dev server's origin.

`src/lib/backend.ts` owns the whole conversation with FastAPI: the base URL, the
`/api/*` → FastAPI path mapping, and the bearer header. `App.tsx` hands Clerk's
`useAuth().getToken` to it once at mount, because `api.ts` and `speech.ts` are
plain modules that cannot call hooks.

### Authentication

The browser holds a Clerk **session token** only. `CLERK_SECRET_KEY` never leaves
the backend, where Clerk's own JWKS verification lives in `app/auth.py`.

Because the browser signs its own requests, the frontend's Clerk instance and the
backend's `CLERK_ISSUER` **must be the same Clerk application**. If they diverge,
every authenticated call returns 401 while the app appears to work.

## Deploying

This is a pure static bundle. Serve `dist/` from any static host or CDN.

```bash
npm ci && npm run build
```

`render.yaml` at the repo root configures only the Python backend and has **not**
been updated for this frontend.

## Known limitations

- **Not a PWA.** No web app manifest and no service worker.
- **No dark mode.** The palette is light-only.
- **Translation coverage is incomplete.** 11 locales are wired up, but 791 of
  3,740 possible strings are untranslated (78.9%; `bn` lowest at 65.2%). Missing
  keys fall back to English. Run `npm run i18n:coverage`.
- **The main JS bundle is ~1.5 MB** (~400 kB gzipped), mostly `gsap`,
  `react-markdown` and Clerk. Not code-split.

## Conventions

- `@/` resolves to `src/` in `tsconfig.json`, `vite.config.ts` and
  `vitest.config.mjs`. All three must stay in agreement.
- `src/styles/globals.css` is the design system and is kept byte-identical to
  the original. Its 12 `--font-*` variables are declared in `index.html`;
  `globals.css` composes font stacks from them by name — do not rename them.
- Components carry no `"use client"` directive; there is no such thing in a Vite
  SPA.
- ESLint reports 46 warnings, all pre-existing in the copied source. They are
  demoted to `warn` rather than refactored, so the debt stays visible.
