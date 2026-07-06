# Onboarding (Module 1) — Code-Review Gate Findings

Automated code-review gate (Opus 4.8, read-only) after Phase 1. The reviewer
confirmed security, tenant isolation, audit coverage, migration fidelity, and the
core numbering math were correct, and surfaced functional gaps in the later-edits
workflow and continuous-sequential semantics. All must-fixes were applied and
re-verified in Docker (146 tests green, full HTTP e2e green).

## Must-fixes applied

| # | Severity | Area | What was wrong | Fix |
|---|----------|------|----------------|-----|
| 1 | HIGH | `repository.max_continuous_number` / `service` continuous seed | Continuous sequential seeded from the max over ALL numeric house numbers, so an AUTO tower's floor-encoded numbers (e.g. `1001`) pushed the next continuous tower to start at `1002` — not "one running sequence across towers" (spec §4). | Added `max_continuous_building_number` / `max_continuous_individual_number` that count only houses with `numbering_mode='sequential'`; seed continuous batches from those. |
| 2 | MEDIUM/HIGH | `create_buildings`, `map_building` | Both called `_require_onboarding_open`, so once the society was `active` they 409'd — but spec §4/§6 list add-building/map as allowed **post-completion** later edits. | Removed the open-guard from those two methods (type guards kept; `select_type`/`complete` keep theirs). |
| 3 | MEDIUM | add-floor later edit | `map_building` refused any building that already had houses and there was no other add-floor path, so "add floor" (spec §4/§6 later edit) was impossible. | Added `POST /onboarding/buildings/{id}/floors` + `service.add_floors`, reusing the building's stored `numbering_config`; clash-checked + audited. |
| 4 | MEDIUM | per-floor `houses_count` default | Spec §3 says a floor's count "falls back to the building default," but no default field existed and `houses_count` was required. | Added optional `default_houses_per_floor` on the map + add-floors requests; `FloorInput.houses_count` now optional; effective count = per-floor override else default; both missing → 422. (User decision: different floors may have different counts.) |

## Confirmed correct (no change needed)
- **Tenant isolation**: society from JWT (never a path id); every repo query
  `society_id`-scoped; cross-tenant path ids → 404.
- **Auth gates**: every write route `require_module('onboarding')` +
  `onboarding.manage`; reads `onboarding.read`.
- **Transactions / audit**: no service commits (flush only); every state change
  writes an in-transaction audit row with the exact spec §5 action strings.
- **Auto-grant**: additive + idempotent; grants only for enabled modules.
- **Numbering engine**: AUTO ground-prefix, floor-10 padding, per-floor overrides,
  custom prefix+pad, per_building-vs-continuous, manual count-mismatch errors — all
  correct and unit-tested.
- **Migration**: matches the models exactly (both partial unique indexes, ground
  partial unique, FKs, nullability); downgrade order correct.

## Deferred (documented, revisit when later modules exist)
- **Delete guard**: v1 blocks delete only when a house's `status != 'empty'`. The
  fuller guard (Finance **dues** + House & Occupancy **occupancy**) is deferred until
  those modules are built; wire the real checks into `delete_building/floor/house`
  then. Tracked in `docs/implemented/onboarding.md` deviations.
