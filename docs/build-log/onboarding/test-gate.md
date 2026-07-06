# Onboarding (Module 1) — Test Gate

Phase-3 test gate. Coverage was written by parallel Opus 4.8 test agents (one file
per dimension) on top of the shared harness; the lead consolidated and stabilized.

## Result
**218 tests pass** (146 → 218; +72 onboarding tests across 4 new files), full suite
green in ~21s. All through the real HTTP stack asserting status + body + DB state +
audit rows.

## Files & coverage
| File | Dimension |
|------|-----------|
| `test_numbering.py` | Pure numbering engine — all 6 modes, ground floor, pad, floor-10, per-floor overrides, continuous across towers, custom prefix/pad, manual mismatch, clash helper (Phase 1). |
| `test_onboarding_smoke.py` | Registry/seed, auto-grant on enable, /me onboarding_required, type selection, gating (Phase 0). |
| `test_onboarding_later_edits.py` | Continuous-seq seeding, post-completion add-building/map, add-floors, floor default (Phase 2). |
| `test_onboarding_happy.py` | Both society types; all numbering modes; per-floor override + building default; continuous across towers; prefill-repeat; resume/draft; preview; add-floors; completion. |
| `test_onboarding_security.py` | With/without roles (manage vs read vs none); module-disabled block; cross-tenant isolation (A→B = 404); unauth/bad token; must_change lockout; forged claims re-derived from DB; SQLi-safety. |
| `test_onboarding_badpaths.py` | Invalid type/transitions; dup names; number clashes (batch rolled back, offenders reported); floor validation; manual count mismatch; override edges; re-map guard; delete guards; complete validation; 404s. |
| `test_onboarding_e2e.py` | Full building + individual journeys incl. foundation prerequisites; house-registry reads; post-completion later edits; blocking-wizard signal. |

## Test-pipeline problem found & fixed
- **Symptom:** during parallel test-agent execution, suite runs hung with no output
  and intermittently raised `uq_permissions_key`.
- **Root cause:** multiple concurrent full-suite runs (4 test agents each running
  `pytest -n 4`, plus leftover interrupted runs) shared the same per-worker
  databases (`society_test_gw0..3`) and deadlocked/collided on the per-test
  truncate+reseed. Not a code or test-correctness defect — it was DB contention from
  overlapping/zombie pytest processes.
- **Fixes:**
  1. Killed the stale pytest processes; a single clean run passes.
  2. **Hardened the harness:** `conftest._seed_baseline` is now idempotent
     (`ON CONFLICT DO NOTHING` on permissions/roles/super-admin), so overlapping or
     leftover runs can never crash the reset again.
  3. **Faster:** `run-tests.sh` default workers 4 → 8 (collision-safe now); full
     suite ~26s → ~21s with no coverage reduced. `TEST_WORKERS` still overrides.

## No real product bugs found in this gate
All Phase-3 failures traced to the pipeline contention above, not to the module.
The functional defects were already caught and fixed in the Phase-2 code-review gate
(see `code-review-findings.md`).
