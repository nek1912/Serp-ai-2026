# JanSahay Frontend — Next.js 16 PWA

Multilingual (11 languages) chat, grievance intake, schemes/services/library/faq/legal browsers. Talks to the FastAPI backend **only through `src/app/api/*` proxy routes** (Clerk JWT attached server-side; no backend keys in the browser).

## Setup

```bash
npm install
npm run dev   # http://localhost:3000
```

## Environment (`frontend/.env.local`, git-ignored — restart `npm run dev` after changes)

```
BACKEND_API_URL=http://localhost:8000   # backend BASE, no /chat suffix
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=...   # sign-in
CLERK_SECRET_KEY=...                    # server-side only
```

Proxy mapping: `/api/chat` → `{BACKEND_API_URL}/chat`, `/api/chat/stream` → `{BACKEND_API_URL}/chat/stream` (a legacy `/chat` suffix is stripped automatically). A **503** means the backend is unreachable; a **502** means it answered non-OK.

## Key files

- `src/app/api/chat/route.ts`, `src/app/api/chat/stream/route.ts` — chat proxies (JSON + SSE)
- `src/lib/api.ts` — `sendChat` / `sendChatStream` client
- `src/components/ChatWindow.tsx` — main chat UI (streaming, voice, citations)
- `src/lib/i18n/` — 11-language dictionaries
