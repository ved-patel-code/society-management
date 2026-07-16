# Foundation — Society Management Frontend (Stage 1)

**You are building the foundation of the resident portal.** Everything the module sessions
(Notices, Complaints, Finance, Notifications) import comes from here. Build it exactly as specified —
the frozen signatures at the end are a contract other sessions depend on; do not change their names
or shapes.

> Read `README.md` in this folder first for the big picture. API source of truth: `d:\society\docs\api\*.md`.
> Backend runs at `http://localhost:8000` (Bearer JWT, no cookies). Your dev server MUST run on port
> **3000** (backend CORS allows exactly `http://localhost:3000`).

**Working directory:** `d:\society\frontend`. Create the app *in place* here (this `docs/` folder
already exists and must be preserved). If a tool scaffolds into a subfolder, move files up so
`package.json` sits at `d:\society\frontend\package.json`.

---

## 0. Deliverable / definition of done

A running SPA where: `/login` → (portal chooser if >1 portal) → app shell with a responsive sidebar
whose nav tabs come from `me.modules`, a topbar with a working unread-notification bell badge, a
theme toggle, and logout. Each module route renders a placeholder `<LoadingState/>` (the module
sessions replace these). `must_change` and token-refresh flows work. `tsc --noEmit` and
`npm run build` are clean. See the checklist in §12.

---

## 1. Project setup

```bash
# in d:\society\frontend
npm create vite@latest . -- --template react-ts
npm install
npm install react-router-dom @tanstack/react-query @tanstack/react-query-devtools
npm install react-hook-form zod @hookform/resolvers
npm install lucide-react sonner
npm install class-variance-authority clsx tailwind-merge
# Tailwind v3 (shadcn-compatible)
npm install -D tailwindcss@3 postcss autoprefixer
npx tailwindcss init -p
```

### `vite.config.ts`
```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { port: 3000, strictPort: true },
});
```

### `tsconfig.json` — ensure the `@/*` path alias
Add under `compilerOptions`: `"baseUrl": ".", "paths": { "@/*": ["./src/*"] }`. Keep `"strict": true`.
(Also add the same alias to `tsconfig.app.json` if the Vite template split it out.)

### `.env` (at `d:\society\frontend\.env`)
```
VITE_API_BASE=http://localhost:8000
```

### Tailwind — `tailwind.config.ts`
Use the standard shadcn config. Set `darkMode: ["class"]`, `content: ["./index.html", "./src/**/*.{ts,tsx}"]`,
and the shadcn theme tokens (`extend.colors` mapped to CSS variables, `borderRadius`, container).
Follow the shadcn "Vite" install guide exactly.

### `src/index.css`
Tailwind directives + shadcn CSS variables for **both** light and dark:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%; --foreground: 222 47% 11%;
    --card: 0 0% 100%; --card-foreground: 222 47% 11%;
    --primary: 222 60% 52%;        /* brand blue */
    --primary-foreground: 0 0% 100%;
    --muted: 210 40% 96%; --muted-foreground: 215 16% 47%;
    --border: 214 32% 91%; --input: 214 32% 91%; --ring: 222 60% 52%;
    --destructive: 0 72% 51%; --destructive-foreground: 0 0% 100%;
    --radius: 0.6rem;
    /* add secondary, accent, popover per shadcn defaults */
  }
  .dark {
    --background: 222 47% 8%; --foreground: 210 40% 96%;
    --card: 222 45% 11%; --card-foreground: 210 40% 96%;
    --primary: 217 80% 62%; --primary-foreground: 222 47% 11%;
    --muted: 217 33% 17%; --muted-foreground: 215 20% 65%;
    --border: 217 33% 20%; --input: 217 33% 20%; --ring: 217 80% 62%;
    --destructive: 0 62% 50%; --destructive-foreground: 0 0% 98%;
    /* mirror the rest per shadcn defaults */
  }
  * { @apply border-border; }
  body { @apply bg-background text-foreground; }
}
```

### shadcn/ui init + components
```bash
npx shadcn@latest init      # style: default; base color: slate; CSS variables: yes
npx shadcn@latest add button card dialog sheet table badge tabs select input textarea \
  dropdown-menu skeleton sonner label form separator avatar alert scroll-area
```
This creates `src/components/ui/*` and `src/lib/utils.ts` (the `cn` helper). Keep `cn` there.

---

## 2. Folder structure to create

```
src/
  main.tsx                # ReactDOM root -> <Providers/> around <RouterProvider/>
  providers.tsx           # QueryClientProvider + AuthProvider + ThemeProvider + <Toaster/>
  router.tsx              # createBrowserRouter (public + protected branches)
  index.css
  components/
    ui/                   # shadcn (generated)
    common/               # Can, IfModule, StatusBadge, DataView, FormModal, ConfirmDialog,
                          #   EmptyState, Forbidden, LoadingState, PageHeader, SectionCard
    shell/                # AppShell, Sidebar, SidebarNav, NavItem, Topbar, BellButton, UserMenu, ThemeToggle
    notices/ complaints/ finance/ notifications/   # EMPTY dirs (module sessions fill these) — add a .gitkeep
  pages/
    auth/                 # LoginPage, ChoosePortalPage, ChangePasswordPage, ForgotPasswordPage
    NoticesPlaceholder etc are NOT needed — router points module routes at <LoadingState/> until modules land
  hooks/
    useAuth.ts useCan.ts useModule.ts useIsMobile.ts useTheme.ts
    useUnreadNotifications.ts   # working (badge source; §11)
    useComplaints.ts useHouseDues.ts   # STUBS (§5b) — module sessions replace bodies
  lib/
    api/ client.ts endpoints.ts queryKeys.ts
    auth/ tokenStore.ts AuthProvider.tsx guards.tsx ThemeProvider.tsx
    queryClient.ts format.ts notificationLinks.ts utils.ts (cn from shadcn)
  types/
    auth.ts common.ts notices.ts complaints.ts finance.ts notifications.ts
```

> The module sessions OWN `src/components/<module>/*`, `src/pages/<module>/*`, and the bodies of
> their endpoint fns / types. You create the empty `components/<module>` dirs and the stubbed
> endpoint fns + types so their imports resolve.

---

## 3. Types (`src/types/*`) — FROZEN shared contracts

### `src/types/common.ts`
```ts
export interface ApiErrorShape { code: string; message: string; details: Record<string, unknown>; }

export class ApiError extends Error {
  status: number; code: string; details: Record<string, unknown>;
  constructor(status: number, body: ApiErrorShape) {
    super(body?.message ?? "Request failed");
    this.status = status;
    this.code = body?.code ?? "unknown_error";
    this.details = body?.details ?? {};
  }
}

export interface Paginated<T> { items: T[]; total: number; }
```

### `src/types/auth.ts`
```ts
export type Portal = "admin" | "resident" | "platform";
export type PasswordState = "active" | "must_change";

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;            // "bearer"
  password_state: PasswordState;
  available_portals: Portal[];
}

export interface Me {
  user: { id: number; email: string; full_name: string | null; phone: string | null };
  active_society_id: number | null;
  available_portals: Portal[];
  active_portal: Portal | null;
  modules: string[];             // nav tabs: e.g. ["notices","complaints","finance","notifications"]
  landing: string | null;        // e.g. "notices"
  permissions: string[];         // dot-keys: e.g. ["notices.read","complaints.create",...]
  onboarding_required: boolean;
}
```

> The module DTOs (`notices.ts`, `complaints.ts`, `finance.ts`, `notifications.ts`) are defined in
> each module doc. In the foundation, create those files with a `// filled by <module> session`
> comment and any type the stubbed endpoint signatures reference (see §5). Keeping the files present
> prevents import errors.

---

## 4. API client + token store (`src/lib/api/client.ts`, `src/lib/auth/tokenStore.ts`)

### `tokenStore.ts` — single-writer token store
```ts
// Access token: in-memory + sessionStorage mirror (survives reload). Refresh: localStorage.
const ACCESS_KEY = "sm.access";     // sessionStorage
const REFRESH_KEY = "sm.refresh";   // localStorage
const PORTAL_KEY = "sm.portal";     // localStorage (chosen portal)

let accessToken: string | null = sessionStorage.getItem(ACCESS_KEY);

export const tokenStore = {
  getAccess: () => accessToken,
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  getPortal: () => localStorage.getItem(PORTAL_KEY),
  setPortal: (p: string | null) =>
    p ? localStorage.setItem(PORTAL_KEY, p) : localStorage.removeItem(PORTAL_KEY),
  // Always overwrite with the NEWEST tokens. Never keep an old refresh token.
  setTokens: (access: string, refresh?: string) => {
    accessToken = access;
    sessionStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    accessToken = null;
    sessionStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(PORTAL_KEY);
  },
};

// Cross-tab: keep in-memory access token in sync if another tab refreshes.
window.addEventListener("storage", (e) => {
  if (e.key === ACCESS_KEY) accessToken = e.newValue;
});
```
> Note: access token is mirrored to sessionStorage but the `storage` event only fires for
> localStorage across tabs. That's fine — the refresh token in localStorage is the cross-tab source
> of truth; a stale tab that 401s will refresh against the current refresh token.

### `client.ts` — `apiFetch` with single-flight refresh

**Algorithm (implement exactly):**
1. Build URL = `import.meta.env.VITE_API_BASE + path`.
2. Attach `Authorization: Bearer <access>` unless `opts.public === true`. Set
   `Content-Type: application/json` for JSON bodies (NOT for `FormData` — let the browser set it).
3. `fetch`. If response is `204`, return `undefined`. If `ok`, return parsed JSON (guard empty body).
4. On non-ok: parse the `{code,message,details}` envelope into `ApiError`. Then:
   - **401** and not `opts.public` and not already retried and this isn't the refresh call itself →
     call `refresh()` (single-flight, below); on success replay the ORIGINAL request **once** with the
     new access token. On refresh failure → `hardLogout()` and throw the ApiError.
   - **403** with `details.password_state === "must_change"` → redirect to `/change-password`
     (`window.location.assign`) and throw.
   - All other errors → throw the `ApiError` (callers/react-query handle it).

**Single-flight refresh (the core rotation-safety mechanism):**
```ts
let refreshPromise: Promise<void> | null = null;

async function refresh(): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const rt = tokenStore.getRefresh();
      if (!rt) throw new ApiError(401, { code: "authentication_error", message: "No session", details: {} });
      const res = await fetch(`${import.meta.env.VITE_API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => ({})));
      const data = await res.json();           // { access_token, refresh_token, token_type }
      tokenStore.setTokens(data.access_token, data.refresh_token);  // persist BEFORE any retry
    })();
    // clear the latch when done (success or fail) so future 401s can refresh again
    refreshPromise.finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

export function hardLogout() {
  tokenStore.clear();
  if (location.pathname !== "/login") location.assign("/login");
}
```
> **Why single-flight:** the refresh token is rotated on every use; replaying an already-rotated
> token makes the backend revoke ALL sessions (theft signal). Concurrent 401s must share ONE refresh
> call so the token is exchanged exactly once.

**`apiFetch` signature (frozen):**
```ts
export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;          // JSON-serialized unless it's FormData
  public?: boolean;        // skip auth header + skip refresh (login/refresh/forgot)
  _retried?: boolean;      // internal
}
export async function apiFetch<T = unknown>(path: string, opts?: ApiFetchOptions): Promise<T>;
```

---

## 5. Endpoints + query keys (STUBBED signatures — module sessions fill bodies)

### `src/lib/api/queryKeys.ts` (FROZEN)
```ts
export const queryKeys = {
  me: (portal: string | null) => ["me", portal] as const,
  notices: {
    list: (page: number) => ["notices", "list", page] as const,
    detail: (id: number) => ["notices", "detail", id] as const,
  },
  complaints: {
    list: (params: Record<string, unknown>) => ["complaints", "list", params] as const,
    detail: (id: number) => ["complaints", "detail", id] as const,
    categories: () => ["complaints", "categories"] as const,
  },
  finance: {
    dues: (houseId: number) => ["finance", "dues", houseId] as const,
  },
  notifications: {
    list: (page: number) => ["notifications", "list", page] as const,
    unread: () => ["notifications", "unread"] as const,
  },
};
```

### `src/lib/api/endpoints.ts` (signatures FROZEN; foundation writes auth bodies + stubs the rest)
Implement the **auth** functions fully. For module functions, write the correct signature with a
body that calls `apiFetch` with the right path (the module session verifies/uses them). Example:
```ts
import { apiFetch } from "./client";
import type { LoginResponse, Me, Portal } from "@/types/auth";

// --- AUTH (implement fully in foundation) ---
export const authApi = {
  login: (email: string, password: string) =>
    apiFetch<LoginResponse>("/auth/login", { method: "POST", public: true, body: { email, password } }),
  logout: (refresh_token: string) =>
    apiFetch<{ message: string }>("/auth/logout", { method: "POST", public: true, body: { refresh_token } }),
  changePassword: (current_password: string, new_password: string) =>
    apiFetch<{ message: string }>("/auth/change-password", { method: "POST", body: { current_password, new_password } }),
  forgotPassword: (email: string) =>
    apiFetch<{ message: string }>("/auth/forgot-password", { method: "POST", public: true, body: { email } }),
  me: (portal: Portal | null) =>
    apiFetch<Me>(`/me${portal ? `?portal=${encodeURIComponent(portal)}` : ""}`),
};

// --- MODULE endpoints: correct paths, filled/verified by module sessions ---
// notices, complaints, finance, notifications — see each module doc for exact shapes.
```
> Provide these module endpoint groups with the exact resident paths (from each module doc) so
> imports resolve: `noticesApi`, `complaintsApi`, `financeApi`, `notificationsApi`. Module sessions
> refine return types against their `types/<module>.ts`.

### 5b. Cross-module hook STUBS (ship these so all 4 module sessions are 100% independent)

`useComplaints` is consumed by the **Finance** module but implemented by the **Complaints** module.
To let all four module sessions build and typecheck in **any order, fully in parallel**, the
foundation ships a working **stub** for each cross-imported hook. The owning module session later
replaces the stub body — keeping the exact same signature, file path, and query key.

Create these three hook files now (stub bodies), so every `@/hooks/...` import resolves from day one:

**`src/hooks/useComplaints.ts`** (owned later by the Complaints session):
```ts
import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/queryKeys";
import { useAuth } from "@/hooks/useAuth";

export interface ComplaintListParams {
  page?: number; page_size?: number; status?: string; category_id?: number;
  house_id?: number; date_from?: string; date_to?: string; q?: string;
}

// STUB — Complaints session replaces the queryFn with complaintsApi.list(params).
// Keep this signature + queryKey + the `enabled` gate identical.
export function useComplaints(params: ComplaintListParams) {
  const { hasModule } = useAuth();
  return useQuery({
    queryKey: queryKeys.complaints.list(params as Record<string, unknown>),
    queryFn: async () => ({ items: [] as unknown[], total: 0 }), // stub: empty until Complaints lands
    enabled: hasModule("complaints"),
  });
}

// STUB — Complaints session replaces the queryFn with complaintsApi.categories.
export function useComplaintCategories() {
  return useQuery({
    queryKey: queryKeys.complaints.categories(),
    queryFn: async () => [] as unknown[], // stub
  });
}
```

**`src/hooks/useHouseDues.ts`** (owned later by the Finance session):
```ts
import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/queryKeys";

// STUB — Finance session replaces the queryFn with financeApi.houseDues(houseId).
export function useHouseDues(houseId: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.finance.dues(houseId as number),
    queryFn: async () => null as unknown,  // stub
    enabled: typeof houseId === "number",
  });
}
```

**`src/hooks/useUnreadNotifications.ts`** — already specified in §11 (ship a WORKING version, not a
stub, because the shell needs the real badge count in Stage 1). The Notifications session may extend
it but must keep the signature + query key.

> Result: Notices, Complaints, Finance, and Notifications sessions each import only foundation
> exports (real or stubbed) — none imports an unbuilt module's file. They can run **all at once**,
> merge in **any order**, and each keeps `tsc --noEmit` green on its own. When the owning session
> lands, it swaps the stub body for the real one; consumers need no change because the contract is
> identical.

---

## 6. Auth provider + guards (`src/lib/auth/`)

### `AuthProvider.tsx` — exposes (FROZEN via `useAuth`)
```ts
interface AuthContextValue {
  me: Me | null;
  status: "loading" | "authed" | "anon" | "must_change";
  portal: Portal | null;
  availablePortals: Portal[];
  has: (perm: string) => boolean;        // me?.permissions.includes(perm) ?? false
  hasModule: (mod: string) => boolean;   // me?.modules.includes(mod) ?? false
  login: (email: string, password: string) => Promise<LoginResponse>;
  setPortal: (p: Portal) => Promise<void>;  // persists, calls me(p), updates state
  logout: () => Promise<void>;
}
```
Behavior:
- **On mount:** if a refresh token exists → try `me(storedPortal)` (via a react-query `["me",portal]`
  query). A 401 triggers the client's refresh; if that fails → status `anon`. A 403 with
  `password_state==="must_change"` → status `must_change`. Else status `authed`.
- **`login`:** call `authApi.login`, `tokenStore.setTokens(...)`. If
  `password_state==="must_change"` → status `must_change` (navigation to `/change-password` handled
  by the guard/page). Else if `available_portals.length === 1` → `setPortal(that)`; if `>1` → leave
  `portal=null` (guard sends to `/choose-portal`).
- **`setPortal(p)`:** `tokenStore.setPortal(p)`, refetch `me(p)`, set state; caller navigates to
  `me.landing`.
- **`logout`:** `authApi.logout(refresh)` (ignore errors), `tokenStore.clear()`, reset query cache,
  go to `/login`.

### `guards.tsx` — `<RequireAuth>`
Wraps the protected branch. Redirect logic:
- status `loading` → render `<LoadingState/>` (full screen).
- status `anon` → `<Navigate to="/login" />`.
- status `must_change` → `<Navigate to="/change-password" />`.
- status `authed` but `me.active_portal === null` and `availablePortals.length > 1` →
  `<Navigate to="/choose-portal" />`.
- status `authed` and `me.onboarding_required` → for the resident portal this won't happen; if it
  does, render an `<EmptyState/>` explaining onboarding is pending (admin builds the wizard later).
- else render `<Outlet/>`.

### `ThemeProvider.tsx` + `useTheme.ts`
- Theme = `"light" | "dark"`. On first load read `localStorage["sm.theme"]`, else
  `window.matchMedia("(prefers-color-scheme: dark)")`. Apply by toggling the `dark` class on
  `document.documentElement`. `useTheme()` returns `{ theme, toggle }`, persists to localStorage.

---

## 7. Common components (`src/components/common/`) — FROZEN props

- **`Can`** — `props: { permission: string; children: ReactNode; fallback?: ReactNode }`. Renders
  children only if `useAuth().has(permission)`, else `fallback ?? null`.
- **`IfModule`** — `props: { module: string; children: ReactNode; fallback?: ReactNode }`. Same, via
  `hasModule`.
- **`StatusBadge`** — `props: { status: string }`. Maps a status string → shadcn `Badge` variant/class:
  `success` (green): `owned | resolved | paid | published | recorded`;
  `destructive` (red): `open | outstanding | overdue`;
  `warning` (amber): `in_progress | to_let | for_sale`;
  `info` (blue): `rented`;
  `muted` (gray): `empty | closed | withdrawn | archived | draft | voided`.
  Render the label with underscores → spaces, capitalized. (Add `success/warning/info/muted` as
  extra Badge variants via `cva`.)
- **`DataView<T>`** — responsive table. FROZEN props:
  ```ts
  interface Column<T> { header: string; cell: (row: T) => ReactNode; mobileLabel?: string; className?: string; }
  interface DataViewProps<T> {
    columns: Column<T>[];
    rows: T[];
    keyField: (row: T) => string | number;
    onRowClick?: (row: T) => void;
    empty?: ReactNode;      // shown when rows.length === 0
  }
  ```
  Desktop (≥768px via `useIsMobile`): shadcn `Table`. Mobile: a list of `Card`s, each row a card with
  `mobileLabel: value` pairs. Row click works in both.
- **`FormModal`** — responsive modal. FROZEN props:
  ```ts
  interface FormModalProps {
    open: boolean; onOpenChange: (o: boolean) => void;
    title: string; description?: string;
    children: ReactNode;          // form body
    footer: ReactNode;            // action buttons
  }
  ```
  Desktop: shadcn `Dialog`. Mobile: shadcn `Sheet` (side `bottom`). Same API.
- **`ConfirmDialog`** — `{ open, onOpenChange, title, description?, confirmLabel?, onConfirm, destructive? }`.
- **`EmptyState`** — `{ icon?: ReactNode; title: string; description?: string; action?: ReactNode }`.
- **`Forbidden`** — fixed content: lock icon + "You don't have access to this." Rendered when an
  action/page returns `403 permission_denied`.
- **`LoadingState`** — shadcn `Skeleton` rows; optional `{ rows?: number }`.
- **`PageHeader`** — `{ title: string; description?: string; actions?: ReactNode }`.
- **`SectionCard`** — `{ title?: string; description?: string; actions?: ReactNode; children }` wrapping shadcn `Card`.

### Error handling helper (`src/lib/format.ts` or a small `errors.ts`)
Provide `getErrorMessage(err: unknown): string` → if `err instanceof ApiError` return `err.message`,
else a generic fallback. Modules show it via `toast.error(getErrorMessage(e))` (sonner) or inline.

### `format.ts` (FROZEN)
```ts
export const fmtDate = (iso?: string | null) => /* "13 Jul 2026, 09:00" or "—" */;
export const fmtDateOnly = (iso?: string | null) => /* "13 Jul 2026" or "—" */;
export const formatCurrency = (v?: string | number | null) =>
  /* "₹2,500.00" — uses Intl.NumberFormat("en-IN", { style:"currency", currency:"INR" }); "—" if null */;
```

---

## 8. Permission hooks (`src/hooks/`)
- `useAuth()` → the AuthContext value.
- `useCan(perm: string) => boolean` → `useAuth().has(perm)`.
- `useModule(mod: string) => boolean` → `useAuth().hasModule(mod)`.
- `useIsMobile()` → boolean via `matchMedia("(max-width: 767px)")` (sync + listener).

---

## 9. The shell (`src/components/shell/`)

- **`AppShell`** (the layout route element): flex row. Desktop ≥1024px: persistent `<Sidebar/>` +
  `<main>` with `<Topbar/>` and a scrollable `<Outlet/>`. Mobile <1024px: sidebar hidden; a
  hamburger in the Topbar opens the sidebar inside a shadcn `Sheet` (left). Close the sheet on route
  change.
- **`Sidebar` / `SidebarNav` / `NavItem`:** brand ("Society Management") at top; nav list built from
  `me.modules` in this order, each wrapped in `<IfModule>`:
  | order | module key | label | route | lucide icon |
  |---|---|---|---|---|
  | 1 | `notices` | Notice Board | `/notices` | `Megaphone` |
  | 2 | `finance` | Financial | `/finance` | `Wallet` |
  | 3 | `complaints` | Complaints | `/complaints` | `Wrench` |
  | 4 | `notifications` | Notifications | `/notifications` | `Bell` |

  The Notifications nav item shows the unread count from `useUnreadNotifications()` as a small badge.
  Footer: `<UserMenu/>`.
- **`Topbar`:** current page title (derive from route), a `<BellButton/>` (unread badge, click →
  `/notifications`), a `<ThemeToggle/>`, and on mobile the hamburger. Show society context
  (`me.user.full_name` / society) as available.
- **`UserMenu`** (shadcn `DropdownMenu`): user name + email; **Switch portal** (only if
  `availablePortals.length > 1` → navigate `/choose-portal`); **Logout**.
- **`BellButton`** and the sidebar badge both read `useUnreadNotifications()` — do NOT call the API
  directly here; use the hook (its signature is frozen in §11, implemented by the Notifications
  session; foundation ships a working version per §11).
- **`ThemeToggle`:** `useTheme()`; sun/moon lucide icons.

---

## 10. Router + auth pages

### `router.tsx`
```
createBrowserRouter([
  { path: "/login", element: <LoginPage/> },
  { path: "/choose-portal", element: <ChoosePortalPage/> },
  { path: "/change-password", element: <ChangePasswordPage/> },
  { path: "/forgot-password", element: <ForgotPasswordPage/> },
  {
    element: <RequireAuth><AppShell/></RequireAuth>,
    children: [
      { path: "/", element: <RootRedirect/> },     // navigate to me.landing (fallback "/notices")
      { path: "/notices", element: <LoadingState/> },        // replaced by Notices session
      { path: "/notices/:id", element: <LoadingState/> },
      { path: "/finance", element: <LoadingState/> },        // replaced by Finance session
      { path: "/complaints", element: <LoadingState/> },     // replaced by Complaints session
      { path: "/complaints/:id", element: <LoadingState/> },
      { path: "/notifications", element: <LoadingState/> },  // replaced by Notifications session
    ],
  },
  { path: "*", element: <Navigate to="/" replace/> },
])
```
> Module sessions swap their `<LoadingState/>` placeholders for their real page components and add
> nothing else to the router structure.

### Auth pages (build all four fully)
- **`LoginPage`:** email + password (shadcn `Form`/`Input`). On submit call `login`. On success:
  `must_change` → `/change-password`; single portal → `/{landing}`; multi portal → `/choose-portal`.
  On `ApiError` (401 `authentication_error`) show inline "Invalid email or password." Include a
  "Forgot password?" link → `/forgot-password`.
- **`ChoosePortalPage`:** list `availablePortals` as cards (resident 🏠 / admin 🛠). On pick →
  `setPortal(p)` then `navigate(me.landing)`. If not logged in → `/login`.
- **`ChangePasswordPage`:** `current_password` + `new_password` (+ confirm). Client-validate policy
  (≥8 chars, ≥1 letter + 1 digit, differs from current) to match the server. On success the server
  revokes sessions → show "Password changed, please log in again," `tokenStore.clear()`, go `/login`.
  This page is reachable while `must_change`.
- **`ForgotPasswordPage`:** email only → `authApi.forgotPassword`. Always show the generic success
  message regardless of response. Link back to `/login`.

---

## 11. Notifications badge hook (`src/hooks/useUnreadNotifications.ts`) — FROZEN signature

The shell needs this in Stage 1, and the Notifications session also uses it. **Foundation ships a
working implementation**; the Notifications session may extend but must keep the signature + query key.
```ts
// returns the unread count for the bell + sidebar badge
export function useUnreadNotifications(): { count: number; isLoading: boolean };
// Implementation: useQuery({ queryKey: queryKeys.notifications.unread(),
//   queryFn: () => apiFetch<{unread_count:number}>("/notifications/unread-count"),
//   refetchInterval: 30000, enabled: hasModule("notifications") })
// Return { count: data?.unread_count ?? 0, isLoading }
```
Also ship `src/lib/notificationLinks.ts` (FROZEN):
```ts
import type { /* Notification */ } from "@/types/notifications";
// Map a notification to an in-app route. entity_type: "complaint"|"notice"|"house"|null
export function notificationLinks(n: { entity_type: string | null; entity_id: number | null }): string {
  switch (n.entity_type) {
    case "notice":    return n.entity_id ? `/notices/${n.entity_id}` : "/notices";
    case "complaint": return n.entity_id ? `/complaints/${n.entity_id}` : "/complaints";
    case "house":     return "/finance";   // maintenance_due deep-links to the Financial page
    default:          return "/notifications";
  }
}
```

---

## 12. Self-verification checklist (must pass before done)

Start backend: `cd d:\society && docker-compose up` (confirm `GET http://localhost:8000/health`).
Then `cd d:\society\frontend && npm run dev` (serves on **:3000**).

- [ ] **Login (single portal):** a resident account logs in and lands on `/notices` (placeholder).
- [ ] **Login (multi portal):** a dual-portal account → `/choose-portal` → pick resident → shell.
- [ ] **Nav from modules:** sidebar shows only tabs in `me.modules`; each is an `<IfModule>`.
- [ ] **Bell badge:** unread count shows on the topbar bell and the sidebar Notifications item; polls.
- [ ] **Token refresh:** delete the `sm.access` sessionStorage key (simulate expiry), trigger a
      request → exactly ONE `/auth/refresh` call, request retries and succeeds. Corrupt the refresh
      token → next 401 hard-logs-out to `/login`.
- [ ] **must_change:** run forgot-password for a test account, log in with the temp password →
      redirected to `/change-password`, shell blocked; after change → back to `/login`.
- [ ] **Theme:** toggle light/dark; persists across reload.
- [ ] **Responsive:** at <1024px the sidebar opens as a Sheet via the hamburger; closes on nav.
- [ ] **Guards:** removing a module from a mocked `me` hides its tab; a forced `403` renders `<Forbidden/>`.
- [ ] `npx tsc --noEmit` clean; `npm run build` clean.

**Do not modify these frozen items after this stage:** the signatures in §3, §4 (`apiFetch`), §5
(`queryKeys`, endpoint fn names/paths), §6 (`useAuth` shape), §7 (component props), §11
(`useUnreadNotifications`, `notificationLinks`). Module sessions import them as-is.
