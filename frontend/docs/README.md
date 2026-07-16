# Society Management — Frontend Build Docs (Resident Portal)

This folder is the **complete build specification** for the Society Management frontend. Each doc is
self-contained: hand one doc to a **fresh Claude session with no prior context**, and it can build
that piece correctly and error-free without asking questions.

> **Backend is already built and running.** The frontend is a pure API client. The ONLY source of
> truth for the API is `d:\society\docs\api\*.md`. Endpoint shapes in these docs are copied from
> there; if anything is ambiguous, that folder wins.

---

## What we are building

The **resident portal** of a multi-tenant society-management app. A resident logs in and can:
- read the **Notice Board** (landing page),
- view their **Financial** page (their house + read-only maintenance dues + that house's complaints),
- raise and track **Complaints** (with photos),
- see their **Notifications**.

Mobile-first (small phones → desktop monitors). The login/shell are **portal-agnostic** so the admin
portal can be added later with **additions only** — no refactor.

## Stack (fixed — do not substitute)

| Concern | Choice |
|---|---|
| Build tool | **Vite** + React 18 + **TypeScript** (strict) |
| Routing | **react-router-dom v6** (`createBrowserRouter`) |
| UI | **shadcn/ui** (Tailwind CSS + Radix), components copied into `src/components/ui` |
| Icons | `lucide-react` |
| Server state | **TanStack Query** (`@tanstack/react-query`) |
| Toasts | **sonner** (shadcn wrapper) |
| Forms | `react-hook-form` + `zod` |
| API base | `http://localhost:8000` (no path prefix) via `VITE_API_BASE` |
| Dev port | **3000** (backend CORS allows exactly `http://localhost:3000`) |

There is **no SSR** — the app is a client SPA. (An old `d:\society\index.html` mockup exists; it is
**NOT a design reference**. You may glance at it only for behavioral logic — never copy its CSS.)

---

## Build order — hand the docs out in this sequence

### Stage 1 — Foundation (ONE session, must finish and be merged first)
Give session 1 → **[`00-foundation.md`](./00-foundation.md)**.
It scaffolds the project, installs everything, and builds every shared piece the modules import:
the API client + token refresh, auth/guards, the responsive shell, the theme toggle, all common
components, the permission primitives, and **frozen stub signatures** (endpoint fns, query keys,
shared hooks) that the module sessions fill in.
**Deliverable:** the app runs — login → (portal chooser) → shell with nav tabs; module pages show a
placeholder/loading state. Merge this before starting Stage 2.

### Stage 2 — Modules (up to 4 sessions IN PARALLEL, after foundation is merged)
Each session gets exactly one doc. They never touch each other's files, and none edits foundation
files or redefines a shared contract.

| Session | Doc | Route | Module key |
|---|---|---|---|
| A | [`notices.md`](./notices.md) — **the landing page** | `/notices`, `/notices/:id` | `notices` |
| B | [`complaints.md`](./complaints.md) | `/complaints`, `/complaints/:id` | `complaints` |
| C | [`finance.md`](./finance.md) — house + dues merged | `/finance` | `finance` |
| D | [`notifications.md`](./notifications.md) | `/notifications` | `notifications` |

> **All four Stage-2 sessions are fully independent and can run at the same time, merging in any
> order.** The foundation ships stub hooks (`useComplaints`, `useHouseDues` — see `00-foundation.md`
> §5b) so no session ever imports an unbuilt module's file; each keeps `tsc --noEmit` green on its
> own. Finance reuses Complaints' `useComplaints` to list a house's complaints — until Complaints
> lands, that section renders empty via the stub, then fills in automatically. No blocking, no
> ordering requirement.

---

## The permission model (every session must understand this)

After login, `GET /me` returns the shell view. Two arrays drive all gating:

- **`modules: string[]`** — which nav tabs/pages exist for this portal. Resident modules include
  `notices`, `complaints`, `finance`, `notifications`. Gate whole tabs/pages with
  `<IfModule module="...">`.
- **`permissions: string[]`** — flat dot-strings (e.g. `notices.read`, `complaints.create`,
  `finance.read`, `notifications.read`). Gate individual buttons/actions with `<Can permission="...">`.

**The server re-checks every request** — client checks are hints only. Every screen must also handle
a returned `403 permission_denied` gracefully by rendering `<Forbidden />`, never crashing.

> **Real permission keys come from the backend, listed per-module in each doc.** Ignore the
> illustrative keys in some `auth.md` examples (`finance.manage`, `complaints.assign`) — they are not
> real. Use the keys named in each module doc.

---

## Frozen shared contracts (defined in `00-foundation.md`; modules import, never redefine)

- `apiFetch` + typed `ApiError` (parses the `{code, message, details}` envelope) — `src/lib/api/client.ts`
- `queryKeys.*` factory — `src/lib/api/queryKeys.ts`
- Endpoint fns (stubbed signatures) — `src/lib/api/endpoints.ts`
- `useAuth()`, `useCan()`, `useModule()` — `src/hooks/`
- `<Can>`, `<IfModule>` — `src/components/common/`
- `useUnreadNotifications()` (badge source; owned by the Notifications session) — signature + query key frozen
- `useHouseDues(houseId)`, `useComplaints(params)` — signatures frozen
- `notificationLinks(n)` (deep-link resolver) — `src/lib/notificationLinks.ts`
- `<DataView>` (Table on desktop, Card list on mobile), `<FormModal>` (Dialog↔Sheet),
  `<StatusBadge>`, `<EmptyState>`, `<Forbidden>`, `<LoadingState>`, `<PageHeader>`, `<SectionCard>`,
  `<ConfirmDialog>` — `src/components/common/`
- `fmtDate`, `fmtDateOnly`, `formatCurrency` (INR) — `src/lib/format.ts`
- `useTheme()` + theme provider (light/dark toggle) — `src/hooks/useTheme.ts`

---

## Two API gotchas every relevant doc must respect

1. **Residents have NO `/houses/*` access.** The Financial page's "house info" is assembled from
   `house_id` / `house_display_code` embedded in the dues and complaints payloads — there is **no
   owner/tenant record** available to a resident, and no endpoint returns the resident's `house_id`
   directly (derive it from dues/complaints). New resident with no dues/complaints → empty state.
2. **`finance.read` over-grants.** The backend will NOT stop a resident from calling society-wide
   finance/analytics endpoints. Hide any admin finance UI **by portal** (`portal === "admin"`), not
   by permission. The resident Financial page shows **own-house dues only**, read-only.

## Product rules (confirmed)

- **Dues are read-only.** There is no online-payment endpoint. Show a static "how to pay" note
  (pay offline / contact admin). No "Pay now" button.
- **My House is merged into the Financial tab.** Resident nav = Notice Board (landing), Financial,
  Complaints, Notifications.
- **Complaint photos are in v1** (upload + delete, capped by backend config).
- **Light + dark theme** with a persisted toggle.

---

## Definition of done (per building session)
Run the **self-verification checklist** at the bottom of your doc: `npm run dev` on port 3000 against
the backend on `:8000`, exercise the flow end-to-end, then `tsc --noEmit` and `npm run build` must
both be clean. Do not consider a piece finished until its checklist passes.
