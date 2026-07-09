# Notifications (Module 7) — Build Approach

> Build/QA record. Design source of truth: `docs/modules/notifications.md`.
> As-built index: `docs/implemented/notifications.md`.

## Method
Lead-authored core + parallel review + parallel test agents (the repo's
established flow, adapted): one consistent hand wrote the whole module so the
engine/handlers/worker share one style and contract, then adversarial review and
test agents verified it.

### Phase A — Foundation (lead)
Branch `feat/notifications` off `main`. Migration `0008_notifications` (1 table +
4 indexes). Module skeleton + the **engine choke point** (`notify` / `notify_many`
/ `clear_for_entity`) as the frozen contract everything builds on. Foundation
additions: `RolesService.user_ids_with_permission` (reverse permission lookup),
`HouseService.{society_id_for_house, owner_user_ids_for_house,
owner_user_ids_by_house}`. Startup + worker wiring. Verified: migration applies,
app+worker import, table+indexes correct.

### Phase B — Core implementation (lead)
Full module: event handlers (own-session, payload-only, data-driven recipients),
feed/badge/mark-read, config (partial-merge + audit), the dues reminder rule
(pure `is_fire_day` + consolidated `build_for_house`), the two worker scans
(per-society isolation, batched owner resolution).

### Phase C — Expert review (3 parallel agents) + fixes
See `code-review-findings.md`. One CRITICAL bug found (partial-index ON CONFLICT)
and fixed + verified against real Postgres; N+1 eliminated; resilience hardened;
dead code + redundant gate removed.

### Phase D — Test gate (3 parallel agents + lead smoke)
Shared harness `tests/_notifications_helpers.py` (lead-authored, smoke-verified).
Suites: feed/config/security, event handlers/e2e/mark-read, dues/worker/scale/
resilience. Coverage: happy, bad, edge, security (with/without perms +
cross-society isolation + own-only), e2e across complaints/notices/finance, scale
(batched fan-out — one INSERT for N owners), multi-society dues, per-society
failure isolation, crash/idempotent replay, handler-failure containment.

## Key design decisions (baked in)
- **Sync + batched event path**, worker for bulk/recurring (dues, purge). No
  Redis/broker in v1 (Postgres backbone; documented as future for real-time +
  horizontal scale).
- **Idempotency everywhere** via `dedupe_key` + partial-unique `ON CONFLICT` — the
  crash-safe replay + handler-failure-recovery guarantee.
- **Handler-failure containment**: per-event blast radius, per-society isolation
  in the worker, per-recipient-safe batched insert, logged failures.
- **Push-ready seams**: every create through `notify()`; each row carries
  `type/title/body/payload` deep-link data a future push/WS frame needs. Any
  client (web/mobile/desktop) uses the same JWT REST API unchanged.
