# Finance (Module 4) — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/modules/finance.md`. Build/QA record: `docs/build-log/finance/`.

## Status
**COMPLETE** — built (frozen core + 7 parallel waves), code-reviewed, tested. The
fourth toggleable feature module: runs a society's money — effective-dated
maintenance rate, per-house monthly dues materialized by a worker, payment
collection (oldest-first, no partial-within-month) incl. prepaid blocks, expenses
+ income, a computed reserve ledger (void/reversal stay visible), and full
analytics + rate-change preview. `depends_on: houses`; admin-only writes, resident
read-only (own dues). No new third-party dependencies (stdlib `Decimal` +
`NUMERIC(12,2)`, stdlib `calendar`/`date` for month math, existing APScheduler for
the worker). Migration `0005_finance` chained off `0004_vault`.

## File map
Module package `app/modules/finance/`:
- `models.py` — 8 tables: `maintenance_rates` (effective-dated), `house_dues`
  (materialized monthly), `payments`, `payment_allocations`, `prepaid_blocks`,
  `expense_categories` (system + society), `expenses`, `ledger_entries` (the
  reserve backbone + transparency log). Money `NUMERIC(12,2)`; composite indexes
  per the design's common queries; enum-like domains enforced in the service.
- `schemas.py` — frozen Pydantic contracts + the consumed domains
  (`PAYMENT_METHODS`, `RESERVE_POSTABLE_ENTRY_TYPES`, `ENTRY_TYPE_DIRECTION`,
  `LEDGER_SOURCE_TYPES`, prepaid sizes, due-day bounds) + `FinanceConfig` +
  `quantize_money` (THE canonical 2dp ROUND_HALF_UP rule).
- `periods.py` — stdlib calendar-month helpers (`add_months`, `month_range`,
  `due_date_for`, `period_key`/`unpack_period`, `period_of`). No `dateutil`.
- `repository.py` — SQL-only, `society_id`-scoped. Rate resolution, dues
  (idempotency + `outstanding_dues(lock=True)` FOR UPDATE), payments/allocations,
  prepaid blocks, categories/expenses, ledger (`reserve_balance`, `list_ledger`),
  and the analytics aggregates pushed to the DB (`collection_totals`,
  `collection_by_house`, `arrears_by_house`, `expense_by_category`,
  `total_by_entry_type`, `reversal_totals_by_reversed_type`,
  `ledger_monthly_totals`).
- `service.py` — thin `FinanceService` facade over the concern split; exposes the
  inter-module contract shortcuts (`outstanding_dues`, `record_payment`,
  `generate_due_cycle`, `has_dues`).
- `services/support.py` — shared internals: `money` (delegates to
  `quantize_money`), `load_config`, `society_currency`, `ensure_default_categories`
  (lazy idempotent seed of the 7 system categories — no platform edit),
  `post_ledger_entry` (the single ledger-write choke-point).
- `services/rates.py` — set (new row, never edits history; dup valid_from → 409),
  current+history read, rate-for-month resolution, rate-change preview projection.
- `services/dues.py` — `generate_due_cycle`: idempotent per-house monthly
  materialization at the effective rate, backfills from `first_left_empty_on`,
  skips existing/no-rate months; `has_dues`. Consumes `HouseService.houses_owing`.
- `services/collection.py` — `get_house_dues`/`outstanding_total` reads;
  `record_payment` (oldest-first whole-month allocation, FOR UPDATE),
  `record_prepaid` (materialize-then-arrears-check, future window
  `max(current, latest+1)`, locked rate, house-tied), `void_payment` (re-open
  dues + reversing entry; unwinds a prepaid block).
- `services/expenses.py` — categories (list seeds defaults, add), `record_expense`
  (outflow entry), `void_expense` (reversal); paginated list + `include_voided`.
- `services/reserve.py` — computed balance read; `post_entry` (fixed/adjustment
  direction; house-link validated in-tenant), `reverse_entry` (negating entry,
  blocks system/reversal/already-reversed), `reconcile` (adjustment for the diff;
  zero-diff → 422).
- `services/analytics.py` — collection / arrears / expenses / income / trends;
  income & trends net reversals against the type they undo (matches reserve
  balance); voided expenses excluded from by-category.
- `services/jobs.py` — `run_daily_dues_generation` worker scan (owns its session,
  commit-per-society, failure-isolated) + testable `_run_for_societies` helper.
- `api.py` — public inter-module contract: `outstanding_dues`, `outstanding_total`,
  `has_dues`, `maintenance_due_day`, `record_payment`, `generate_due_cycle`.
- `router.py` — 19 thin `/finance/*` routes, dual-gated `require_module('finance')`
  + permission (read/manage_rate/record_payment/manage_expenses/manage_reserve).
- `spec.py` — `FINANCE_SPEC` (`depends_on: ['houses']`, 5 perms, `default_config`
  = `{maintenance_due_day, prepaid_blocks}`, admin=all / resident=read).
- `alembic/versions/0005_finance.py` — migration (chained off `0004_vault`); the 8
  tables + indexes. No FK cascade (append-only convention).

Consumer/provider wiring (House & Occupancy):
- `app/modules/houses/{repository,service}.py` — added `houses_owing(society_id)`
  (`(house_id, first_left_empty_on)` for status != empty), `house_by_number`,
  `house_exists` — consumed by Finance via the service interface, never tables.

Foundation touchpoints:
- `app/main.py` — registers `register_finance` + mounts the router; the
  `RequestValidationError` handler now runs `exc.errors()` through
  `jsonable_encoder` (custom field_validator errors were 500ing on `json.dumps`).
- `alembic/env.py` — imports finance models. `app/worker/entrypoint.py` — schedules
  the daily dues scan (02:00 UTC). `tests/conftest.py` — `_reset_db` disposes the
  app engine pool before TRUNCATE (avoids the idle-reader-lock deadlock the 8 new
  tables widened).

## Functions (summary · deps · @location)
- `RatesService.set_rate` — new effective-dated row (dup valid_from → 409), audits
  `finance.rate_set`. deps: repo.add_rate, AuditService. @ services/rates.py
- `RatesService.rate_amount_for_month / preview` — rate resolution + projection
  (proposed × houses_owing vs current). deps: repo.rate_for_month, HouseService. @ rates.py
- `DuesService.generate_due_cycle` — idempotent monthly materialization, backfill,
  skip existing/no-rate. deps: HouseService.houses_owing, RatesService, repo. @ dues.py
- `CollectionService.record_payment` — oldest-first whole-month settle, allocations,
  collection inflow, audit. deps: repo.outstanding_dues(lock), post_ledger_entry. @ collection.py
- `CollectionService.record_prepaid` — materialize-then-arrears, future window, locked
  rate, block. deps: DuesService.generate_due_cycle, repo. @ collection.py
- `CollectionService.void_payment` — re-open dues, reversing entry, unwind prepaid
  block. deps: repo.collection_entry_for_payment, prepaid_block_for_payment. @ collection.py
- `ExpensesService.record_expense / void_expense / add_category` — expense outflow /
  reversal / category. deps: ensure_default_categories, post_ledger_entry. @ expenses.py
- `ReserveService.post_entry / reverse_entry / reconcile` — manual ledger entry (house
  link tenant-checked) / negating entry / bank-diff adjustment. deps: repo, HouseService.house_exists. @ reserve.py
- `AnalyticsService.{collection,arrears,expenses,income,trends}` — DB-aggregate reads,
  reversal-netting. deps: repo aggregates. @ analytics.py
- `run_daily_dues_generation` — worker scan, per-society commit/isolation. deps:
  SessionLocal, FinanceService.generate_due_cycle. @ services/jobs.py
- `finance.api.*` — cross-module contract other modules import. @ api.py

## Tables owned
`maintenance_rates`, `house_dues`, `payments`, `payment_allocations`,
`prepaid_blocks`, `expense_categories`, `expenses`, `ledger_entries`.

## Endpoints
Rate: `GET /finance/rate` · `POST /finance/rate` · `GET /finance/rate/preview?amount=`.
Collection: `GET /finance/houses/{id}/dues` · `POST /finance/houses/{id}/payments` ·
`POST /finance/houses/{id}/prepaid` · `POST /finance/payments/{id}/void`.
Expenses: `GET/POST /finance/expense-categories` · `GET /finance/expenses`
(`{items,total}` + `include_voided`) · `POST /finance/expenses` ·
`POST /finance/expenses/{id}/void`.
Reserve: `GET /finance/reserve` · `POST /finance/reserve/entries` ·
`POST /finance/reserve/entries/{id}/reverse` · `POST /finance/reserve/reconcile`.
Analytics: `GET /finance/analytics/{collection|arrears|expenses|income|trends}`.
Worker trigger: `POST /finance/dues/generate` (on-demand, `finance.manage_rate`).
All dual-gated `require_module('finance')` + permission. Society always from the JWT.

## Audited actions (emitted)
`finance.rate_set` · `finance.payment_recorded` / `finance.payment_voided` ·
`finance.prepaid_recorded` · `finance.expense_recorded` / `finance.expense_voided` ·
`finance.category_added` · `finance.reserve_entry_posted` /
`finance.reserve_entry_reversed` · `finance.reserve_reconciled`. All in-transaction.

## Cross-module wiring
- **Consumes:** House & Occupancy (`houses_owing`, `house_by_number`,
  `house_exists` via `HouseService`); Onboarding house registry (numbering/display
  via houses); foundation `TenantContext` + `AuditService` + the worker;
  `societies.currency` + `society_modules.config`.
- **Provides:** `outstanding_dues`, `outstanding_total`, `has_dues`,
  `maintenance_due_day`, `record_payment`, `generate_due_cycle` (`finance/api.py`).
- **Deferred wiring (skeleton-then-wire):** the Notifications `maintenance_due`
  reminder rule will consume `outstanding_dues` + `maintenance_due_day` when
  Notifications is built; Finance emits no reminder itself (cadence lives in
  Notifications). A future PaymentProvider gateway plugs in behind
  `payments.provider` with no finance-core change.

## Testing
Reuses the shared harness (`backend/tests/`): isolated per-worker `society_test`
DBs, truncate+reseed, existing fixtures + `tests/_finance_helpers.py`. 90 finance
tests across the 7 feature files + the Phase-3 gate (cross-module e2e, config,
security/vulnerability, deep edge cases). Run:
`docker compose exec backend bash scripts/run-tests.sh`.

## Deviations from design (drift vs docs/modules/finance.md)
1. **Default expense categories** are seeded LAZILY on first use of the expenses
   feature (`ensure_default_categories`) rather than at module-enable — the enable
   flow is shared/foundation-owned and must not be edited per-module (matches the
   "zero edits to existing modules" rule); functionally equivalent.
2. **`GET /finance/expenses`** returns a paginated envelope `{items, total}` (+
   `include_voided`) rather than a bare list, for consistent client paging.
3. **Zero-diff reconcile** returns 422 (no phantom zero entry) — the contract is
   "an adjustment is posted only when there is a difference."
4. **Voiding a prepaid payment** deletes the `prepaid_blocks` row and resets its
   dues to `source=accrued` (the coverage no longer exists); the money trail stays
   in the payment + reversal ledger entries.
5. **Foundation validation-handler fix** shipped with this module (custom
   field_validator errors now render 422, not 500) — required for finance's money/
   method validators over HTTP; benefits every module.

Everything else matches `docs/modules/finance.md`.
