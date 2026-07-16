# Module: Complaints

Residents raise house-scoped complaints, track their status timeline, edit/withdraw while open, and
attach report photos. This module also exposes `useComplaints(...)`, which the **Finance** module
reuses to show a house's complaints — keep that hook's signature stable.

> **Prerequisites:** `00-foundation.md` built and merged. Import ONLY frozen foundation exports. Do
> NOT modify foundation files. You own `src/pages/complaints/*`, `src/components/complaints/*`,
> `src/types/complaints.ts` body, `complaintsApi` bodies + complaints query keys, and the
> `useComplaints` / `useComplaintCategories` hooks.
>
> API source of truth: `d:\society\docs\api\complaints.md`. Base path `/complaints`.

## Foundation imports
`apiFetch`, `queryKeys.complaints.*`, `useAuth`/`useCan`/`useModule`, `<Can>`, `<IfModule>`,
`<DataView>`, `<FormModal>`, `<ConfirmDialog>`, `<PageHeader>`, `<SectionCard>`, `<EmptyState>`,
`<Forbidden>`, `<LoadingState>`, `<StatusBadge>`, `fmtDate`, `getErrorMessage`, sonner `toast`,
shadcn `Select`/`Input`/`Textarea`/`Button`/`Badge`/`Card`.

---

## 1. Scope & routes
- `/complaints` → `ComplaintsPage` (list + filters + Raise button). Replace router placeholder.
- `/complaints/:id` → `ComplaintDetailPage` (detail + timeline + images + resident actions).
- Files:
  - `src/pages/complaints/ComplaintsPage.tsx`, `src/pages/complaints/ComplaintDetailPage.tsx`
  - `src/components/complaints/ComplaintTable.tsx` (uses `<DataView>`)
  - `src/components/complaints/RaiseComplaintModal.tsx` (uses `<FormModal>`)
  - `src/components/complaints/EditComplaintModal.tsx`
  - `src/components/complaints/ComplaintTimeline.tsx`
  - `src/components/complaints/ReportImages.tsx` (upload/delete + thumbnails)
  - `src/components/complaints/StatusFilter.tsx`
  - `src/hooks/useComplaints.ts` + `useComplaintCategories` — **replace the foundation STUB body**
    (foundation §5b shipped a stub with empty results). Keep the exact signature, query key, and
    `enabled` gate; just swap the `queryFn` to call `complaintsApi.list` / `complaintsApi.categories`.
    Finance imports these — do not rename.

## 2. Permission / module gating
- Page under `<IfModule module="complaints">`.
- Resident permissions: **`complaints.read`** (list + detail; scoped to houses they own — the backend
  enforces this) and **`complaints.create`** (raise, edit while open, withdraw, add/remove report
  images).
- Resident sees **only complaints on a house they own**; owning zero houses → empty list (that's
  expected, not an error).
- **Admin-only status actions must NOT appear on the resident portal.** The only status action a
  resident has is **withdraw** (from `open`). Do not render start-progress / resolve / close / reopen
  — those need `complaints.update_status` (gate with `<Can permission="complaints.update_status">`,
  which will be false for residents; effectively hidden).
- Report image endpoints require the `vault` module server-side; if an image call returns
  `403 module_disabled` (`vault`), disable the image UI and toast a friendly message.

## 3. Endpoints (exact — from complaints.md)

### Types (`src/types/complaints.ts`)
```ts
export type ComplaintStatus =
  | "open" | "in_progress" | "resolved" | "closed" | "archived" | "withdrawn";

export interface Category { id: number; name: string; is_active: boolean; is_system: boolean; }

export interface StatusHistory {
  id: number;
  from_status: ComplaintStatus | null;   // null for the initial "open" entry
  to_status: ComplaintStatus;
  note: string | null;
  changed_by: number | null;             // null for system-triggered archiving
  created_at: string;
}
export interface ComplaintImage {
  id: number;
  kind: "report" | "proof";
  vault_document_id: number;
  preview_url: string | null;
  created_at: string;
}
export interface ComplaintListItem {
  id: number; reference: string; title: string; status: ComplaintStatus;
  category_id: number; category_name: string;
  house_id: number; house_display_code: string | null;
  report_image_count: number; proof_image_count: number;
  created_at: string; updated_at: string;
}
export interface ComplaintDetail {
  id: number; reference: string;
  house_id: number; house_display_code: string | null;
  raised_by: number;
  category_id: number; category_name: string;
  title: string; description: string;
  status: ComplaintStatus;
  resolved_at: string | null; closed_at: string | null;
  archived_at: string | null; withdrawn_at: string | null;
  created_at: string; updated_at: string;
  timeline: StatusHistory[];
  images: ComplaintImage[];
}
export interface ComplaintListParams {
  page?: number; page_size?: number;
  status?: ComplaintStatus; category_id?: number; house_id?: number;
  date_from?: string; date_to?: string; q?: string;
}
```

### `GET /complaints/categories` → `Category[]` (active only, sorted by name)  (perm `complaints.read`)
### `GET /complaints?…` → `{ items: ComplaintListItem[]; total: number }`  (perm `complaints.read`)
Filters: `page,page_size,status,category_id,house_id,date_from,date_to,q`. Newest-first. Resident
visibility (own houses) enforced server-side even if `house_id` is passed.
### `GET /complaints/{id}` → `ComplaintDetail`  (perm `complaints.read`)
- 404 `"Complaint not found."`; 403 `"You may only view complaints on a house you own."` → `<Forbidden/>`.
### `POST /complaints` → `ComplaintDetail`  (perm `complaints.create`)
Body: `{ category_id, title, description, house_id? }`. Omit `house_id` if the resident owns exactly
one house (inferred). If they own >1, `house_id` is required (see error below).
- 403 `"Only a current house owner may raise a complaint."` (owns none)
- 422 `"You own several houses; specify house_id."` with `details.owned_house_ids: number[]` — when
  this happens, re-open the form asking the user to pick a house from `owned_house_ids`.
### `PATCH /complaints/{id}` → `ComplaintDetail`  (perm `complaints.create`; raiser + only while `open`)
Body: any of `{ title?, description?, category_id? }` (≥1 required).
- 409 `"This complaint is locked once it is in progress."` if not open.
### `POST /complaints/{id}/withdraw` → `ComplaintDetail`  (perm `complaints.create`; raiser + only `open`)
- 409 `"Only an open complaint can be withdrawn."`
### `POST /complaints/{id}/images` → `ComplaintImage`  (perm `complaints.create`; raiser + `open`; needs vault)
`multipart/form-data` field **`file`**. Capped at `max_report_images` (default 2 — you won't have the
config endpoint as a resident; just handle the 409 below and disable "Add" once 2 report images exist).
- 409 `"Report image limit reached for this complaint."`
- 409 `"Report images can only be changed while the complaint is open."`
- 415 `file_type_not_allowed`, 413 `storage_quota_exceeded` → toast the message.
### `DELETE /complaints/{id}/images/{imageId}` → `204`  (perm `complaints.create`; raiser + `open`)

`complaintsApi` (foundation stubbed the paths):
```ts
export const complaintsApi = {
  categories: () => apiFetch<Category[]>("/complaints/categories"),
  list: (p: ComplaintListParams) => apiFetch<{ items: ComplaintListItem[]; total: number }>(
    `/complaints?${new URLSearchParams(/* only defined params */).toString()}`),
  detail: (id: number) => apiFetch<ComplaintDetail>(`/complaints/${id}`),
  create: (body: { category_id: number; title: string; description: string; house_id?: number }) =>
    apiFetch<ComplaintDetail>("/complaints", { method: "POST", body }),
  update: (id: number, body: { title?: string; description?: string; category_id?: number }) =>
    apiFetch<ComplaintDetail>(`/complaints/${id}`, { method: "PATCH", body }),
  withdraw: (id: number) => apiFetch<ComplaintDetail>(`/complaints/${id}/withdraw`, { method: "POST" }),
  addImage: (id: number, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return apiFetch<ComplaintImage>(`/complaints/${id}/images`, { method: "POST", body: fd });
  },
  deleteImage: (id: number, imageId: number) =>
    apiFetch<void>(`/complaints/${id}/images/${imageId}`, { method: "DELETE" }),
};
```
> Build the query string from only the defined params (skip undefined/empty).

### `useComplaints` — FROZEN signature (Finance reuses this)
```ts
export function useComplaints(params: ComplaintListParams) {
  return useQuery({
    queryKey: queryKeys.complaints.list(params as Record<string, unknown>),
    queryFn: () => complaintsApi.list(params),
    enabled: /* hasModule("complaints") */,
  });
}
export function useComplaintCategories() {
  return useQuery({ queryKey: queryKeys.complaints.categories(), queryFn: complaintsApi.categories });
}
```

## 4. Components & behavior

### `ComplaintsPage`
- `<PageHeader title="Complaints" actions={<Can permission="complaints.create"><Button>Raise complaint</Button></Can>}/>`.
- **Status filter** (`StatusFilter`): a shadcn `Select` over `open/in_progress/resolved/closed/withdrawn/archived`
  + "All". Optional `q` search input. Update query params → refetch.
- `ComplaintTable` via `<DataView>` columns: Reference · Title · House (`house_display_code`) ·
  Category · Status (`<StatusBadge>`) · Updated (`fmtDate`). `onRowClick` → `/complaints/:id`.
  `mobileLabel` set per column so it renders as labeled cards on mobile.
- Empty → `<EmptyState title="No complaints yet" description="Raise one to get started."/>`.
- Pagination via `total` / page_size.

### `RaiseComplaintModal` (`<FormModal>`)
- Category `Select` (from `useComplaintCategories`), Title `Input`, Description `Textarea`.
- If the resident owns >1 house you'll learn it only from the 422 response — on that error, show a
  house `Select` populated from `details.owned_house_ids` and resubmit with `house_id`. (Most
  residents own one house and never see this.)
- Submit → `complaintsApi.create` → on success invalidate `complaints.list(*)`,
  `toast.success`, close modal, optionally navigate to the new detail.

### `ComplaintDetailPage`
- Back button → `/complaints`. Header: title, `reference · house_display_code · category_name`,
  `<StatusBadge>`.
- Description paragraph.
- **Resident actions row** (only when `raised_by === me.user.id`):
  - While `status === "open"`: **Edit** (opens `EditComplaintModal`, PATCH) and **Withdraw**
    (`ConfirmDialog` → `complaintsApi.withdraw`).
  - Wrap each in `<Can permission="complaints.create">`.
  - Do NOT render any admin transition buttons (guarded by `complaints.update_status`).
- `ComplaintTimeline`: render `timeline[]` as a vertical timeline — each entry shows
  `from_status → to_status` (or just `to_status` for the initial), `note` if present, `fmtDate`.
- `ReportImages`: show `images.filter(kind === "report")` as thumbnails (`preview_url`). While
  `status === "open"` and viewer is the raiser: an **Add photo** file input (accept images) →
  `addImage`; a delete (X) on each report thumbnail → `deleteImage` (ConfirmDialog). Disable Add once
  2 report images exist or if a 409 limit error returns. Proof images (`kind === "proof"`), if any,
  are shown read-only.
- On any mutation success → invalidate `complaints.detail(id)` (+ `complaints.list(*)` for
  status-changing ones like withdraw).
- 404 → `<EmptyState title="Complaint not found"/>`; 403 → `<Forbidden/>`.

## 5. Mobile behavior
- List uses `<DataView>` → labeled cards on mobile.
- Raise/Edit modals use `<FormModal>` → bottom `Sheet` on mobile.
- Detail: single column; image thumbnails wrap; action buttons stack full-width.

## 6. Query keys & invalidation
- Reads: `complaints.list(params)`, `complaints.detail(id)`, `complaints.categories()`.
- create → invalidate `complaints.list(*)`.
- update → invalidate `complaints.detail(id)` (+ `list(*)` if it affects list fields).
- withdraw → invalidate `complaints.detail(id)` + `complaints.list(*)`.
- add/delete image → invalidate `complaints.detail(id)`.

## 7. Cross-links
Expose `/complaints/:id`. `complaint_new` / `complaint_update` / `complaint_withdrawn` notifications
deep-link here (mapped in foundation `notificationLinks`). Reading a complaint detail also clears its
related notification server-side — after a successful detail load, invalidate
`queryKeys.notifications.unread()` so the bell badge updates.

## 8. Self-verification checklist
Backend `:8000`, `npm run dev` `:3000`, logged in as a resident who owns a house:
- [ ] `/complaints` lists only this resident's house complaints; status filter works.
- [ ] "Raise complaint" creates one (single-house resident: no house picker needed); it appears in the list.
- [ ] Detail shows the timeline; an `open` complaint shows Edit + Withdraw (and NO admin buttons).
- [ ] Editing while open updates title/description/category; a non-open complaint hides Edit.
- [ ] Withdraw moves an open complaint to `withdrawn` (confirm dialog first).
- [ ] Add up to 2 report photos (thumbnails render); the 3rd is blocked; delete a photo works; all
      image controls disappear once the complaint is no longer `open`.
- [ ] Opening a complaint that had a notification decrements the bell badge.
- [ ] A complaint id you don't own → `<Forbidden/>`; a nonexistent id → "Complaint not found".
- [ ] Mobile: list → cards, modals → bottom sheets.
- [ ] `npx tsc --noEmit` and `npm run build` clean.
