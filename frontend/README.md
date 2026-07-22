# Frontend — Society Management (Vite + React + TypeScript)

The resident portal, built as a client-side SPA (**Vite + React 18 + TypeScript**,
no SSR). It is a pure client of the backend API documented in
[`../docs/api/`](../docs/api/); the build specification lives in
[`docs/`](./docs/) (start with [`docs/README.md`](./docs/README.md), then
[`docs/00-foundation.md`](./docs/00-foundation.md)).

## Stack
- Vite + React + TypeScript (strict), react-router-dom, TanStack Query
- shadcn/ui (Tailwind + Radix), lucide-react, sonner, react-hook-form + zod

## Develop
```bash
npm install
npm run dev        # serves on http://localhost:3000 (backend CORS allows this origin)
```
API base is configured via `VITE_API_BASE` in `.env` (defaults to
`http://localhost:8000`). Bring the backend up first from the repo root
(`docker compose up -d`).

## Scripts
- `npm run dev` — dev server on port 3000
- `npm run build` — type-check (`tsc -b`) + production build
- `npm run typecheck` — `tsc --noEmit`
- `npm run preview` — preview the production build
