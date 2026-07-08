# Finance (Module 4) — Code-Review Findings

Phase 2 gate: one expert reviewer (Opus 4.8, medium, read-only) over the frozen
core + 7 waves. All findings were APPLIED (none deferred). Full suite green (614)
after fixes + test realignment.

## Verified correct (no change needed)
Tenant isolation (every query `society_id`-scoped; society always from JWT;
cross-society id → 404); no raw SQL / injection; `Decimal` + `NUMERIC(12,2)`
everywhere, no float; allocation sum == payment amount; oldest-first + FOR UPDATE
on the collection path; void re-opens exactly the allocated dues + posts one
reversal (original retained, `is_reversed` set), double-void → 409; reserve
balance = Σinflow−Σoutflow in the DB; system collection/expense entries blocked
from manual reversal; audit keys match design §5, in-transaction; every route
dual-gated with the right permission; migration matches models (no drift);
worker owns its session + isolates per society; `periods.py` month arithmetic
(year rollover, clamping, boundaries) correct.

## Must-fix (applied)
- **M1 — Prepaid window could land in the past.** The window anchored at
  `latest_existing_due + 1`; for a house whose last due was long paid, this
  materialized historical months and left upcoming ones uncovered. Fixed to
  `max(current_period, latest+1)` so a block always covers FUTURE months.
- **M2 — Arrears check ran only against materialized rows.** Dues are generated
  lazily; a mid-cycle prepaid could slip past real (not-yet-generated) arrears and
  overlap a month about to accrue. Fixed: `record_prepaid` now runs
  `generate_due_cycle` first, so the "arrears cleared first" check sees the true
  owed set (from `first_left_empty_on`).

## Should-fix (applied)
- **S1/S2 — Payment-void reversal was dated `today`,** so per-period analytics
  counted a cross-month void's original gross in month A and its reversal in
  month B (overstating A, negative B). Fixed to date the reversal at the original
  collection's `occurred_on` — matching the expense-void path and
  `ledger_monthly_totals` bucketing.
- **S3 — Voiding a prepaid payment left `source=prepaid` dues + an orphan block.**
  Fixed: void unwinds the block (deletes the `prepaid_blocks` row, resets the
  re-opened dues to `source=accrued` / `locked_rate=NULL`); money trail preserved
  in the payment + reversal entries.
- **S5 — `GET /finance/expenses` discarded the pagination total.** Now returns
  `{items, total}` + an `include_voided` filter.
- **S6 — Money rounding was encoded twice** (`quantize_money` + `money`).
  Consolidated: `quantize_money` is canonical (2dp, ROUND_HALF_UP); `money`
  delegates.

## Nits (applied)
- **N1** — reconcile audit now records signed `difference` + `direction` + unsigned
  `amount` (fully describes the posted entry).
- **N2** — reserve `source_type=house` now validates the house belongs to the
  society (`HouseService.house_exists`) → 404 otherwise (tenant isolation on the
  link).
- **N3** — rate-preview projection money-wrapped.
- **N4** — expense-void router now types `ExpenseVoidRequest` (was
  `PaymentVoidRequest`; identical `reason` field).
- **N5** — pruned dead schema domain constants (`ALL_ENTRY_TYPES`, `DUE_STATUSES`,
  `DUE_SOURCES`, `PAYMENT_STATUSES`, `PAYMENT_PROVIDERS`, `EXPENSE_STATUSES`);
  column domains stay documented at their model columns + enforced via literals.

## Tests realigned to corrected behavior
7 wave tests updated (expenses envelope; prepaid future-window + materialize-then-
arrears; reserve house-link 404; reconcile audit shape), with stronger assertions
added (`include_voided` filter, non-existent-house → 404, future prepaid window).
