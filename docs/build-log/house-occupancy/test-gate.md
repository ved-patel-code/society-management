# House & Occupancy — Test Gate

Phase 3 test gate for Module 2. Two-model split: **Opus 4.8 designed the test
matrix** (155-case spec, grounded in the implementation with file:line
derivations for the subtle paths); **Sonnet 5 implemented and ran** the cases in
Docker, iterating to green and flagging any genuine product bugs rather than
bending tests to pass.

## Result

```
docker compose exec backend bash scripts/run-tests.sh
372 passed  (~41s, pytest-xdist)
```

- 218 pre-existing (foundation + onboarding) — still green (no regressions).
- **154 new House & Occupancy tests** across 6 dimension-split files.
- **Zero product bugs found** — every documented business rule held against the
  implementation.

## Files & coverage

| File | Tests | Covers |
|---|---|---|
| `test_houses_smoke.py` | 10 | registry/perms seeded; enable-without-onboarding → DependencyError; enable-both; list/detail/history/empty-page shapes; all routes 401 without auth |
| `test_houses_happy.py` | 30 | every empty→X and non-empty→non-empty transition; owner retention; tenant open/close; filters (status/building/floor/number); pagination; detail/history; display codes; email normalization; id_proof roundtrip; current_owner_user_ids; **N+1 perf guard** |
| `test_houses_badpaths.py` | 40 | →empty rejected (409) from each status; unknown status; required-field violations per target; owner/tenant payload validation; 404s; pagination/filter-type bounds; rollback-persists-nothing |
| `test_houses_security.py` | 26 | per-route permission gating; module-disabled (403); cross-society IDOR (404, no leak); crafted/forged tokens; super-admin gate derivations (no-active-society → 422); must-change lockout |
| `test_houses_edge.py` | 33 | owner replacement (close/flush/revoke/provision, single-txn no unique violation); orphan-deactivation (kept-role vs role-removed); first_left_empty_on once-only; same-status POST = edit (no history); dual-role link; **id_proof retention regression**; validity windows |
| `test_houses_e2e.py` | 15 | full multi-house lifecycle; owner replacement mid-journey; audit completeness; individual-house flow; onboarding-complete-then-operate; **delete-guard regression** (occupancy blocks building/floor/house delete; empty deletes fine) |

## Test-side corrections made during implementation (not product bugs)

1. Same-status repost history-count tests initially asserted 0 history rows — the
   *initial* transition writes 1; only the same-status repost adds none. Fixed to
   assert exactly 1.
2. Orphan-deactivation test expected a separate `user.deactivated` audit —
   `revoke_house_access` deactivates inline and records only `house.access_revoked`
   with `after.deactivated=True`. Corrected.
3. Owner-email-equals-admin test expected 0 `user.created` rows — the `admin_user`
   fixture itself is provisioned via `create_or_link_user`, emitting one at setup.
   Corrected to assert the link adds no *second* user.
4. E2e single-replacement-deactivation test — a plain replacement never orphans
   the old owner (they keep the `resident` role). Rewritten to assert the correct
   behavior (stays active); true orphan-deactivation is covered at the DB layer in
   `test_houses_edge.py` after an explicit role removal.

## Perf guard

`test_list_houses_no_n_plus_one_on_buildings` asserts `list_houses` issues a
constant ≤3 SELECTs (count + page + one batched building fetch) for a page
spanning 4 buildings — verified constant at 2/4/8/16 buildings — locking in the
N+1 fix (buildings batch-loaded via `WHERE id IN (...)`).
