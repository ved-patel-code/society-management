# Finance (Module 4) — Test Gate

Phase 3 QA gate. Opus 4.8 designed the matrix (`test-gate-matrix.md`, this
folder); Sonnet 5 implemented + ran it to green. Builds on the 90 per-feature wave
tests with cross-cutting coverage the waves miss.

## Result
**Full suite: 689 passed, 2 skipped** — stable across repeated parallel runs (no
flakiness). ~146 finance tests total across 13 files. Migration `alembic check`:
no drift.

## New coverage (beyond the per-feature wave tests)
- `tests/_finance_helpers.py` — shared full-stack harness: `enable_finance` (+vault),
  `setup_finance`, `finance_admin_bearer`/`resident_bearer`, `owned_house`/
  `rented_house`, `set_rate_http`, `second_society_with_finance`, `audit_actions`,
  `reserve_balance`, and a `freeze_utcnow`/`frozen_today` helper that patches
  `utcnow` at each CONSUMER module's imported name (dues/collection/expenses/jobs/
  houses) for date-deterministic period-boundary tests.
- `test_finance_e2e.py` — full lifecycle across ALL built modules
  (Foundation→Onboarding→House&Occupancy→Vault→Finance) on real data (proves
  Finance consumes live `houses_owing`, not fixtures) + the void/reversal
  transparency invariant end-to-end + the full audit trail.
- `test_finance_contract.py` — the `finance/api.py` surface called directly as
  Notifications/Onboarding/gateway/worker will (`outstanding_dues`, `has_dues`,
  `maintenance_due_day`, `record_payment` txn-join, `generate_due_cycle`,
  overdue signal, config validation).
- `test_finance_enable.py` — enable→seed→login chain, `depends_on: houses` (409),
  admin-gets-all-6-perms / resident-gets-read via real login, per-society config
  overrides change behavior, and a parametrized module-disabled-403 sweep over
  every `/finance/*` route.
- `test_finance_edge.py` — rate effective-date boundaries, Dec→Jan backfill,
  year-crossing prepaid windows, oldest-first across years, multi-house bulk / no
  runaway N+1, long mixed ledgers with reversals, trends across months, negative-
  balance reconcile.
- `test_finance_security.py` — the 422-not-500 regression gate (the `main.py`
  `jsonable_encoder` fix), a systematic cross-tenant IDOR sweep over
  payment/expense/entry/house ids, resident full-mutation lockout, dues-scope
  (`finance.read_all` vs own-house), must-change-password lockout on finance routes.
- `test_finance_concurrency.py` — generate idempotency at DB-row level, worker
  due-day no-op, second-payment-can't-resettle-a-month (the observable FOR UPDATE
  effect). One P3 two-session live-lock race is `skip`-guarded (needs infra beyond
  this harness; sequential correctness of the same invariant is covered).

## Genuine product bugs found by the gate — FIXED in `app/`
The test gate surfaced 2 real defects (flagged, not papered over); both fixed and
re-verified green:
1. **Missing house-in-society check on the collection methods.** A foreign/
   nonexistent `house_id` on `GET /finance/houses/{id}/dues` returned 200 with
   empty dues (reads as "no dues", not "not found"); `record_payment` returned a
   misleading 422; `record_prepaid` would materialize rows against an unverified
   house. Fixed: `CollectionService._require_house` (via `HouseService.house_exists`,
   the pattern already used by `ReserveService.post_entry`) now guards
   `get_house_dues`, `record_payment`, `record_prepaid` → cross-tenant/unknown
   house → 404.
2. **`HouseDueOut.is_overdue` was never computed** (always the schema default
   `False`) — the exact overdue signal Notifications will consume. Fixed:
   `CollectionService._due_out` computes it as `status == "outstanding" and
   due_date < today` at read time.

## Also fixed during this phase (not gate-found)
- **Dues-read authorization made data-driven** (user requirement): the "view any
  house's dues" capability is the new `finance.read_all` permission (+ `is_super_admin`
  bypass), not a hardcoded role/permission list — a future finance-staff role works
  with zero code change. Residents (`finance.read` only) are scoped to their own
  occupied house.

## Model assignment
Test-matrix design → Opus 4.8; test implementation + running → Sonnet 5. Bug fixes
+ the data-driven authorization change → lead (Opus 4.8). Every subagent had its
model set explicitly.
