# House & Occupancy Module — Design

> Design doc. Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md) · [../platform/platform-foundation](../platform/platform-foundation.md) · [onboarding](onboarding.md)
>
> **Confirmed decisions baked in:** statuses empty/owned/rented/to_let/for_sale, **never back to empty** · dues accrue from month house first leaves empty (`first_left_empty_on`) · owner login **auto-provisioned/linked** · **data tied to `house_id`** (occupant↔login via `house_occupancies.user_id`) · owned→rented keeps owner access, **tenant login/view deferred** · replace occupant → unlink + revoke sessions, deactivate if orphaned · **ID proof OPTIONAL** everywhere · owner data (incl. ID proof) **retained across statuses + editable** · owner **email required** (login) · transitions: **any non-empty ↔ any non-empty**, each captures the target's data.

## 1. Purpose & scope
Manage each house's occupancy lifecycle after onboarding: change **status** (empty → owned/rented/to_let/for_sale, never back), capture/edit **owner & tenant** details (+ optional ID-proof image via Vault), auto-provision **owner logins**, and **filter houses by status**. Writes the status/occupancy columns of the shared `houses` rows created by Onboarding.

**Out of scope:** structure/numbering (Onboarding); dues calculation (Finance — this module only sets `first_left_empty_on`, which Finance reads); tenant login/view (deferred).

## 2. Audience & permissions
- **society_admin** operates this module. Owners get read access to their own house's data via the occupancy link (foundation); the module itself is admin-facing.
- Permissions (`houses.*`): `houses.read`, `houses.update_status` (change status + occupancy payload), `houses.manage_occupancy` (edit occupant details).
- Gated `require_module('houses')` + `require_permission(...)`.

## 3. Data model
Shared/owned columns; logic in services; DB holds PK/FK/NOT NULL/UNIQUE only.

**houses** (shared table; this module owns these columns) — `status`, `first_left_empty_on` DATE (set once when first leaving empty). Structure columns owned by Onboarding.

**house_occupancies** (this module owns) — `society_id` FK, `house_id` FK, `party_type`(owner|tenant), `user_id` FK NULL (login link; foundation provisions), `full_name`, `email` (owner: required; tenant: optional), `contact_number`, `persons_living` INT NULL, `id_proof_type` TEXT NULL, `id_proof_document_id` FK→vault_documents NULL (**optional; wired when Vault built**), `is_current` BOOL, `valid_from`, `valid_to` NULL.
- Partial UNIQUE(`house_id`,`party_type`) WHERE `is_current` — one current owner + one current tenant per house max.
- idx(`society_id`,`house_id`); idx(`user_id`).

**house_status_history** (this module owns; append-only) — `society_id`, `house_id`, `from_status`, `to_status`, `changed_by`, `changed_at`, `snapshot` JSONB.

## 4. Business rules
**Transitions:** empty → any non-empty (captures target data); any non-empty → any non-empty (never empty). Each validates the target status's required fields.

**Required data per target status:**
- **owned:** owner {name, email, phone, persons_living}; ID proof optional.
- **to_let / for_sale:** owner {name, email, phone}; ID proof optional; no persons_living. (Identical data + behavior; only the label/intent differs.)
- **rented:** owner {name, email, phone} (kept/prefilled if already present) + tenant {name, phone, persons_living; email optional; ID proof optional}.

**Owner retention & identity:**
- The owner record (incl. ID proof) is **retained across status changes** and **editable** (e.g. in owned).
- **Owner identity = email.** On a transition/edit: **same email → same owner** (update fields, keep login/access); **different email → owner replaced** (close old owner occupancy, `revoke_house_access` on the old login — deactivate if orphaned — and `create_or_link_user` for the new owner). Uses foundation `UserProvisioningService`.

**Tenant:** created when entering `rented`; replacing the tenant closes the old tenant occupancy and opens a new one; leaving `rented` closes the tenant occupancy. Tenant login deferred (no login provisioned now).

**Other invariants:**
- `first_left_empty_on` set on the **first** move away from empty; never cleared; drives Finance dues.
- **No transition to empty** (hard rule, no exception).
- Owner **auto-provisioned** with the society default password + must_change; ID-proof image (optional) stored via the Vault interface under `Houses/<house>/Proof` when provided — `id_proof_document_id` nullable, wired when Vault is built.
- Every status change + occupancy edit writes `house_status_history` + `audit_log`, all within the transaction.

## 5. Audited actions
Written to `audit_log` (in-transaction, append-only) — in addition to the domain-specific `house_status_history`:
- `house.status_changed` — house_id, from_status → to_status.
- `house.occupancy_created` — house_id, party_type (owner/tenant).
- `house.occupancy_updated` — house_id, party_type, changed fields (before/after).
- `house.owner_replaced` — house_id, old owner user → new (email change triggers replacement).
- `house.access_revoked` — user_id, house_id (occupant removed; deactivated if orphaned).

## 6. Endpoints (`/houses/*`, society from JWT)
- `GET /houses` — list with **filters** (status, building/floor, search by number/display code); paginated. (`houses.read`)
- `GET /houses/{id}` — detail + current occupancy(ies) + history.
- `POST /houses/{id}/status` — change status with the target's occupancy payload (validates; provisions/updates/replaces owner login per identity rule; opens/closes occupancies; sets `first_left_empty_on` on first leave-empty). (`houses.update_status`)
- `PATCH /houses/{id}/occupancy/{party}` — edit owner/tenant details (email change → re-provision per identity rule). (`houses.manage_occupancy`)
- `GET /houses/{id}/history` — status/occupancy history. (`houses.read`)

## 7. Inter-module contracts
- **Consumes:** Onboarding **house registry** (houses exist, resolve-by-number); foundation `UserProvisioningService` (`create_or_link_user`, `revoke_house_access`), `AuditService`, `TenantContext`; Vault `store_document`/`get_download_url` for ID-proof images (**wired when Vault built**).
- **Provides:** house **status + `first_left_empty_on`** for Finance; **current-occupant → house** mapping for resident access; **house status** for Onboarding's delete-guard; **`current_owner_user_ids(society_id)`** (the society's current owner accounts) for Notice Board read-receipts/audience and (future) Notifications recipients.
- **Shared `houses` table:** this module writes `status`, `first_left_empty_on`, occupancy; Onboarding owns structure.

## 8. Feature flag / config
- Module key `houses`. `depends_on: onboarding`. No significant `config` now.

## 9. Background jobs
None (dues generation lives in Finance).

## 10. Open questions / future
- Unpaid dues when an owner is replaced — dues stay on `house_id`; who settles is a Finance concern.
- Tenant login + view (deferred).
- Bulk status update / import (future).

## 11. Resolved decisions
1. **Owner identity = email** — same email = same owner (keep access); different email = owner replaced (revoke old + provision new).
2. **to_let and for_sale are identical** in data + behavior (owner + login + dues apply), differing only by label/intent.
3. **persons_living** captured for owned (owner) and rented (tenant), not for to_let/for_sale.
4. **Module key `houses`**, `depends_on: onboarding`.
