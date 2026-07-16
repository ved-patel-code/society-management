# Module: Financial (house + maintenance dues, merged)

The resident's single "Financial" tab. It combines what would otherwise be a "My House" view with the
maintenance-dues view: house header, read-only dues (outstanding + history), a "how to pay" note, and
that house's complaints. **There is no online payment** — dues are strictly read-only.

> **Prerequisites:** `00-foundation.md` built and merged. You reuse `useComplaints` from
> `@/hooks/useComplaints` — the foundation ships it as a stub (§5b), so this import resolves and
> typechecks **whether or not the Complaints module is built yet**. If Complaints hasn't landed, the
> complaints section simply renders empty (the stub returns `{items:[],total:0}`); it fills in
> automatically once Complaints replaces the stub. **You can therefore build Finance fully in
> parallel with the other modules.** Import ONLY frozen foundation exports; do NOT modify
> foundation files. You own `src/pages/FinancePage.tsx`, `src/components/finance/*`,
> `src/types/finance.ts` body, `financeApi` bodies + finance query keys, and `useHouseDues`.
>
> API source of truth: `d:\society\docs\api\finance.md`. Base path `/finance`.

## Foundation imports
`apiFetch`, `queryKeys.finance.dues`, `useAuth`/`useCan`/`useModule`, `<Can>`, `<IfModule>`,
`<DataView>`, `<PageHeader>`, `<SectionCard>`, `<EmptyState>`, `<Forbidden>`, `<LoadingState>`,
`<StatusBadge>`, `fmtDateOnly`, `formatCurrency`, `getErrorMessage`, sonner `toast`, shadcn
`Card`/`Badge`. From the Complaints module: `useComplaints` (frozen signature) — import from
`@/hooks/useComplaints`.

---

## ⚠️ Two hard constraints (do not violate)

1. **Residents have NO `/houses/*` access.** Do NOT call any `/houses/...` endpoint. There is **no
   owner/tenant record** for a resident, and no endpoint returns the resident's `house_id` directly.
   **Derive the `house_id`** from the resident's data:
   - Preferred: from a `maintenance_due` notification's `payload.house_id` / a complaint's `house_id`.
   - Robust approach for this page: get the house from the resident's complaints
     (`useComplaints({ page: 1, page_size: 1 })` → `items[0]?.house_id` / `house_display_code`). If
     the resident has complaints, that yields the house. If they have **neither dues context nor
     complaints**, show the empty state (below). (A resident normally has dues generated for their
     occupied house; the dues endpoint needs the id, hence deriving it first.)
   - If you can obtain `house_id` but the resident is not a current occupant, the dues call returns
     403 `"You may only view dues for your own house."` → render `<Forbidden/>` for that section.
2. **`finance.read` over-grants.** The backend will NOT stop a resident from calling society-wide
   finance/analytics endpoints. This page must render the **own-house dues view ONLY** — no rate,
   reserve, expenses, or analytics. Gate any future admin finance surfaces by **portal**
   (`me.active_portal === "admin"`), never by `has("finance.read")`.

## Permission / module gating
- Page under `<IfModule module="finance">`.
- Resident permission is **`finance.read`** (own-house dues via the per-house scope check).

---

## Endpoints (exact — from finance.md)

### Types (`src/types/finance.ts`)
```ts
export interface HouseDue {
  id: number;
  house_id: number;
  period_year: number;
  period_month: number;        // 1–12
  amount_due: string;          // decimal string, e.g. "2500.00"
  due_date: string;            // ISO date
  status: "outstanding" | "paid";
  source: "accrued" | "prepaid";
  locked_rate: string | null;
  paid_at: string | null;
  is_overdue: boolean;         // computed: outstanding && due_date in the past
}
export interface HouseDuesResponse {
  house_id: number;
  outstanding: HouseDue[];     // status "outstanding" only, oldest-first
  outstanding_total: string;   // decimal string
  history: HouseDue[];         // all dues, oldest-first
}
```

### `GET /finance/houses/{house_id}/dues` → `HouseDuesResponse`  (perm `finance.read` + own-house scope)
- 403 `"You may only view dues for your own house."` → `<Forbidden/>` for the dues section.
- 404 `"House not found in this society."` → treat as empty state.

`financeApi` (foundation stubbed the path):
```ts
export const financeApi = {
  houseDues: (houseId: number) => apiFetch<HouseDuesResponse>(`/finance/houses/${houseId}/dues`),
};
```

### `useHouseDues` — replace the foundation STUB (file: `src/hooks/useHouseDues.ts`)
Foundation §5b shipped a stub returning `null`. Replace its `queryFn` with the real call below; keep
the signature, query key, and `enabled` gate identical.
```ts
export function useHouseDues(houseId: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.finance.dues(houseId as number),
    queryFn: () => financeApi.houseDues(houseId as number),
    enabled: typeof houseId === "number",
  });
}
```

---

## Components & behavior

Files:
- `src/pages/FinancePage.tsx`
- `src/components/finance/HouseHeader.tsx`
- `src/components/finance/DuesSummary.tsx`
- `src/components/finance/DuesHistory.tsx` (uses `<DataView>`)
- `src/components/finance/HowToPayNote.tsx`
- `src/components/finance/HouseComplaints.tsx` (reuses `useComplaints`)

### `FinancePage` — assembly
1. Derive `house` (`{ house_id, house_display_code }`) as described in constraint #1 — use
   `useComplaints({ page: 1, page_size: 1 })` to read `items[0]`. (If you have a cheaper source of
   the house id later, swap it in — keep the empty-state fallback.)
2. If no `house_id` can be derived → `<EmptyState title="No house data yet"
   description="Once your maintenance dues or complaints are on record, your house details appear here."/>`
   and stop.
3. Otherwise: `useHouseDues(house_id)`; compose the sections below.
4. `<PageHeader title="Financial"/>`.

### `HouseHeader`
Card showing `house_display_code` (fallback "Your house") and, if you have it, a `<StatusBadge>` for
the house status. (Status is often NOT available to residents — only render the badge if you actually
have a status string; otherwise omit it. Never fetch `/houses` to get it.)

### `DuesSummary`
A row of summary `Card`s from `HouseDuesResponse`:
- **Outstanding total** → `formatCurrency(outstanding_total)`.
- **Months pending** → `outstanding.length`.
- **Next due date** → the earliest `outstanding[0]?.due_date` (`fmtDateOnly`), or "All paid" when
  `outstanding.length === 0`.
Show a red accent when there are overdue dues (`outstanding.some(d => d.is_overdue)`).

### `DuesHistory` (`<DataView>`)
Columns over `history[]`: Period (`${period_month}/${period_year}`) · Amount
(`formatCurrency(amount_due)`) · Status (`<StatusBadge status={d.is_overdue ? "overdue" : d.status}/>`)
· Paid on (`fmtDateOnly(paid_at)` or "—"). Oldest-first as returned (or reverse to newest-first for
display — your call; state it). Read-only, no row actions.

### `HowToPayNote`
A static informational `SectionCard` (no API): e.g. "Maintenance dues are collected offline. Please
pay your society administrator/committee and they will record your payment, after which it reflects
here. For queries, contact your society office." **No "Pay now" button** — there is no payment
endpoint.

### `HouseComplaints`
`useComplaints({ house_id, page: 1, page_size: 5 })` → a compact list (reference · title ·
`<StatusBadge>`), each linking to `/complaints/:id`. Header links to `/complaints`. If Complaints
module isn't built yet, the hook still exists (frozen in foundation) and returns empty → render
`<EmptyState/>`; don't crash.

## Mobile behavior
- Summary cards: `grid` that collapses to 1–2 columns on mobile.
- Dues history via `<DataView>` → labeled cards on mobile.
- Everything single-column and scrollable.

## Query keys & invalidation
- Read: `queryKeys.finance.dues(houseId)`, plus `queryKeys.complaints.list(...)` via `useComplaints`.
- This page has **no mutations** (dues are read-only). A `maintenance_due` notification deep-links
  here; no cache writes needed. It's fine to refetch dues on mount (`staleTime` small).

## Cross-links
Expose `/finance`. `maintenance_due` (`entity_type: "house"`) notifications deep-link here (mapped in
foundation `notificationLinks` → `/finance`). No action needed beyond the route being live.

## Self-verification checklist
Backend `:8000`, `npm run dev` `:3000`, logged in as a resident with dues + at least one complaint:
- [ ] `/finance` derives the house and shows the header with `house_display_code`.
- [ ] Summary shows correct outstanding total, months pending, and next due date (or "All paid").
- [ ] Overdue dues are visually flagged (`overdue` badge / red accent).
- [ ] Dues history table lists every period with amount/status/paid-on; it is READ-ONLY (no pay button).
- [ ] The "how to pay" note is present and contains no payment control.
- [ ] The house's recent complaints show and link to `/complaints/:id`.
- [ ] A resident with NO dues and NO complaints sees the "No house data yet" empty state (no crash,
      no `/houses` call anywhere — verify in the network tab).
- [ ] No society-wide finance calls are made (no `/finance/rate`, `/finance/reserve`,
      `/finance/analytics/*`) — only `/finance/houses/{id}/dues`.
- [ ] Mobile: cards stack, dues table becomes cards.
- [ ] `npx tsc --noEmit` and `npm run build` clean.
