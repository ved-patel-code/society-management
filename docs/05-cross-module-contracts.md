# Cross-Module Contracts

> Foundation doc 5 of 5. How modules depend on and talk to each other.
> Companion docs: [01-project-overview](01-project-overview.md) · [02-architecture](02-architecture.md) · [03-backend-and-db-principles](03-backend-and-db-principles.md) · [04-module-template](04-module-template.md)

## 1. How modules communicate
Modules never read/write each other's tables directly. Each module that others depend on exposes a **service interface** (a Python class/functions in its `service` layer). Consumers call the interface. This keeps boundaries clean and lets a dependency be built/changed behind a stable contract.

Example interfaces (detailed in each module's design doc):
- **Vault** provides: `store_document(society_id, folder_key, file) -> document_id`, `get_download_url(document_id)`, `usage(society_id)`.
- **Finance** provides: `outstanding_dues(house_id)`, `record_payment(...)`, `generate_due_cycle(...)`.
- **Notifications** provides: `notify(user_id, type, payload)` and consumes events from other modules.

## 2. Skeleton-then-wire dependency map
When a consumer is built before its dependency, it stores the linking field (nullable) and is marked "requires module X"; it's wired when X exists.

| Consumer | Needs | Skeleton now | Wired when |
|---|---|---|---|
| House/Occupancy (owned → ID proof image) | Vault | `house_occupancies.id_proof_document_id` nullable FK | Vault core built (built early) |
| Complaints (images) | Vault | `complaint_images.vault_document_id` FK | ✅ wired (Vault built) — filed under `Houses/<house>/Complaints/<reference>` |
| Complaints (status/new/withdraw → resident+admin) | Notifications + worker | emits `complaint.status_changed` / `complaint.created` / `complaint.withdrawn`; calls `mark_read_for` on open | ✅ wired (Notifications built) |
| Notice Board (attachments) | Vault | `notice_attachments.vault_document_id` FK; society-level `Notices/<notice>` system folder via `ensure_notice_folder` | ✅ wired (Vault built) |
| Notice Board (new notice → residents) | Notifications | emits `notice_posted`; calls `mark_read_for` on notice-open | ✅ wired (Notifications built) |
| Notice Board (read receipts + audience) | House & Occupancy | consumes `current_owner_user_ids(society_id)` | ✅ wired (Occupancy built) |
| Finance (due reminders) | Notifications + worker | dues service callable standalone; reminder rule hosted by Notifications | ✅ wired (Notifications built) |
| Notifications (maintenance due) | Finance | consumes `outstanding_dues` + `maintenance_due_day`; `dedupe_key = dues:{house}:{day}` | ✅ wired (Finance built) |
| Finance (online payment) | Payment gateway | `PaymentProvider` interface, `payments.method=gateway` | Gateway added (future) |
| ID proofs + Complaint images | Vault house folders | house-centric layout `Houses/<house>/Proof` + `Houses/<house>/Complaints`, auto-created on first use | Vault built |
| Elections (admin handover) | Roles + Notifications + Audit | reassigns `society_admin` via `user_roles`; reads `audit_log` for tenure history | Elections module built (future) |

## 3. Events (lightweight)
For "when X happens, notify/act" (e.g. complaint status changed → notify resident), modules emit **domain events** to a simple in-process dispatcher; interested modules subscribe. This avoids tight coupling and keeps notification logic out of the emitting module's core. **Notifications** ([modules/notifications.md](modules/notifications.md)) is the primary subscriber.

**Events in play (v1):**
| Event | Emitter | Notifications delivers |
|---|---|---|
| `complaint.created` | Complaints | `complaint_new` → admins (holders of `complaints.read_all`) |
| `complaint.withdrawn` | Complaints | `complaint_withdrawn` → those admins |
| `complaint.status_changed` | Complaints | `complaint_update` → the raising owner |
| `notice_posted` | Notice Board | `notice` → all current owners |

**Clear-on-read hook:** the in-app notification feed empties as items are read. A reading module calls Notifications **`mark_read_for(user_id, entity_type, entity_id)`** when a user opens the underlying item (Complaints on complaint-open; Notice Board on notice-open), clearing that user's pending alert for that entity — in addition to the user opening the notification directly.

**Scheduled reminders (not events):** recurring reminders (v1: maintenance dues) are **reminder rules** registered with the Notifications engine and run by the worker — see [notifications.md §4.3/§9](modules/notifications.md). Finance exposes the dues data; the cadence config lives in Notifications.
