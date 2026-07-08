# Finance — Phase 3 Test-Gate Matrix (cross-cutting + E2E gaps)

> Test-DESIGN document. Do not confuse with the existing 90 per-feature wave tests
> (`test_finance_{rates,dues,collection,expenses,reserve,analytics,worker}.py`).
> This matrix specifies ONLY the gaps those wave tests miss: full cross-module
> lifecycle, contract-surface, module-enable/config/permission wiring, deep edge
> cases, security/IDOR beyond the per-feature 403s, concurrency/idempotency, and
> the void-transparency invariant. Every spec below is NEW.

## How to read this

Each row: **Test name** · **Setup / steps** · **Expected assertion** · **Prio**
(P1 must-have / P2 valuable / P3 nice). File placement is suggested per section.

## Shared helper to build first (`tests/_finance_helpers.py`)

The 7 wave files each re-declare `_enable_finance`, `_setup`, `_set_rate`,
`_second_society`, etc. The E2E and cross-cutting specs below need ONE reusable
full-stack helper module. Put these in `tests/_finance_helpers.py`:

- `enable_finance(db, society, superadmin, *, config=None)` — enable
  onboarding+houses+vault+finance in one `set_modules` call (finance
  `depends_on: houses`); commit. (Lift from `test_finance_collection._enable_finance`,
  add vault.)
- `finance_admin_bearer(auth, admin_user)` — reuse `_houses_helpers._admin_bearer`.
- `set_rate_http(auth, hdr, amount, valid_from)` — POST `/finance/rate` (real path,
  not a DB insert) so E2E exercises the write.
- `owned_house(auth, hdr, **owner)` / `rented_house(...)` — onboarding→map→status,
  return house id (wrap `_houses_helpers`).
- `second_society_with_finance(db, superadmin, auth, *, email)` — create society B,
  provision its admin, enable finance, return `(soc_b, admin_b, hdr_b)`. (Lift the
  block duplicated at `test_finance_collection.py:577`.)
- `resident_bearer(auth, resident_user)` — must-change dance for a resident.
- `audit_actions(db, society_id)` — list of `(action, entity_type, entity_id)` for a
  society, for full-trail assertions.
- `reserve_balance(db, society_id)` — `FinanceRepository(db).reserve_balance(...)`.
- A `FROZEN_TODAY` date constant and a monkeypatch helper for `app.common.time.utcnow`
  so period-boundary specs are date-deterministic (see §4/§6 notes).

---

## 1. END-TO-END across ALL modules (highest priority — largely missing)

Suggested file: `tests/test_finance_e2e.py`. No existing test walks
onboarding→houses→finance(+vault) as one journey. These assert the cross-module
WIRING on real data (finance consuming `houses_owing` / `house_exists`, not
hand-inserted dues).

| Test name | Setup / steps | Expected assertion | Prio |
|---|---|---|---|
| `test_full_society_finance_lifecycle` | Super-admin creates society → enable onboarding+houses+vault+finance → admin does must-change → onboarding maps a building with 3 houses → set 2 houses `owned`/`rented` (1 left `empty`) → POST `/finance/rate` → POST `/finance/dues/generate` → GET each owning house's dues → pay oldest month on house1 → pay_all on house2 → record an expense → post an opening reserve entry → GET `/finance/analytics/collection` & `/reserve`. | Each step returns 200/2xx. Dues generated ONLY for the 2 non-empty houses (empty house has zero dues rows). Analytics `expected` = rate × 2 houses × months; `collected` reflects the 2 payments; reserve balance = collections + opening − expense. This is the spine E2E. | P1 |
| `test_finance_consumes_real_houses_owing` | Onboard 4 houses; move 2 to `owned`, 1 to `rented`, leave 1 `empty`; set rate; `POST /finance/dues/generate`. | Exactly 3 houses (owned+owned+rented) get a due; the empty house gets none — proving `DuesService` reads `HouseService.houses_owing` on live data, not a fixture. `rate/preview` reports `dues_owing_houses == 3`. | P1 |
| `test_first_left_empty_on_drives_backfill_start` | Freeze today to month M. Onboard a house, move it off empty in month M−3 (drive by setting `house.first_left_empty_on` through the real status change earlier, or monkeypatch `utcnow` across two status changes). Set a rate valid_from ≤ M−3. Generate. | 4 dues created (M−3 … M), each dated on the due day — confirming finance backfills from the houses-module `first_left_empty_on`, not from "today". | P1 |
| `test_resident_reads_their_house_dues_via_login` | E2E setup; owner provisioned by the `owned` status change logs in (must-change dance) and GETs `/finance/houses/{their_hid}/dues`. | 200 with their outstanding months + total; confirms the resident `finance.read` grant seeded on enable actually lets an owner read dues through a real login. **See the ownership-scope FINDING below — also GET a DIFFERENT house's dues and record the actual status (currently 200, not 403).** | P1 |
| `test_prepaid_then_owner_replaced_months_stay_paid` | E2E: house owned, clear arrears, buy a 6-month prepaid block. Then replace the owner via `/houses/{id}/status` (new email). GET `/finance/houses/{id}/dues`. | The prepaid-covered future months remain `paid`/`source=prepaid` under the new owner — cross-module proof that prepaid is house-tied (spec §10) and survives a houses-module owner replacement. | P2 |
| `test_full_void_reconcile_analytics_trail` | E2E: generate dues, pay_all house1, record+void an expense, post+reverse a reserve deposit, reconcile to a bank figure, then void the house1 payment. GET `/reserve`, `/analytics/income`, and query `audit_log`. | Reserve balance matches the net after all reversals+reconcile; income analytics is net of the voids; `audit_log` for the society contains the full ordered trail: `society.created`, `module.allocated`×N, `finance.rate_set`, `finance.payment_recorded`, `finance.expense_recorded`, `finance.expense_voided`, `finance.reserve_entry_posted`, `finance.reserve_entry_reversed`, `finance.reserve_reconciled`, `finance.payment_voided`. | P1 |
| `test_e2e_vault_and_finance_coexist` | Enable vault+finance together; upload an owner ID-proof (houses→vault) AND record a payment for the same house. | Both succeed independently; no table/route collision; each module's audit rows present. Confirms finance enable doesn't disturb the vault wiring exercised elsewhere. | P3 |

**FINDING (feed into implementer + a bug ticket):** `GET /finance/houses/{house_id}/dues`
(`router.py:115`) gates only on `finance.read` with NO occupancy-ownership check — a
resident can read ANY house's dues in their society, contradicting docs §2 ("Owners
may read **their own** house's dues"). The E2E resident test should assert the CURRENT
behavior (likely 200 for a foreign house) and be flagged, not written to a wished-for
403, so the matrix records reality. Recommend a follow-up decision: scope the read to
the caller's owned house(s) via `HouseService.current_owner_user_ids` / occupancy.

---

## 2. CROSS-MODULE CONTRACT (`app.modules.finance.api` called as other modules do)

Suggested file: `tests/test_finance_contract.py`. Call the `api.py` delegators
DIRECTLY on a `db` session (the way Notifications/Onboarding/worker/gateway will),
not over HTTP. The wave dues test touches `generate_due_cycle`/one `outstanding_dues`
path; the rest of the contract surface is unexercised as a contract.

| Test name | Setup / steps | Expected assertion | Prio |
|---|---|---|---|
| `test_api_outstanding_dues_and_total_match_reads` | Build owing house + dues via service; call `finance_api.outstanding_dues(db, sid, hid)` and `outstanding_total(db, sid, hid)`. | `outstanding_dues` returns a `HouseDuesOut` whose `outstanding_total` equals `outstanding_total(...)` and equals Σ of the outstanding rows — the exact data the Notifications `maintenance_due` rule will consolidate. | P1 |
| `test_api_has_dues_backs_onboarding_delete_guard` | House with an outstanding due → `has_dues` True. Pay_all → `has_dues` False. Prepaid-only house (no arrears) → False. | `has_dues` flips correctly; documents the boolean Onboarding's future delete-guard depends on (spec §7). | P1 |
| `test_api_maintenance_due_day_reads_config` | Enable finance with default config → `maintenance_due_day(db, sid) == 1`. Re-enable/override config `{"maintenance_due_day": 15}` → returns 15. | Confirms the value Notifications reads to align cadence is the per-society config, testable WITHOUT Notifications built. | P1 |
| `test_api_record_payment_joins_caller_txn` | Call `finance_api.record_payment(db, sid, hid, PaymentRecordRequest(...), actor_user_id=admin.id)` on a raw session; do NOT commit; assert within the same session the due is `paid` and a `collection` entry exists; then rollback and assert nothing persisted in a fresh session. | The delegator uses the caller's transaction (docs api.py docstring) — a gateway calling it participates in the caller's unit of work. | P2 |
| `test_api_generate_due_cycle_as_of_and_actor` | Call `generate_due_cycle(db, sid, as_of=date(Y,M,D), actor_user_id=None)`; then again. | Returns count created first run, 0 on the idempotent second run; `as_of` controls the period (worker/backfill contract §9). | P2 |
| `test_overdue_signal_standalone` | Create an outstanding due with `due_date` in the past (via `as_of`/frozen date); read `outstanding_dues`. | Each returned `HouseDueOut.is_overdue` is True for past-due, False for a future due date — the overdue signal Notifications consumes is correct standalone (no Notifications module needed). | P2 |
| `test_config_maintenance_due_day_range_validation` | Load `FinanceConfig` (via `load_config`) for configs `{"maintenance_due_day": 0}`, `{29}`, `{15}`. | 0 and 29 raise validation (range 1–28, schemas.py `MIN/MAX_DUE_DAY`); 15 is accepted. Guards the config contract without a route. | P2 |
| `test_config_prepaid_blocks_validation` | `load_config` with `{"prepaid_blocks": []}` and `{"prepaid_blocks": [0,3]}` and `{"prepaid_blocks":[6]}`. | empty and non-positive raise; `[6]` accepted — the config that drives `record_prepaid` acceptance. | P3 |

---

## 3. MODULE-ENABLE / CONFIG / PERMISSIONS-SEEDING

Suggested file: `tests/test_finance_enable.py`. The wave files assume finance is
enabled and admin has perms; none verify the enable→seed→login chain, depends_on,
config-override behavior change, or the module-disabled 403 on the routes.

| Test name | Setup / steps | Expected assertion | Prio |
|---|---|---|---|
| `test_enable_finance_requires_houses_dependency` | On a fresh society, `set_modules([finance enabled])` WITHOUT houses enabled. | Raises/HTTP 409 `depends_on` (finance needs houses) — via `MODULE_REGISTRY.resolve_dependencies`. Then enabling houses+finance together succeeds. | P1 |
| `test_enable_seeds_admin_all_five_perms_via_login` | Enable finance; admin logs in (must-change), then exercises one route per permission: POST `/finance/rate` (manage_rate), POST a payment (record_payment), POST an expense (manage_expenses), POST a reserve entry (manage_reserve), GET analytics (read). | All 5 succeed — proving `default_role_permissions.society_admin` (all 5) is granted on enable and reaches a real JWT. | P1 |
| `test_enable_seeds_resident_read_only_via_login` | Enable finance; resident logs in; GET `/finance/reserve` (read) succeeds; POST `/finance/rate` and POST payment → 403. | Resident got exactly `finance.read` on enable — read yes, every mutation 403. | P1 |
| `test_module_disabled_403_on_every_finance_route` | Society with onboarding+houses enabled but finance NOT enabled; admin has a valid token. Hit each `/finance/*` route (GET rate, POST rate, GET dues, POST payment, POST prepaid, POST void, GET/POST expenses & categories, GET reserve, POST reserve entry/reverse/reconcile, all 5 analytics, POST dues/generate). | Every route → 403 `module_disabled` (the `require_module('finance')` gate fires before permission). Parametrize over the route list. | P1 |
| `test_disable_finance_revokes_route_access` | Enable finance, confirm a route works; then `set_modules([finance disabled])`; re-login; hit a `/finance/*` route. | 403 module_disabled after disable — the toggle is honored live. | P2 |
| `test_config_custom_due_day_changes_generation` | Enable finance with `config={"maintenance_due_day": 15}`; owning house; rate set; generate. | Generated dues have `due_date` day == 15 (not the default 1) — proves per-society config override actually changes behavior end-to-end (complements the dues wave which sets 15 directly; this drives it through `set_modules` config). | P2 |
| `test_config_custom_prepaid_blocks_changes_acceptance` | Enable finance with `config={"prepaid_blocks":[6]}`; clear arrears; POST `/finance/houses/{id}/prepaid` months_count=3 → 422; months_count=6 → 200. | Per-society prepaid config overrides the default `[3,6,9,12]` through the real enable path. | P2 |
| `test_reenable_is_idempotent_no_duplicate_grants` | Enable finance twice (same config). | Second enable is a no-op for permissions (no duplicate `role_permissions`), no `module.toggled` spam — `set_modules` idempotency for the finance grant set. | P3 |

---

## 4. DEEP EDGE CASES not in wave tests

Suggested file: `tests/test_finance_edge.py`. Use `as_of`/frozen-date control for
determinism. The wave dues test covers simple backfill + idempotency + a mid-history
skip; these push the boundaries.

| Test name | Setup / steps | Expected assertion | Prio |
|---|---|---|---|
| `test_rate_effective_boundary_valid_from_equals_period_start` | Rates: R1 valid_from `2024-01-01`, R2 valid_from `2024-03-01`. Owning house, `first_left_empty_on` in Jan; generate through March. | Jan+Feb dues at R1, March due at R2 (rate for month M = latest valid_from ≤ first-of-M; boundary month uses the NEW rate). Asserts `amount_due` per period. | P1 |
| `test_due_generated_in_month_of_mid_history_rate_change` | Two rates as above; generate only March. | The March due picks R2 (valid_from == period start), not R1 — the exact ≤ boundary in `rate_for_month`. | P1 |
| `test_backfill_across_year_boundary_dec_to_jan` | Frozen today = `Feb 2025`; `first_left_empty_on` = `2024-12-xx`; single rate valid from `2024-12-01`; generate. | Dues for `2024-12`, `2025-01`, `2025-02` (3 rows) — `month_range` / `add_months` cross the Dec→Jan boundary correctly. | P1 |
| `test_backfill_across_year_boundary_and_rate_change` | As above but R1 from `2024-12-01`, R2 from `2025-02-01`. | Dec+Jan at R1, Feb at R2 — year-cross AND rate-cross together. | P2 |
| `test_multi_house_generation_bulk_correct_no_n_plus_one` | Onboard ~30 houses, all owning, one rate, generate once. Wrap the call in a SQLAlchemy statement counter (event listener on `after_cursor_execute`). | 30 dues created; SELECT count is bounded (roughly per-house `existing_periods` + `houses_owing` + rate lookups — assert it does NOT scale as O(houses × months) unexpectedly, e.g. < a small constant × houses). At minimum assert bulk correctness (all 30 present, amounts equal). | P2 |
| `test_generation_idempotent_at_scale` | 30 houses generated; run again. | 0 created on the second pass; no duplicate `house_dues` (the UNIQUE(society,house,period) holds and the service skips existing). | P2 |
| `test_prepaid_window_spans_year_boundary` | Frozen today = `Nov 2024`; clear arrears; buy a 6-month block. | Covered periods = `2024-11 … 2025-04` (6 rows), all `paid`/`source=prepaid`/`locked_rate`, `prepaid_blocks.start_period=202411`, `end_period=202504` — window crosses the year. | P1 |
| `test_oldest_first_allocation_across_years` | Dues for `2024-11,2024-12,2025-01,2025-02` outstanding; pay `months=3`. | The three OLDEST (2024-11,12,2025-01) settle, 2025-02 stays outstanding — oldest-first ordering is by (year,month), not id, across a year boundary. | P1 |
| `test_reserve_balance_long_mixed_ledger_with_multiple_reversals` | Post a long sequence: opening, several deposits/interest/income inflows, several expenses (outflows), collections, then reverse 2 different entries and void 1 expense. | Computed `reserve_balance` == hand-computed Σ inflow − Σ outflow over ALL rows (reversals included as ordinary negating entries); GET `/reserve` `total` counts every row incl. reversals. | P2 |
| `test_analytics_trends_many_months` | Build collections+expenses across ≥6 distinct months (via `occurred_on`/`incurred_on`), including one void dated back to an earlier month. | `/analytics/trends` returns one point per month, oldest→newest, each `net = collected − expense`, and the back-dated void reduces its ORIGINAL month (not the void month) — matches `ledger_monthly_totals` bucketing. | P2 |
| `test_reconcile_when_computed_balance_negative` | Post outflows exceeding inflows so computed balance is negative (e.g. opening 100, expense 500 → −400). Reconcile to actual_balance 0. | An `adjustment` INFLOW of 400 posts; new balance = 0; audit `difference` = "400.00", direction inflow. Reconcile handles a negative starting balance. | P2 |
| `test_house_returned_to_owned_after_period_gap` | House owning from M−4 with dues; (no way to set empty via API — houses forbids return-to-empty). Instead: verify a house that was owning keeps accruing every month with no gaps when generate runs across several months. | No missing months, no duplicates — steady-state monthly accrual across multiple generate calls with advancing `as_of`. | P3 |

---

## 5. SECURITY / VULNERABILITY (beyond per-feature 403 checks)

Suggested file: `tests/test_finance_security.py`. Wave files have per-feature 403s
and a few cross-society voids/expenses; these systematize IDOR across EVERY
id-scoped route, JWT cross-tenant, must-change lockout, and the HTTP-level
422-vs-500 money fix.

| Test name | Setup / steps | Expected assertion | Prio |
|---|---|---|---|
| `test_bad_money_post_returns_422_not_500` | Enable finance; admin bearer. POST `/finance/expenses` with `amount: -5`, then `amount: 0`, then `amount: "1.234"` (sub-cent), then a huge `amount` > NUMERIC(12,2). Also POST `/finance/rate` with `amount: 0` and a non-month-aligned `valid_from`, and a payment with `method: "wire"`. | EVERY malformed body → **422** with `{code:"validation_error"}`, NOT 500. This explicitly verifies the `jsonable_encoder` fix in `main.py:_validation_handler` now renders custom `field_validator` errors as 422 (the rates wave documented this as a KNOWN 500 defect — this test is the regression gate that it is fixed). | P1 |
| `test_negative_and_zero_payment_amount_guarded` | Payment has no client `amount` (derived from months), so instead assert `months: 0` and `months: -1` on `/payments` → 422 (schema `ge=1`), and prepaid `months_count: -3` → 422. | Money-adjacent inputs on the collection routes can't go negative/zero. Fills the gap that only expenses/reserve had negative tests. | P2 |
| `test_cross_society_dues_read_is_404` | Society A owning house with dues; society B admin bearer. GET `/finance/houses/{A_hid}/dues` as B. | 404 (id-scoped resource in another tenant is not found) — the dues READ path had no IDOR test. | P1 |
| `test_cross_society_pay_and_prepaid_404` | As above, B POSTs `/finance/houses/{A_hid}/payments` and `/prepaid`. | 404 for both — pay/prepaid endpoints reject a foreign house id (only void had this before). | P1 |
| `test_idor_across_all_id_scoped_ids` | Parametrize: A creates a payment, expense, ledger entry, category, house due; B (finance-enabled) tries `/payments/{A_pid}/void`, `/expenses/{A_eid}/void`, `/reserve/entries/{A_entry}/reverse`, and reserve entry with `source_type=house source_id={A_hid}`. | Each → 404 (id belongs to another society) — one systematic IDOR sweep over payment_id/expense_id/entry_id/house_id. | P1 |
| `test_jwt_perms_for_society_a_rejected_against_society_b` | Mint a token (via `make_token`) carrying A's admin role_ids but `active_society_id = B`. Hit a `/finance/*` mutation. | 403 (role_ids don't grant the permission in B's context) or module_disabled — a stolen/mismatched token can't act cross-tenant. | P2 |
| `test_resident_read_allowed_all_mutations_403` | Resident bearer (finance.read only). GET rate/reserve/analytics/expenses succeed; POST rate, payment, prepaid, void, expense, category, reserve entry/reverse/reconcile, dues/generate ALL → 403. | One comprehensive resident-mutation-lockout sweep (wave files spot-check a couple). | P1 |
| `test_manage_rate_and_dues_generate_require_perm` | A user with finance.read but not manage_rate: POST `/finance/rate` → 403; POST `/finance/dues/generate` → 403. | Closes the gap that rate + dues-generate had NO 403 test. | P2 |
| `test_must_change_password_locks_finance_routes` | Provision admin (password_state=must_change), log in but do NOT change password; use that token on a `/finance/*` route. | Blocked (403/redirect to change-password) — must-change lockout applies to finance routes like every other. | P1 |
| `test_sequential_ids_do_not_leak_cross_tenant` | A and B each create payments so ids interleave (A gets id 1,3; B gets 2,4 or similar). B GET/void A's id and vice-versa. | Guessing a neighboring sequential id never reaches another tenant's row (404) — validates the "scoping not hiding" defense (docs/03 §5). | P2 |

---

## 6. CONCURRENCY / IDEMPOTENCY

Suggested file: `tests/test_finance_concurrency.py`. True parallel DB races are hard
in this harness (truncate-per-test, single connection); assert the FOR-UPDATE path's
sequential correctness + idempotency, and note where a genuine 2-session test is
possible via a second `SessionLocal`.

| Test name | Setup / steps | Expected assertion | Prio |
|---|---|---|---|
| `test_generate_due_cycle_twice_no_duplicates` | Owning house, rate, `generate_due_cycle` → N; call again same `as_of`. | Second call returns 0; row count unchanged; UNIQUE(society,house,period) never violated — idempotency (broader than the dues wave's single-house case: assert at DB row level for multiple houses/months). | P1 |
| `test_worker_creates_nothing_when_due_day_ne_today` | Finance-enabled society config due_day=15; run `_run_for_societies([sid], as_of=date(...,10))`. | 0 dues, society not "processed" — the worker's due-day gate (already partly covered; include here as the idempotency/no-op guard for the concurrency file's completeness). | P2 |
| `test_double_void_returns_409` | Record a payment; void it; void again. | Second void → 409 `already voided`; only ONE reversal ledger entry exists; due stays outstanding once. (Collection wave has a single-flow double-void; here also assert exactly one reversal row + net balance.) | P2 |
| `test_second_payment_cannot_resettle_same_month` | Dues for M1,M2 outstanding; pay `months=1` (settles M1). Then pay `months=1` again. | The second payment settles M2 (the next oldest), NOT M1 again; M1 has exactly one allocation; you can never double-collect a settled month. After both, a third `months=1` → 422 (nothing outstanding). Proves the oldest-first + settled-exclusion path (the `FOR UPDATE` guard's observable sequential effect). | P1 |
| `test_concurrent_settle_same_month_two_sessions` (optional/if feasible) | Open two `SessionLocal`s; both read outstanding for the same house with `outstanding_dues(lock=True)`; both attempt to settle M1. | The row lock serializes them: one settles M1, the second either blocks then finds M1 paid (settles next / raises) — never two allocations for M1. Mark P3 and skip-guard if the harness can't hold two live connections against the truncate reset. | P3 |

---

## 7. TRANSPARENCY INVARIANT (voids/reversals stay visible)

Suggested file: fold into `tests/test_finance_e2e.py` or a `test_finance_transparency.py`.
Wave files assert a single void's reversal visibility; these assert the FULL invariant
across list endpoints + analytics exclusion in combination.

| Test name | Setup / steps | Expected assertion | Prio |
|---|---|---|---|
| `test_voided_payment_original_and_reversal_both_in_reserve_and_ledger` | Pay a month; GET `/reserve`; void the payment; GET `/reserve` again. | After void, BOTH the original `collection` inflow AND the `reversal` outflow appear in the ledger `entries`; original row's `is_reversed == True`; the reversal's `reverses_entry_id` == original id; net `balance` returns to pre-payment value. | P1 |
| `test_voided_expense_both_visible_reserve_nets` | Record expense; void it. GET `/reserve`. | Original `expense` outflow + `reversal` inflow both present; original `is_reversed`; balance net-zero for that pair. | P1 |
| `test_voided_payment_still_in_lists_but_excluded_from_recorded_analytics` | Pay_all; void the payment; GET `/analytics/income` and `/analytics/collection`. | The voided payment's collection is NETTED OUT of income (`total_collection` reduced) and the dues re-open so `collection` summary `collected` drops / `outstanding` rises — while the ledger still SHOWS both entries. "Visible in reports, excluded from recorded totals." | P1 |
| `test_voided_expense_visible_in_list_excluded_from_expense_analytics` | Record 2 expenses; void 1. GET `/finance/expenses` (default `include_voided=True`) and `?include_voided=false`, and `/analytics/expenses`. | Default list shows BOTH (incl. voided, status=voided); `include_voided=false` shows 1; analytics `total_expense` and `by_category` count only the 1 recorded — matches `expense_by_category` filtering `status='recorded'`. | P1 |
| `test_reversed_reserve_entry_visible_and_flagged` | Post a deposit; reverse it. GET `/reserve`. | Deposit + reversal both listed; deposit `is_reversed=True`; balance nets to pre-deposit; `total` counts both rows. | P2 |
| `test_reconcile_adjustment_visible_in_ledger` | Reconcile to a differing bank figure. GET `/reserve`. | The `adjustment` entry is visible with its direction/amount and moves the balance to the actual figure; nothing hidden. | P3 |

---

## Priority roll-up

- **P1 (must-have, ~19):** the E2E spine + real-`houses_owing` + backfill-start +
  resident-read-via-login + full-void-audit-trail (§1); the 3 contract signals
  (outstanding/has_dues/due_day) (§2); depends_on + both seed-via-login + module-disabled
  sweep (§3); rate boundary ×2 + year-cross backfill + prepaid year-cross + oldest-first
  cross-year (§4); 422-not-500 + cross-society dues read + pay/prepaid IDOR sweep +
  resident-mutation-lockout + must-change lockout (§5); generate-idempotent +
  second-payment-can't-resettle (§6); the 3 core transparency asserts (§7).
- **P2 (valuable, ~17):** config-override-changes-behavior, contract txn-join + overdue
  signal + config range validation, multi-house bulk/no-N+1, long-ledger balance,
  trends-many-months, negative reconcile, JWT cross-tenant, sequential-id leak,
  perm-for-rate/dues, double-void net, reversed-entry-visible, etc.
- **P3 (nice, ~6):** vault+finance coexist, prepaid_blocks config validation,
  steady-state accrual, reconcile-visible, two-session lock race (skip-guarded).

Target ~40 new specs. Build `tests/_finance_helpers.py` first; §1 and §5 give the
highest signal per test.

## Cross-cutting findings for the implementer (not tests, but shape the tests)

1. **Ownership scope gap** (§1 finding): `/finance/houses/{id}/dues` doesn't restrict a
   resident to their own house. Assert current behavior; flag for a product decision.
2. **HTTP 422 regression gate** (§5): the `main.py` `jsonable_encoder` fix must be
   asserted over HTTP — the rates wave only tests custom validators at the schema level
   because the handler historically 500'd. This is the single most important
   security/robustness regression test to add.
3. **Date determinism**: many §4/§6 specs depend on "current period". Monkeypatch
   `app.common.time.utcnow` (used by `dues.py`, `collection.py`, `jobs.py`) rather than
   real dates so specs are stable in CI. Provide the helper in `_finance_helpers.py`.
