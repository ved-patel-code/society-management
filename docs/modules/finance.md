# Finance Module — Design

> Design doc. Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md) · [../platform/platform-foundation](../platform/platform-foundation.md) · [house-occupancy](house-occupancy.md) · [onboarding](onboarding.md)
>
> **Confirmed decisions baked in:** single society-wide **effective-dated** rate per house · dues **materialized** monthly by a worker on the society's **due day** (calendar-month periods) · reserve = **computed running ledger** (admin posts dated entries anytime; collections auto-add; reconcile posts an adjustment) · prepaid **3/6/9/12** months at **locked current rate**, arrears cleared first, tied to the house · **no partial-within-month**, per-month **oldest-first** + pay-all · expenses use **extendable predefined categories** + description · corrections via **void/reversal** (audit-preserving), and **voids/reversals stay VISIBLE in reports** for transparency · **full analytics** · **no late fees / waivers** (future) · payments admin-recorded behind a **PaymentProvider** interface (gateway future) · reminders handled by the separate **modular Notifications** system (Finance only emits due/overdue signals).

## 1. Purpose & scope
Run a society's money: set the maintenance **rate** (effective-dated), generate per-house monthly **dues**, **collect** payments (incl. prepaid), record **expenses** + **income**, maintain the **reserve** as a running ledger, and provide **analytics** incl. a rate-change preview.

**Out of scope (future):** late-fee penalties, discounts/waivers, online payment gateway (interface-ready), per-house-type rates, receipts/invoices PDF.

## 2. Audience & permissions
- **society_admin** (+ super_admin, + any future finance-staff role e.g. a "finance admin"). Owners may **read their own house's dues** (via occupancy link) — read-only; collection is admin-recorded in v1.
- Permissions (`finance.*`): `finance.read` (finance views + one's **own** house dues), `finance.read_all` (view **any** house's dues/collection — society-wide), `finance.manage_rate`, `finance.record_payment`, `finance.manage_expenses`, `finance.manage_reserve`.
- **Dues-read scope is data-driven** (docs/02 §4 — roles add with no code change): the "view any house's dues" capability is the `finance.read_all` permission, not a hardcoded role. `is_super_admin` bypasses (platform operator). A `finance.read`-only holder (resident) is restricted to a house they currently occupy. A new role (e.g. finance admin) gains society-wide dues by including `finance.read_all` in its grants.
- On enable: society_admin is granted all 6 `finance.*`; resident is granted `finance.read`.
- Gated `require_module('finance')` (`depends_on: houses`) + `require_permission(...)`.

## 3. Data model
`id` BIGINT PK, `created_at`, `updated_at`, `society_id`. Money = `NUMERIC(12,2)`. DB holds PK/FK/NOT NULL/UNIQUE only.

**maintenance_rates** (effective-dated) — `society_id`, `amount`, `valid_from` DATE (month-aligned), `created_by`. UNIQUE(`society_id`,`valid_from`). Rate for month M = latest `valid_from ≤ M`.

**house_dues** (materialized) — `society_id`, `house_id` FK, `period_year`, `period_month`, `amount_due`, `due_date` DATE (= due day of that month), `status`(outstanding|paid), `source`(accrued|prepaid), `locked_rate` NULL, `paid_at` NULL. UNIQUE(`society_id`,`house_id`,`period_year`,`period_month`). idx(`society_id`,`status`); idx(`house_id`,`status`).

**payments** — `society_id`, `house_id` FK, `amount`, `method`(cash|cheque|bank_transfer|online|other), `reference` NULL, `provider`(admin_manual|gateway), `provider_ref` NULL, `status`(recorded|voided), `recorded_by`, `paid_at`, `voided_by`/`voided_at`/`void_reason` NULL.

**payment_allocations** — `payment_id` FK, `house_due_id` FK, `amount_applied`. Maps a payment to the whole month(s) it settles (oldest-first).

**prepaid_blocks** — `society_id`, `house_id` FK, `months_count`(3|6|9|12), `rate_locked`, `payment_id` FK, `start_period`, `end_period`. Materializes the covered `house_dues` rows (`source=prepaid`, `locked_rate`).

**expense_categories** — `society_id`, `name`, `is_system` BOOL. Seeded defaults (Electricity, Water, Housekeeping, Security, Repairs, Salaries, Misc) + society-added. UNIQUE(`society_id`,`name`).

**expenses** — `society_id`, `category_id` FK, `amount`, `description`, `incurred_on` DATE, `recorded_by`, `status`(recorded|voided), void fields.

**ledger_entries** (the reserve backbone + transparency) — `society_id`, `entry_type`(opening|deposit|interest|resale_transfer|income|collection|expense|adjustment|reversal), `direction`(inflow|outflow), `amount`, `description`, `occurred_on` DATE, `source_type`/`source_id` NULL (payment/expense/prepaid/house), `recorded_by`, `reverses_entry_id` FK NULL, `is_reversed` BOOL. idx(`society_id`,`occurred_on`).
- **Reserve balance** = Σ inflows − Σ outflows over all entries. Every money movement posts one entry: a recorded payment → `collection` inflow; an expense → `expense` outflow; manual opening/deposit/interest/resale/income/adjustment → their entry. **Reversals post a negating entry** (references the original; both stay visible).

## 4. Business rules
**Which houses owe:** houses with `status != empty`, accruing from `first_left_empty_on` (from House & Occupancy). Empty houses never owe.

**Rate:** effective-dated; a month's due uses the rate whose `valid_from` is the latest ≤ that month. Setting a new rate = new row (never edits history).

**Dues generation (worker, on the society's due day):** for each dues-owing house, create the current month's `house_dues` at the effective rate with `due_date` = due day; idempotent (skips existing / prepaid-covered months); backfills missing past months from `first_left_empty_on`. Overdue = outstanding past `due_date`.

**Collection & payment allocation:**
- `GET` house dues → all outstanding months + total (the "enter house number → see dues" flow, resolved via Onboarding registry).
- A payment settles **whole months only, oldest-first**; **no partial within a month**. Admin can pay **one month, several, or all** outstanding. `payment_allocations` records the mapping; settled dues → `paid`; a `collection` ledger inflow is posted.

**Prepaid (3/6/9/12):** requires **arrears cleared first**. Pays the next N months at the **locked current rate**; materializes those `house_dues` (`source=prepaid`, `locked_rate`) as `paid` even if the rate later rises. After the window, months bill at the then-current rate. **Prepaid is tied to the house** — if the owner is replaced mid-window, those months stay paid.

**Reserve ledger:** admin posts dated inflow/outflow entries anytime (opening/added funds, interest, **resale lump sum** — optionally linked to a house, other income, expenses, adjustments). Balance is computed from the ledger. **Reconcile-to-bank** = post an `adjustment` entry for the difference.

**Corrections & transparency:** monetary fixes go through **void/reversal** — voiding a payment re-opens its dues and posts a reversing ledger entry; voiding an expense posts a reversal. Non-monetary fields (e.g. a description) are editable. **Reports and the ledger show the original AND its reversal** (net computed) — nothing is hidden. All of this is audited.

**Analytics (read-time):** collection summary (expected vs collected vs outstanding, society + per house), arrears list, expense-by-category + income + net, reserve balance + ledger history (incl. reversals), month-over-month trends, and **rate-change preview** = projected monthly collection at a proposed rate (rate × dues-owing houses) vs current — pure projection, nothing persisted.

## 5. Audited actions
Written to `audit_log` (in-transaction):
- `finance.rate_set` — new effective-dated amount + valid_from.
- `finance.payment_recorded` / `finance.payment_voided` — house, amount, allocations (+ void reason).
- `finance.prepaid_recorded` — house, months, locked rate.
- `finance.expense_recorded` / `finance.expense_voided`; `finance.category_added`.
- `finance.reserve_entry_posted` / `finance.reserve_entry_reversed` / `finance.reserve_reconciled`.

## 6. Endpoints (`/finance/*`, society from JWT)
- Rate: `GET /finance/rate` (current + history) · `POST /finance/rate` (set effective-dated) · `GET /finance/rate/preview?amount=` (projection). (`finance.manage_rate` / read)
- Collection: `GET /finance/houses/{id}/dues` (outstanding + history + total) · `POST /finance/houses/{id}/payments` (settle N oldest / all) · `POST /finance/houses/{id}/prepaid` (block) · `POST /finance/payments/{id}/void`. (`finance.record_payment`)
- Expenses: `GET/POST /finance/expenses` · `POST /finance/expenses/{id}/void` · `GET/POST /finance/expense-categories`. (`finance.manage_expenses`)
- Reserve: `GET /finance/reserve` (balance + ledger, incl. reversals) · `POST /finance/reserve/entries` (deposit/interest/resale/income/adjustment) · `POST /finance/reserve/entries/{id}/reverse` · `POST /finance/reserve/reconcile`. (`finance.manage_reserve`)
- Analytics: `GET /finance/analytics/{collection|arrears|expenses|income|trends}?period=`. (`finance.read`)

## 7. Inter-module contracts
- **Consumes:** House & Occupancy (`status` + `first_left_empty_on` → who owes, from when); Onboarding **house registry** (resolve by number for "enter house number"); foundation `TenantContext`/`AuditService`; worker.
- **Provides:** `outstanding_dues(house_id)`, `record_payment(...)`, `generate_due_cycle(society)`; **due/overdue signals** for the modular reminder system (Notifications); a **has-dues** check for Onboarding's delete-guard.
  - **Dues reminders (Finance seam built; reminder pending Notifications):** Finance exposes `outstanding_dues(house_id)` + `maintenance_due_day` today. The recurring reminder itself is a **rule hosted by the Notifications module** (not yet built), which will consume those and build one consolidated `maintenance_due` alert per fire. The **reminder cadence (advance days, interval N) lives in Notifications config, not Finance** — Finance only exposes the dues data. See [notifications.md](notifications.md).
- **PaymentProvider interface:** `admin_manual` now; a gateway is a later implementation — no finance-core change.

## 8. Feature flag / config
- Module key `finance`, `depends_on: houses`. `society_modules.config`: `maintenance_due_day` (1–28), `prepaid_blocks` = [3,6,9,12]. (Currency lives on `societies`.)

## 9. Background jobs
- **Monthly dues generation** — per society, on its `maintenance_due_day`: materialize the new period's dues for dues-owing houses (idempotent, backfills). Callable on demand too (not solely scheduler-dependent).
- (Overdue is computed from `due_date`; reminders are the Notifications module's job.)

## 10. Open questions / future
Late fees/penalties, discounts/waivers, online payment gateway, per-house-type rates, receipts/invoices, resident self-service payment. Unpaid dues at owner replacement: **dues stay on the house** (data tied to house) — new owner sees them; who settles is an operational choice.

## 11. Resolved decisions
Single society-wide effective-dated rate · materialized dues on society due-day · computed reserve ledger + reconcile · prepaid 3/6/9/12 locked, arrears-first, house-tied · no partial-within-month, oldest-first + pay-all · extendable expense categories · void/reversal corrections **visible in reports** (transparency) · full analytics · no late fees/waivers now · PaymentProvider interface for future gateway · reminders via modular Notifications.
