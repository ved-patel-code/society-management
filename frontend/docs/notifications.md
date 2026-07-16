# Module: Notifications

The unread-only notification feed, plus the unread badge source that the shell already consumes. This
module **owns `useUnreadNotifications`** (the badge hook the foundation shipped) and the deep-linking
from each notification to its target screen.

> **Prerequisites:** `00-foundation.md` built and merged. Import ONLY frozen foundation exports; do
> NOT modify foundation files EXCEPT you may finalize `src/hooks/useUnreadNotifications.ts` — but you
> MUST keep its frozen signature and query key. You own `src/pages/NotificationsPage.tsx`,
> `src/components/notifications/*`, `src/types/notifications.ts` body, and `notificationsApi` bodies +
> notifications query keys.
>
> API source of truth: `d:\society\docs\api\notifications.md`. Base path `/notifications`.

## Foundation imports
`apiFetch`, `queryKeys.notifications.*`, `useAuth`/`useModule`, `<IfModule>`, `<PageHeader>`,
`<EmptyState>`, `<Forbidden>`, `<LoadingState>`, `fmtDate`, `getErrorMessage`, `notificationLinks`
(`@/lib/notificationLinks`), sonner `toast`, shadcn `Card`/`Button`/`Badge`.

---

## Key model facts (from notifications.md)
- **The feed is unread-only.** There is no "read history" — once read, a notification disappears from
  every response. Treat it as a to-do list.
- **`unread_count` is the total across the whole feed**, not just the current page.
- Reading a complaint/notice detail (in those modules) **auto-clears** its related notification
  server-side — so after visiting those, the badge should refresh (those modules invalidate
  `queryKeys.notifications.unread()`; you don't need to do anything for that).
- This module requires `finance` enabled server-side, but that's transparent to the client.

## Permission / module gating
- Page under `<IfModule module="notifications">`. Resident permission: **`notifications.read`**.

---

## Endpoints (exact — from notifications.md)

### Types (`src/types/notifications.ts`)
```ts
export type NotificationType =
  | "complaint_new" | "complaint_update" | "complaint_withdrawn" | "notice" | "maintenance_due";

export interface AppNotification {
  id: number;
  type: NotificationType;
  title: string;
  body: string;
  payload: Record<string, unknown>;          // shape varies by type (see below)
  entity_type: "complaint" | "notice" | "house" | null;
  entity_id: number | null;
  created_at: string;
}
export interface NotificationsResponse {
  items: AppNotification[];
  unread_count: number;   // total across whole feed
  page: number;
  page_size: number;
}
```
`payload` by `type` (for optional richer rendering; not required):
- `complaint_new`: `{complaint_id, reference, house_id, category_id}`
- `complaint_withdrawn`: `{complaint_id, reference, house_id}`
- `complaint_update`: `{complaint_id, reference, from_status, to_status, note}`
- `notice`: `{notice_id, title, published_at}`
- `maintenance_due`: `{house_id, outstanding_total (decimal string), months_outstanding, anchor_due_date}`

### `GET /notifications?page=&page_size=` → `NotificationsResponse`  (perm `notifications.read`)
### `GET /notifications/unread-count` → `{ unread_count: number }`  (perm `notifications.read`)
### `POST /notifications/{id}/read` → `{ cleared: 0 | 1 }`  (own only; idempotent)
- 404 `"Notification not found."` if not yours / nonexistent (identical response — no probing).
### `POST /notifications/read-all` → `{ cleared: number }`

`notificationsApi` (foundation stubbed the paths):
```ts
export const notificationsApi = {
  list: (page = 1, pageSize = 20) =>
    apiFetch<NotificationsResponse>(`/notifications?page=${page}&page_size=${pageSize}`),
  unreadCount: () => apiFetch<{ unread_count: number }>("/notifications/unread-count"),
  markRead: (id: number) => apiFetch<{ cleared: number }>(`/notifications/${id}/read`, { method: "POST" }),
  markAllRead: () => apiFetch<{ cleared: number }>("/notifications/read-all", { method: "POST" }),
};
```

### `useUnreadNotifications` — FROZEN (finalize the foundation stub; keep signature + key)
```ts
export function useUnreadNotifications(): { count: number; isLoading: boolean } {
  const { hasModule } = useAuth();
  const q = useQuery({
    queryKey: queryKeys.notifications.unread(),
    queryFn: notificationsApi.unreadCount,
    refetchInterval: 30000,
    enabled: hasModule("notifications"),
  });
  return { count: q.data?.unread_count ?? 0, isLoading: q.isLoading };
}
```

---

## Components & behavior

Files:
- `src/pages/NotificationsPage.tsx`
- `src/components/notifications/NotificationItem.tsx`

### `NotificationsPage`
- `useQuery(queryKeys.notifications.list(page), () => notificationsApi.list(page))`.
- `<PageHeader title="Notifications" actions={<MarkAllReadButton/>}/>`.
- **Mark all read**: `useMutation(notificationsApi.markAllRead)`; on success invalidate
  `notifications.list(*)` + `notifications.unread()`; `toast.success("All caught up")`. Disable when
  `unread_count === 0`.
- Render `items[]` as `NotificationItem` cards, newest first (as returned).
- Empty → `<EmptyState title="You're all caught up 🎉"/>`.
- Pagination via `page`/`page_size` + `unread_count`.

### `NotificationItem`
- Icon by `type` (lucide): `complaint_new`→`Wrench`, `complaint_update`→`RefreshCw`,
  `complaint_withdrawn`→`Undo2`, `notice`→`Megaphone`, `maintenance_due`→`Wallet`.
- `title`, `body`, `fmtDate(created_at)`.
- **Click the item** → `navigate(notificationLinks(n))` (foundation resolver:
  notice→`/notices/:id`, complaint→`/complaints/:id`, house/maintenance_due→`/finance`). Navigating to
  a complaint/notice detail will auto-clear the notification server-side; still, optimistically call
  `markRead(n.id)` on click so it disappears immediately, then invalidate `notifications.list(*)` +
  `notifications.unread()`.
- A **"Mark read"** button (per item) → `notificationsApi.markRead(id)` → invalidate
  `notifications.list(*)` + `notifications.unread()`.

## Mobile behavior
- Single-column card list (already mobile-friendly). Full-width tap target per item; the per-item
  "Mark read" is an icon button so the row stays tappable.

## Query keys & invalidation
- Reads: `notifications.list(page)`, `notifications.unread()`.
- `markRead` / `markAllRead` / item-click → invalidate BOTH `notifications.list(*)` and
  `notifications.unread()` (the latter drives the shell bell + sidebar badge).

## Cross-links (this module is the consumer)
Uses `notificationLinks` to jump to the other modules' routes. Those routes (`/notices/:id`,
`/complaints/:id`, `/finance`) must exist — they do once those modules (or the foundation
placeholders) are in the router.

## Self-verification checklist
Backend `:8000`, `npm run dev` `:3000`, logged in as a resident with some unread notifications
(e.g. trigger one by having an admin publish a notice, or use seeded data):
- [ ] `/notifications` lists unread items, newest first, with the right icon per type.
- [ ] The header count and the shell bell badge match `unread_count`.
- [ ] Clicking a `notice` item goes to `/notices/:id`; a `complaint_*` item to `/complaints/:id`; a
      `maintenance_due` item to `/finance`.
- [ ] Clicking an item removes it from the feed and decrements the bell badge.
- [ ] "Mark all read" empties the feed and zeroes the badge; the button then disables.
- [ ] Reading a complaint/notice detail (in those modules) also clears its notification and updates
      the badge (server auto-clears; badge refetches).
- [ ] Empty feed shows "You're all caught up".
- [ ] Mobile: card list is tappable; per-item mark-read works.
- [ ] `npx tsc --noEmit` and `npm run build` clean.
