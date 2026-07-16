# Module: Notice Board (Notices) — the LANDING page

Resident-facing notice feed + detail. This is the app's landing route (`me.landing === "notices"`).

> **Prerequisites:** `00-foundation.md` is built and merged. You may import ONLY the frozen foundation
> exports (see below). **Do NOT modify any foundation file** or redefine a shared contract. You own
> `src/pages/notices/*`, `src/components/notices/*`, the body of `src/types/notices.ts`, and the
> `noticesApi` bodies + notices query keys.
>
> API source of truth: `d:\society\docs\api\notice-board.md`. Base path `/notices`.

## Foundation imports you may use
`apiFetch` (`@/lib/api/client`), `queryKeys.notices.*` (`@/lib/api/queryKeys`), `useAuth`/`useCan`/
`useModule`, `<Can>`, `<IfModule>`, `<DataView>`, `<PageHeader>`, `<SectionCard>`, `<EmptyState>`,
`<Forbidden>`, `<LoadingState>`, `<StatusBadge>`, `fmtDate`/`fmtDateOnly`, `getErrorMessage`, sonner
`toast`, shadcn `Card`/`Badge`/`Button`/`Skeleton`.

---

## 1. Scope & routes
- Route `/notices` → `NoticesPage` (feed). Swap the router placeholder for this component.
- Route `/notices/:id` → `NoticeDetailPage`.
- Files:
  - `src/pages/notices/NoticesPage.tsx`
  - `src/pages/notices/NoticeDetailPage.tsx`
  - `src/components/notices/NoticeListItem.tsx`
  - `src/components/notices/NoticeBody.tsx` (sanitized HTML renderer)

## 2. Permission / module gating
- Whole page under `<IfModule module="notices">` (nav already gates it; also guard the page body).
- Resident permission is **`notices.read`** (they always get the active feed; `status`/`scope` params
  are ignored for residents — do not send them).
- No publish/receipt actions on the resident portal (those need `notices.publish` /
  `notices.read_receipts`, which residents lack). If a detail returns `403`, render `<Forbidden/>`.

## 3. Endpoints (exact — from notice-board.md)

### `GET /notices?page=&page_size=` → list  (perm `notices.read`)
Response:
```ts
interface NoticeListResponse {
  items: NoticeListItem[];
  total: number;
  unread_count: number;   // active notices caller hasn't read, independent of page
}
interface NoticeListItem {
  id: number;
  title: string;
  status: "draft" | "published" | "withdrawn";   // residents only ever see "published"
  is_pinned: boolean;
  published_at: string | null;
  expires_at: string | null;
  last_edited_at: string | null;
  attachment_count: number;
  is_read: boolean;
  created_at: string;
  updated_at: string;
}
```
Ordering is **pinned first, then newest `published_at`** — the backend already returns them ordered;
preserve that order.

### `GET /notices/{id}` → detail, **marks read as a side effect**  (perm `notices.read`)
```ts
interface NoticeDetail {
  id: number;
  title: string;
  body: string;                 // sanitized HTML (safe tag allow-list, see §5)
  status: "draft" | "published" | "withdrawn";
  is_pinned: boolean;
  published_at: string | null;
  expires_at: string | null;
  last_edited_at: string | null;
  created_by: number;
  withdrawn_at: string | null;
  withdrawn_by: number | null;
  is_read: boolean;             // always true after this call
  created_at: string;
  updated_at: string;
  attachments: NoticeAttachment[];
}
interface NoticeAttachment {
  id: number;
  vault_document_id: number;
  preview_url: string | null;
  download_url: string | null;  // both null if Vault can't produce a link right now
  created_at: string;
}
```
- **404** `"Notice not found."` if it doesn't exist OR is a draft/withdrawn hidden from residents.
  Render an `<EmptyState/>` "Notice not found" for a 404.

### `POST /notices/read-all` → `204 No Content`  (perm `notices.read`)
Marks every active notice read for the caller.

Put these in `noticesApi` (foundation stubbed the paths):
```ts
export const noticesApi = {
  list: (page = 1, pageSize = 20) =>
    apiFetch<NoticeListResponse>(`/notices?page=${page}&page_size=${pageSize}`),
  detail: (id: number) => apiFetch<NoticeDetail>(`/notices/${id}`),
  readAll: () => apiFetch<void>("/notices/read-all", { method: "POST" }),
};
```

## 4. Components & behavior

### `NoticesPage`
- `useQuery(queryKeys.notices.list(page), () => noticesApi.list(page))`.
- `<PageHeader title="Notice Board" actions={<MarkAllReadButton/>}/>`.
- **Mark all read** button: `useMutation(noticesApi.readAll)`; on success invalidate
  `queryKeys.notices.list(*)` AND `queryKeys.notifications.unread()` (a published-notice notification
  clears when read) → `toast.success("All notices marked read")`. Disable if `unread_count === 0`.
- Render the list with `NoticeListItem` cards (this is a feed, not a table — do **not** use DataView
  here; use a vertical stack of cards). Each item: pin indicator (📌 / `Pin` icon) if `is_pinned`,
  title, `fmtDate(published_at)`, `attachment_count` if >0, and an **unread dot/Badge** when
  `is_read === false`. Clicking a card → `navigate('/notices/'+id)`.
- Show `unread_count` somewhere in the header (e.g. "3 unread").
- States: loading → `<LoadingState/>`; empty (`items.length === 0`) → `<EmptyState title="No notices yet"/>`.
- Pagination: simple Prev/Next using `total` vs `page_size` (page size 20).

### `NoticeDetailPage`
- `useQuery(queryKeys.notices.detail(id), () => noticesApi.detail(id))`.
- **Opening marks read** — after the query succeeds, invalidate `queryKeys.notices.list(*)` and
  `queryKeys.notifications.unread()` so the feed's unread dot and the bell badge update. (Do this in
  an `onSuccess`/`useEffect` guarded to run once.)
- Layout: back button → `/notices`; title + pin indicator; `fmtDate(published_at)`; `StatusBadge`
  (will be `published`); the sanitized body via `<NoticeBody html={notice.body}/>`; attachments as a
  list of download chips (use `download_url` when non-null, else show the filename disabled).
- 404 → `<EmptyState title="Notice not found"/>` with a back link.

### `NoticeBody` — sanitized HTML renderer
The body is **already sanitized server-side** (allow-list: `p br span strong b em i u s ul ol li a
h1–h4 blockquote code pre hr`; only `a` keeps `href`/`title`; only http/https/mailto). Still,
**re-sanitize client-side before `dangerouslySetInnerHTML`** as defense-in-depth.
- Install `dompurify`: `npm install dompurify && npm install -D @types/dompurify`.
- `const clean = DOMPurify.sanitize(html, { ALLOWED_TAGS: [...], ALLOWED_ATTR: ["href","title"] })`.
- Render inside a `div.prose`-style wrapper (Tailwind typography optional; if not installed, add
  minimal spacing classes). Open `a` links in a new tab with `rel="noopener noreferrer"`.

## 5. Mobile behavior
- Feed is a single-column card stack on all sizes (already mobile-friendly).
- Detail: full-width, readable line length (`max-w-prose`), attachments wrap.
- No modals in this module.

## 6. Query keys & invalidation (summary)
- Read: `queryKeys.notices.list(page)`, `queryKeys.notices.detail(id)`.
- `readAll` mutation → invalidate `notices.list(*)` + `notifications.unread()`.
- Opening a detail (marks read) → invalidate `notices.list(*)` + `notifications.unread()`.

## 7. Cross-links (for the Notifications module)
Expose `/notices/:id`. A `notice` notification deep-links here via `notificationLinks` (already
mapped in the foundation). No action needed beyond having the route live.

## 8. Self-verification checklist
Backend on `:8000`, `npm run dev` on `:3000`, logged in as a resident:
- [ ] `/notices` lists active notices, **pinned first then newest**; unread items show an unread mark.
- [ ] Header shows the correct `unread_count`.
- [ ] Opening a notice renders sanitized rich text; a `<script>`/`onclick` in the body never executes.
- [ ] After opening, that notice loses its unread mark on the feed and the bell badge decrements.
- [ ] "Mark all read" clears all unread marks and zeroes the badge.
- [ ] A `/notices/:id` for a nonexistent/hidden id shows "Notice not found" (404 handled).
- [ ] Attachments with a `download_url` are clickable; null ones are shown disabled.
- [ ] Mobile (<768px): feed and detail read well; nav drawer works.
- [ ] `npx tsc --noEmit` and `npm run build` clean.
