# Remaining Modules — Discussion Notes (handoff for future design sessions)

> **Purpose:** capture everything discussed/decided about the **not-yet-designed** modules so they can be designed in a **fresh chat** without re-explaining the app.
> **Before designing any of these, read:** `docs/README.md` → foundation docs `docs/01`–`docs/05` → `docs/platform/platform-foundation.md` → the already-designed module docs. Then follow the same process we've used: **surface genuine decisions first, ask the user, keep no assumptions**, draft the design, then write `docs/modules/<module>.md` (using the template in `docs/04-module-template.md`, which includes a standard **§5 Audited actions** section).

## Status / design order
Designed ✅: Platform Foundation, Onboarding, House & Occupancy, Vault, Finance, **Complaints**, **Notice Board**, **Notifications** (`docs/modules/notifications.md`).
**Remaining: NONE — all modules are designed.** This handoff doc is fully consumed; the authoritative specs are the per-module docs under `docs/modules/`.

**Built 🛠️ (backend, in Docker, on feature branches → PR):** Module 0 — Platform Foundation; **Module 1 — Onboarding** (`docs/implemented/onboarding.md`; 218 tests); **Module 2 — House & Occupancy** (`docs/implemented/house-occupancy.md`; 372 tests) — writes `houses.status` / `first_left_empty_on` / occupancy on the registry Onboarding created, provides `current_owner_user_ids`. Next module to build: **Vault** (Module 3) — document storage (MinIO); also wires the deferred `house_occupancies.id_proof_document_id` FK + ID-proof upload.

## Global rules already in force (apply to every remaining module)
- **Stack/arch:** modular monolith, FastAPI + Postgres + MinIO + Docker; per-society **feature flags** (`society_modules`); **data-driven roles/permissions**; each module self-contained (`ModuleSpec`, `require_module` + `require_permission`).
- **API URL conventions** (`docs/02 §3.1`): root-level, no version prefix; society-scoped modules mount at `/{module}/*` with society from the JWT; `society_admin` and residents **share** module endpoints — **permissions decide access, no per-role prefix**.
- **Backend owns all logic**; DB keeps only PK/FK/NOT NULL/UNIQUE; BIGINT identity PKs; efficient queries (no N+1, only needed columns).
- **Audit:** every state-changing action writes `audit_log` (in-transaction); each module doc lists its audited actions.
- **Data tied to `house_id`, not login** (dues/complaints/docs follow the house).
- **Residents = owners** for now. **Tenant (renter) login + view is DEFERRED** — decide per module how tenants fit when that lands.
- **Vault (built) provides** `store_document` / `get_preview_url` / `get_download_url` / `ensure_house_folder(house, kind)`; complaint images live under **`Houses/<house>/Complaints/<complaint>`**, ID proofs under `Houses/<house>/Proof`.
- **EmailSender** interface exists (test mode → terminal; SMTP later). A **worker** container exists for scheduled/background jobs.

---

## 1) Complaints module — ✅ DESIGNED → `docs/modules/complaints.md`
Resolved with the user: status **open→in_progress→resolved→closed→archived** (admin manual close; worker auto-archives after configurable days, default 15); categories **predefined + admin-extendable**, **common-area = a category** (complaint always ties to raiser's house, `house_id` NOT NULL); **status-only + optional admin note** (no thread); resident **edit + withdraw while open**; **≤2 report + ≤2 proof images** in Vault; **no admin-raised, no assignment** in v1. Open-questions below are kept for history.

### Original requirement (user's words, paraphrased)
- Present in **both** views — resident (home owner) and society admin.
- **Resident:** see the list of complaints they raised **with status**; create a new complaint; attach **up to 2 images** of the problem (not mandatory).
- **Admin:** see complaints, **update status**, and on completion optionally add a **proof photo** (not mandatory).
- All complaint images are **auto-saved to the Vault** (in a folder; admin can make folders).

### Already decided / constrained
- Complaint **images → Vault** under `Houses/<house>/Complaints/<complaint>` (Vault built; wire via `store_document`). Skeleton link: `complaint_images.vault_document_id` FK.
- Complaints are **house-scoped** (tied to the resident's house; data follows the house).
- **Resident raises**, sees only their own; **admin sees all**, updates status, adds proof. Residents & admin share `/complaints/*` endpoints; permissions differ.
- Resident gets a **notification on status change** (delivered by the Notifications module).
- Tenant raising complaints = **deferred** (owners only for now).

### Open questions to resolve (ask the user)
- **Status set** — proposed `open / in_progress / resolved / closed`; which transitions are allowed; who can close (admin only? resident cancel?).
- **Complaint categories/types** (plumbing, electrical, common-area, …) — include or freeform?
- **Common-area / non-house complaints** — supported, or every complaint tied to a house?
- **Conversation/comments thread** between resident and admin, or status-only?
- **Image counts** — resident ≤2 (report) confirmed; admin proof-photo count/limit?
- **Admin-raised complaints** on behalf of a house? **Assignment** to a staff member?
- **Filters** for admin (by status/house/category); **reference number** format.
- **Edit/withdraw** rules (can a resident edit/withdraw an open complaint?).
- Which **`complaints.*` permissions** (e.g. `complaints.create`, `complaints.read`, `complaints.update_status`).

---

## 2) Notice Board module — ✅ DESIGNED → `docs/modules/notice-board.md`
Resolved with the user: **whole-society broadcast only**; content = title + **rich text** + **unlimited attachments** (Vault); lifecycle **draft→published**, **edit after publish** (shows "edited · date"), **withdraw** (soft-delete), **pin**, optional **expiry** (query-time, no worker); **admin read receipts** (denominator = current owners); emits **`notice_posted`** for Notifications (skeleton); **admin-only archive** (residents see active feed only); **resident-portal landing = the active feed**. Vault extended with a `Notices/<notice>` system folder. Open-questions below kept for history.

### Original requirement (user's words, paraphrased)
- Admin can **send a notice to the entire society at once**; all home owners can see it.
- Admin has a **tab/panel** listing all notices they've sent + composing a new one.
- The resident's **default landing page is the Notice Board**.

### Already decided / constrained
- **Broadcast society-wide**; **admin posts, residents read**. Shared `/notices/*` endpoints, permission-gated.
- Resident **landing page = active notices** (ordered newest first).

### Open questions to resolve (ask the user)
- **Notice content** — title + body; **rich text?** **attachments/images** (via Vault)?
- **Targeting** — whole society only, or subsets (a building/block)? (spec says whole society.)
- **Lifecycle** — edit/delete a notice? **pin/priority**, **expiry/auto-archive**, draft vs publish?
- **Acknowledgement / read receipts** — track who has seen a notice?
- **Notification on new notice** — push a notification when a notice is posted (ties to Notifications)?
- Which **`notices.*` permissions** (e.g. `notices.publish`, `notices.read`).

---

## 3) Notifications module — ✅ DESIGNED → `docs/modules/notifications.md`
Resolved with the user: **in-app only** in v1 (email/push future); **reusable engine** = event-driven (immediate) + scheduled reminder-rule registry; **dues cadence** advance (X) + due-day + every N days while unpaid, **admin-set X/N**, **one consolidated notification** per fire (total of all unpaid months), stateless/idempotent via `dedupe_key`, auto-stops when paid; **recipients = owners AND admins** (admin alerts: `complaint_new`, `complaint_withdrawn`, resolved by permission); **clear-on-read** feed (removed once read, incl. when the item is read anywhere via `mark_read_for`); **no opt-out** in v1. Reminder cadence config lives in Notifications; `maintenance_due_day` stays in Finance. Open-questions below kept for history.

### Original requirement (user's words, paraphrased)
- Home owners get notifications: **complaint updates**, **maintenance-due reminders**, etc.
- Admin sets the **reminder interval** for dues (e.g. every 5 days if unpaid). The **maintenance due day** is admin-set (already lives in Finance config as `maintenance_due_day`).
- If a due is **carried forward** to the next month, send **ONE notification with the total due**, not one per month (**dedupe**).

### Already decided / constrained
- **User wants the reminder system MODULAR/reusable** — design a generic **reminder/notification engine** other modules hook into (not finance-only).
- **Finance emits due/overdue signals**; Notifications delivers. **Maintenance due day** = Finance config; **reminder interval** = Notifications config.
- Channels: **in-app + email** (via `EmailSender`); push = future. Needs the **worker/scheduler**.
- Recipients = **owners** for now (tenant deferred). Types seen so far: `complaint_update`, `maintenance_due`, `notice`.
- **Dedupe:** carried-forward dues collapse to a single notification (e.g. a `dedupe_key` per dues cycle).
- Notice-board posts may trigger a notification (decide with Notice Board).

### Open questions to resolve (ask the user)
- **Delivery channels** in v1 — in-app only, or in-app + email now? Push later?
- **Reminder cadence** — exact rules: first reminder on/after due day, then every N days while unpaid; stop condition; the single-consolidated-total behavior for arrears.
- **Reusable-engine shape** — a generic `notify(user, type, payload)` + a scheduled-reminder registry other modules register rules with; how modules emit events.
- **In-app feed** — unread counts, mark-read/mark-all-read, retention.
- **Per-user notification preferences / opt-outs** (future?).
- **Event source pattern** — the lightweight in-process domain-events dispatcher mentioned in `docs/05 §3` (module emits event → Notifications subscribes).
- Which **`notifications.*` permissions** + admin config endpoints (set reminder interval, channels).

---

## Cross-cutting deferred items that intersect these modules
- **Tenant (renter) login + view** — deferred. When designed, decide tenant visibility into complaints/notices/notifications.
- **Payment gateway** — future (resident self-pay); Finance already has a `PaymentProvider` interface.
- **Elections module** — future (in-app handover of `society_admin` via `user_roles`); may itself use Notifications + Notice Board.

## How a new chat should proceed
1. Load context: `docs/README.md`, foundation `docs/01`–`05`, `docs/platform/platform-foundation.md`, and this file.
2. Pick the next module (**Complaints** first). Work through its **Open questions** with the user (AskUserQuestion, no assumptions).
3. Draft the design, then write `docs/modules/<module>.md` per the template; update `docs/01`, `docs/README.md`, and the auto-memory `docs-structure.md`; apply any cross-module alignment edits.
