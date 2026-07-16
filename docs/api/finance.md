# Finance API Reference

Endpoint-level reference for the Finance module: the maintenance rate, monthly dues,
payment/prepaid collection, expenses, the reserve fund ledger, and analytics.

**Scope note:** there are no super-admin endpoints in this module. Every endpoint requires the
`finance` module to be enabled (which itself requires `houses` to be enabled first) plus one of
five permission keys — see the permission table below. This is the one module so far where a
resident holds a real permission key (`finance.read`) rather than none at all, so read the
"Who can call what" section carefully before integrating a resident-facing screen.

Base path: **`/finance`**.

---

## How finance works

- **One flat, effective-dated rate for the whole society** — not per-sqft or per-person.
  Setting a new rate never edits history; it inserts a new row effective from a given month.
  The rate used for any given month's dues is whichever rate's `valid_from` is the latest one
  on or before that month. **Changing the rate never touches dues that were already
  generated** — each due's amount is fixed at the moment it's materialized.
- **Dues are "materialized"** — a real `house_dues` row is created (with a fixed amount) for
  every dues-owing house (any house not in `empty` status), once per calendar month, rather
  than being computed on the fly. This happens automatically via a nightly worker job on each
  society's configured due-day, and can also be triggered on demand (see
  [`POST /finance/dues/generate`](#post-financeduesgenerate)). It's idempotent — running it
  twice in the same month never double-creates dues. If a house has been owing for several
  months without dues ever being generated (e.g. the job was off), the next run backfills every
  missing month up to the current one. If no rate had been set yet for a given month, that
  month is silently skipped (not billed) rather than erroring.
- **Due status:** only two real states — `outstanding` and `paid`. There's no stored "overdue"
  or "waived" status. `is_overdue` is computed at read time (`outstanding` and past its
  `due_date`), not persisted.
- **Due source:** `accrued` (normal monthly generation) or `prepaid` (settled in advance via a
  prepaid block purchase, which also locks in the rate at time of purchase — see below).
- **Payments settle oldest-first**, whole months only — you either settle the N oldest
  outstanding months or all of them; there's no partial-month payment. A single payment can
  span (and is linked to) multiple `house_dues` rows.
- **Prepaid blocks** let an owner pay several months in advance in one go (e.g. 3/6/9/12 months
  — configurable per society). This requires all current arrears to be cleared first, and locks
  in the rate at purchase time for those months, so a later rate increase never retroactively
  charges more for months already prepaid.
- **Voiding is non-destructive.** Voiding a payment reopens whatever dues it had settled (back
  to `outstanding`); if any of those dues had been prepaid, they're reset to `source: "accrued"`
  with their locked rate cleared — meaning if the owner pays for those months again, they pay at
  the *current* rate, not the old locked-in one. Voiding always posts a reversing ledger entry
  dated to match the original transaction (not "today"), so per-period analytics stay accurate.
  Nothing is ever deleted from the ledger; both the original and its reversal remain visible,
  with the original flagged as reversed.
- **The reserve balance is computed, not stored** — it's simply the sum of every inflow minus
  every outflow across the ledger's entire history. Routine activity (`collection` from
  payments, `expense` from recorded expenses) posts automatically; a few entry types
  (`opening`, `deposit`, `interest`, `resale_transfer`, `income`, `adjustment`) can be posted
  manually for things the system doesn't generate on its own (bank interest credited, a
  resale-transfer fee, correcting the ledger to match an actual bank statement). You cannot
  manually reverse a `collection` or `expense` entry directly — those can only be corrected by
  voiding the payment or expense that created them, which keeps the money trail attached to its
  real-world cause.

## Who can call what

| Permission key | Grants | Default holder |
|---|---|---|
| `finance.read` | View the rate/rate preview, expense categories & list, the reserve ledger, and **all analytics endpoints** — plus (with its own extra scope check) a resident's **own house's** dues. | `resident` **and** `society_admin` |
| `finance.read_all` | View **any** house's dues, not just your own. | `society_admin` |
| `finance.manage_rate` | Set the rate; trigger dues generation. | `society_admin` |
| `finance.record_payment` | Record payments/prepaid; void payments. | `society_admin` |
| `finance.manage_expenses` | Record/void expenses; add expense categories. | `society_admin` |
| `finance.manage_reserve` | Post/reverse manual reserve entries; reconcile. | `society_admin` |

**Important:** `finance.read` is a broad grant. Because residents hold it by default, a
resident can currently call not just "my own dues" but also the rate, rate-preview, expense
categories/list, the full reserve ledger, and every `/analytics/*` endpoint — all of which are
**society-wide**, not scoped to their own house. The only endpoint under `finance.read` that
has its own extra per-house scope check is `GET /houses/{house_id}/dues` (see below) — residents
are restricted there to their own house, but nowhere else in this module. If your frontend is
building an admin-only finance dashboard, don't rely on the backend permission model alone to
hide it from residents — the backend currently allows it. This is a known characteristic of the
current permission model, not a bug in your integration.

## Common error envelope

Same shape as every other module — see the
[auth API reference](auth.md#common-error-envelope) for the full explanation.

| HTTP status | `code` | Meaning |
|-------------|--------|---------|
| 422 | `validation_error` | Bad input or a business rule was violated. |
| 403 | `permission_denied` | Missing the required permission, module not enabled, or (dues endpoint only) not your own house. |
| 404 | `not_found` | House / payment / expense / expense category / ledger entry doesn't exist in this society. |
| 409 | `conflict` | Action conflicts with current state (duplicate rate month, arrears not cleared, already voided/reversed, duplicate category name). |

All auth-related 401/403s (missing bearer token, expired token, forced password change) are
identical to every other protected endpoint — see the
[auth reference](auth.md#errors-5) rather than repeated per-endpoint here.

**Module/permission errors** (apply to every endpoint below, permission key varies by endpoint):

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `module_disabled` | `"No active society."` | `{"module_key": "finance"}` | Caller's token has no active society. |
| 403 | `module_disabled` | `"Module 'finance' is not enabled for this society."` | `{"module_key": "finance"}` | Finance module isn't enabled. |
| 403 | `permission_denied` | `"You do not have permission to perform this action."` | `{"required_permission": "finance.read"}` (or whichever key the endpoint needs) | Caller's role(s) lack the needed permission. |

---

## Rate

### `GET /finance/rate`

Returns the current rate and its full history.

**Permission:** `finance.read`.

#### Request

No parameters.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `current` | `RateOut` \| null | The latest rate, or `null` if none has ever been set. |
| `history` | array of `RateOut` | All rates ever set, newest `valid_from` first. |

`RateOut`: `id` (integer), `amount` (decimal), `valid_from` (date, always the 1st of a month),
`created_at` (datetime).

```json
{
  "current": {"id": 3, "amount": "2500.00", "valid_from": "2026-04-01", "created_at": "2026-03-20T10:00:00Z"},
  "history": [
    {"id": 3, "amount": "2500.00", "valid_from": "2026-04-01", "created_at": "2026-03-20T10:00:00Z"},
    {"id": 1, "amount": "2000.00", "valid_from": "2025-01-01", "created_at": "2024-12-15T09:00:00Z"}
  ]
}
```

#### Errors

None beyond the shared module/permission errors above.

---

### `POST /finance/rate`

Sets a new effective-dated rate. Always an insert — never edits or replaces a past rate.

**Permission:** `finance.manage_rate`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `amount` | decimal | Yes | Must be > 0, ≤ 2 decimal places, and ≤ `9999999999.99`. |
| `valid_from` | date | Yes | **Must be the 1st of a month.** |

```json
{
  "amount": "2500.00",
  "valid_from": "2026-08-01"
}
```

#### Response — `200 OK`

`RateOut` (see above):

```json
{
  "id": 4,
  "amount": "2500.00",
  "valid_from": "2026-08-01",
  "created_at": "2026-07-10T12:00:00Z"
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"amount must be positive."` | | `amount` ≤ 0. |
| 422 | `validation_error` | `"amount exceeds the maximum allowed value."` | | `amount` > `9999999999.99`. |
| 422 | `validation_error` | `"amount must have at most 2 decimal places."` | | `amount` has sub-cent precision. |
| 422 | `validation_error` | `"valid_from must be the first day of a month."` | | `valid_from.day != 1`. |
| 409 | `conflict` | `"A rate already exists for this effective month."` | `{"valid_from": "2026-08-01"}` | A rate for that exact `valid_from` already exists. |

---

### `GET /finance/rate/preview`

Projects the effect of a proposed rate change, without persisting anything.

**Permission:** `finance.read`.

#### Request

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `amount` | decimal (query) | Yes | Must be > 0. |

```
GET /finance/rate/preview?amount=2750.00
```

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `proposed_amount` | decimal | Echoed input. |
| `dues_owing_houses` | integer | Count of houses not in `empty` status. |
| `projected_monthly_collection` | decimal | `proposed_amount × dues_owing_houses`. |
| `current_amount` | decimal \| null | Current rate, or `null` if none set. |
| `current_monthly_collection` | decimal \| null | `current_amount × dues_owing_houses`, or `null`. |
| `delta` | decimal \| null | `projected − current`, or `null` if there's no current rate to compare against. |

```json
{
  "proposed_amount": "2750.00",
  "dues_owing_houses": 40,
  "projected_monthly_collection": "110000.00",
  "current_amount": "2500.00",
  "current_monthly_collection": "100000.00",
  "delta": "10000.00"
}
```

#### Errors

None beyond query-param validation (non-positive/non-numeric `amount` → standard `422
validation_error`).

---

## Collection (dues, payments, prepaid)

### `GET /finance/houses/{house_id}/dues`

Returns a house's outstanding dues and full dues history.

**Permission:** `finance.read` — **plus an extra scope check**: super-admins and holders of
`finance.read_all` (i.e. `society_admin`) can view any house; a caller who only holds
`finance.read` (i.e. a plain resident) can view **only a house they currently occupy**.

#### Request

Path param `house_id` only.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `house_id` | integer | |
| `outstanding` | array of `HouseDueOut` | Status `"outstanding"` only, oldest-first. |
| `outstanding_total` | decimal | Sum of `outstanding`'s `amount_due`. |
| `history` | array of `HouseDueOut` | Every due (outstanding + paid), oldest-first. |

`HouseDueOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `house_id` | integer | |
| `period_year` | integer | |
| `period_month` | integer | 1–12. |
| `amount_due` | decimal | Fixed at the time this due was generated. |
| `due_date` | date | |
| `status` | string | `"outstanding"` or `"paid"`. |
| `source` | string | `"accrued"` or `"prepaid"`. |
| `locked_rate` | decimal \| null | Set only for `source: "prepaid"` dues — the rate locked in at purchase. |
| `paid_at` | datetime \| null | |
| `is_overdue` | boolean | Computed: `true` if `status == "outstanding"` and `due_date` is in the past. |

```json
{
  "house_id": 103,
  "outstanding": [
    {"id": 501, "house_id": 103, "period_year": 2026, "period_month": 6, "amount_due": "2500.00", "due_date": "2026-06-01", "status": "outstanding", "source": "accrued", "locked_rate": null, "paid_at": null, "is_overdue": true},
    {"id": 502, "house_id": 103, "period_year": 2026, "period_month": 7, "amount_due": "2500.00", "due_date": "2026-07-01", "status": "outstanding", "source": "accrued", "locked_rate": null, "paid_at": null, "is_overdue": false}
  ],
  "outstanding_total": "5000.00",
  "history": [
    {"id": 490, "house_id": 103, "period_year": 2026, "period_month": 5, "amount_due": "2500.00", "due_date": "2026-05-01", "status": "paid", "source": "accrued", "locked_rate": null, "paid_at": "2026-05-03T10:00:00Z", "is_overdue": false},
    {"id": 501, "house_id": 103, "period_year": 2026, "period_month": 6, "amount_due": "2500.00", "due_date": "2026-06-01", "status": "outstanding", "source": "accrued", "locked_rate": null, "paid_at": null, "is_overdue": true},
    {"id": 502, "house_id": 103, "period_year": 2026, "period_month": 7, "amount_due": "2500.00", "due_date": "2026-07-01", "status": "outstanding", "source": "accrued", "locked_rate": null, "paid_at": null, "is_overdue": false}
  ]
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `permission_denied` | `"You may only view dues for your own house."` | | Caller only holds `finance.read` and isn't a current occupant of this house. |
| 404 | `not_found` | `"House not found in this society."` | `{"house_id": ...}` | No such house (checked after the scope check passes). |

---

### `POST /finance/houses/{house_id}/payments`

Records a payment, settling the oldest outstanding months (or all of them).

**Permission:** `finance.record_payment`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `method` | string | Yes | `"cash"` \| `"cheque"` \| `"bank_transfer"` \| `"online"` \| `"other"`. |
| `reference` | string \| null | No | Free-text reference (cheque number, transaction id, etc). |
| `paid_at` | date \| null | No | Defaults to today if omitted. |
| `months` | integer ≥ 1 \| null | Exactly one of `months`/`pay_all` | Settle this many of the oldest outstanding months. |
| `pay_all` | boolean | Exactly one of `months`/`pay_all` | Settle every outstanding month. Defaults `false`. |

```json
{
  "method": "bank_transfer",
  "reference": "UTR123456789",
  "months": 2
}
```

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `house_id` | integer | |
| `amount` | decimal | Sum of the settled dues' `amount_due`. |
| `method` | string | |
| `reference` | string \| null | |
| `provider` | string | Always `"admin_manual"` for this endpoint. |
| `status` | string | `"recorded"` (or `"voided"` after voiding). |
| `paid_at` | datetime | |
| `voided_at` | datetime \| null | |
| `void_reason` | string \| null | |
| `allocations` | array of `PaymentAllocationOut` | One entry per due settled. |

`PaymentAllocationOut`: `id`, `house_due_id`, `amount_applied` (decimal), `period_year`,
`period_month`.

```json
{
  "id": 88,
  "house_id": 103,
  "amount": "5000.00",
  "method": "bank_transfer",
  "reference": "UTR123456789",
  "provider": "admin_manual",
  "status": "recorded",
  "paid_at": "2026-07-10T00:00:00Z",
  "voided_at": null,
  "void_reason": null,
  "allocations": [
    {"id": 201, "house_due_id": 501, "amount_applied": "2500.00", "period_year": 2026, "period_month": 6},
    {"id": 202, "house_due_id": 502, "amount_applied": "2500.00", "period_year": 2026, "period_month": 7}
  ]
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"House not found in this society."` | `{"house_id": ...}` | No such house. |
| 422 | `validation_error` | `"Provide exactly one of 'months' or 'pay_all'."` | | Both or neither given. |
| 422 | `validation_error` | `"This house has no outstanding dues."` | | Nothing to settle. |
| 422 | `validation_error` | `"Requested months exceed the outstanding count."` | `{"requested": 5, "outstanding": 2}` | `months` is larger than what's actually outstanding. |

---

### `POST /finance/houses/{house_id}/prepaid`

Buys a prepaid block of future months in one go, locking in the current rate.

**Permission:** `finance.record_payment`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `months_count` | integer | Yes | Must be one of the society's configured prepaid blocks (default `[3, 6, 9, 12]`). |
| `method` | string | Yes | Same allowed values as the payment endpoint. |
| `reference` | string \| null | No | |
| `paid_at` | date \| null | No | Defaults to today. |

```json
{
  "months_count": 6,
  "method": "online",
  "reference": "razorpay_abc123"
}
```

#### Response — `200 OK`

`PaymentOut` — same shape as `POST .../payments`; `allocations` here cover the newly
materialized prepaid months (all `source: "prepaid"`, `locked_rate` set to the rate at
purchase).

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"House not found in this society."` | `{"house_id": ...}` | No such house. |
| 422 | `validation_error` | `"months_count must be an allowed prepaid block."` | `{"allowed": [3, 6, 9, 12]}` | `months_count` isn't one of the configured blocks. |
| 409 | `conflict` | `"Clear arrears first."` | | This house still has outstanding (unpaid) dues before the requested prepaid window. |
| 422 | `validation_error` | `"No maintenance rate is set; cannot lock a prepaid rate."` | | No rate has ever been set for this society. |

---

### `POST /finance/payments/{payment_id}/void`

Voids a previously recorded payment (regular or prepaid), reopening whatever dues it had
settled.

**Permission:** `finance.record_payment`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `reason` | string, 1–1000 chars | Yes | |

```json
{
  "reason": "Cheque bounced"
}
```

#### Response — `200 OK`

`PaymentOut`, now with `status: "voided"`, `voided_at`/`void_reason` set, and `allocations`
showing what was reopened. If this had been a prepaid payment, the reopened dues are reset to
`source: "accrued"` with `locked_rate` cleared — they'll be charged at whatever the current rate
is if paid again.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Payment not found."` | | No such payment in this society. |
| 409 | `conflict` | `"Payment is already voided."` | | Already voided. |

---

## Expenses

### `GET /finance/expense-categories`

Lists expense categories. The 7 default system categories (`Electricity`, `Water`,
`Housekeeping`, `Security`, `Repairs`, `Salaries`, `Misc`) are created automatically the first
time this or the expenses endpoints are touched for a society — nothing needs to be
provisioned manually.

**Permission:** `finance.read`.

#### Request

No parameters.

#### Response — `200 OK`

Array of `ExpenseCategoryOut`, ordered by name:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `name` | string | |
| `is_system` | boolean | `true` for the 7 defaults; `false` for society-added categories. |

```json
[
  {"id": 1, "name": "Electricity", "is_system": true},
  {"id": 7, "name": "Misc", "is_system": true},
  {"id": 9, "name": "Landscaping", "is_system": false}
]
```

#### Errors

None beyond the shared module/permission errors above.

---

### `POST /finance/expense-categories`

Adds a custom expense category.

**Permission:** `finance.manage_expenses`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string, 1–64 chars | Yes | Case-sensitive uniqueness within the society. |

```json
{
  "name": "Landscaping"
}
```

#### Response — `200 OK`

`ExpenseCategoryOut`:

```json
{
  "id": 9,
  "name": "Landscaping",
  "is_system": false
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 409 | `conflict` | `"An expense category named 'Landscaping' already exists."` | | Name collides with an existing category (system or custom). |

---

### `GET /finance/expenses`

Lists recorded expenses, paginated, newest first.

**Permission:** `finance.read`.

#### Request

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | integer ≥ 1 | `1` | |
| `page_size` | integer, 1–100 | `20` | |
| `include_voided` | boolean | `true` | Set `false` to exclude voided expenses. |

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `items` | array of `ExpenseOut` | |
| `total` | integer | Total matching expenses across all pages. |

`ExpenseOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `category_id` | integer | |
| `amount` | decimal | |
| `description` | string \| null | |
| `incurred_on` | date | |
| `status` | string | `"recorded"` or `"voided"`. |
| `voided_at` | datetime \| null | |
| `void_reason` | string \| null | |

```json
{
  "items": [
    {"id": 44, "category_id": 1, "amount": "8500.00", "description": "June electricity bill", "incurred_on": "2026-06-28", "status": "recorded", "voided_at": null, "void_reason": null}
  ],
  "total": 12
}
```

#### Errors

None beyond the shared module/permission errors above.

---

### `POST /finance/expenses`

Records an expense against a category. Posts a matching outflow ledger entry automatically.

**Permission:** `finance.manage_expenses`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `category_id` | integer | Yes | Must belong to this society. |
| `amount` | decimal | Yes | Same money rules as the rate endpoint (> 0, ≤2dp, ≤ max). |
| `description` | string, ≤2000 chars \| null | No | |
| `incurred_on` | date | Yes | |

```json
{
  "category_id": 1,
  "amount": "8500.00",
  "description": "June electricity bill",
  "incurred_on": "2026-06-28"
}
```

#### Response — `200 OK`

`ExpenseOut` (see above).

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Expense category {id} was not found."` | | `category_id` doesn't exist in this society. |
| 422 | `validation_error` | Same money-validation messages as the rate endpoint | | Bad `amount`. |

---

### `POST /finance/expenses/{expense_id}/void`

Voids a recorded expense, posting a reversing inflow entry dated to match the original.

**Permission:** `finance.manage_expenses`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `reason` | string, 1–1000 chars | Yes | |

```json
{
  "reason": "Duplicate entry"
}
```

#### Response — `200 OK`

`ExpenseOut`, now `status: "voided"` with `voided_at`/`void_reason` set.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Expense {id} was not found."` | | No such expense. |
| 409 | `conflict` | `"This expense is already voided."` | | Already voided. |

---

## Reserve

### `GET /finance/reserve`

Returns the computed reserve balance and the ledger, paginated newest-first.

**Permission:** `finance.read`.

#### Request

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | integer ≥ 1 | `1` | |
| `page_size` | integer, 1–100 | `20` | |

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `balance` | decimal | Sum of all inflows minus all outflows, across the entire ledger (not just this page). |
| `entries` | array of `LedgerEntryOut` | This page, newest first. |
| `total` | integer | Total ledger entries across all pages. |

`LedgerEntryOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `entry_type` | string | `opening` \| `deposit` \| `interest` \| `resale_transfer` \| `income` \| `collection` \| `expense` \| `adjustment` \| `reversal`. |
| `direction` | string | `"inflow"` or `"outflow"`. |
| `amount` | decimal | |
| `description` | string \| null | |
| `occurred_on` | date | |
| `source_type` | string \| null | `"payment"` \| `"expense"` \| `"prepaid"` \| `"house"` \| `null`. |
| `source_id` | integer \| null | |
| `reverses_entry_id` | integer \| null | Set on a `reversal` entry, pointing at the entry it reverses. |
| `is_reversed` | boolean | `true` once this entry has been reversed. |
| `created_at` | datetime | |

```json
{
  "balance": "245000.00",
  "entries": [
    {"id": 300, "entry_type": "collection", "direction": "inflow", "amount": "5000.00", "description": null, "occurred_on": "2026-07-10", "source_type": "payment", "source_id": 88, "reverses_entry_id": null, "is_reversed": false, "created_at": "2026-07-10T00:00:01Z"}
  ],
  "total": 156
}
```

#### Errors

None beyond the shared module/permission errors above.

---

### `POST /finance/reserve/entries`

Posts a manual reserve ledger entry (for money movements the system doesn't generate on its
own — bank interest, a resale-transfer fee, a one-off inflow/outflow).

**Permission:** `finance.manage_reserve`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `entry_type` | string | Yes | One of `"opening"`, `"deposit"`, `"interest"`, `"resale_transfer"`, `"income"`, `"adjustment"`. (`collection`/`expense`/`reversal` are system-internal and rejected here.) |
| `amount` | decimal | Yes | Same money rules as elsewhere (> 0, ≤2dp). |
| `occurred_on` | date | Yes | |
| `description` | string, ≤2000 chars \| null | No | |
| `direction` | string \| null | **Required only for `entry_type: "adjustment"`** | `"inflow"` or `"outflow"`. Every other `entry_type` has a fixed direction (all inflows except `expense`, which isn't allowed here anyway). |
| `source_type` | string \| null | No | `"payment"` \| `"expense"` \| `"prepaid"` \| `"house"`. |
| `source_id` | integer \| null | **Required if `source_type: "house"`** | Must reference a house in this society. |

```json
{
  "entry_type": "interest",
  "amount": "1200.00",
  "occurred_on": "2026-07-01",
  "description": "Quarterly FD interest credited"
}
```

#### Response — `200 OK`

`LedgerEntryOut` (see above).

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"entry_type must be one of [...]"` | | Bad or internal-only `entry_type`. |
| 422 | `validation_error` | `"direction must be one of [...]"` | | Bad `direction` value. |
| 422 | `validation_error` | `"direction is required for an adjustment entry (inflow or outflow)."` | | `entry_type: "adjustment"` with no `direction`. |
| 422 | `validation_error` | `"source_id is required when source_type=house."` | | `source_type: "house"` with no `source_id`. |
| 404 | `not_found` | `"Linked house not found in this society."` | `{"house_id": ...}` | `source_type: "house"` with a `source_id` that doesn't exist. |
| 422 | `validation_error` | Money-validation messages | | Bad `amount`. |

---

### `POST /finance/reserve/entries/{entry_id}/reverse`

Reverses a manually-posted ledger entry.

**Permission:** `finance.manage_reserve`.

#### Request

Path param `entry_id` only, no body.

#### Response — `200 OK`

`LedgerEntryOut` — **the new reversal entry**, not the original (fetch the original separately
via `GET /finance/reserve` if you need both).

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Ledger entry not found."` | | No such entry. |
| 409 | `conflict` | `"This entry has already been reversed."` | | Already reversed. |
| 422 | `validation_error` | `"A reversal entry cannot itself be reversed."` | | Target is itself a `reversal` entry. |
| 422 | `validation_error` | `"A system-posted 'collection' entry is corrected by voiding the underlying payment/expense, not via reserve reversal."` | | Target is a `collection` or `expense` entry — void the originating payment/expense instead. |

---

### `POST /finance/reserve/reconcile`

Reconciles the computed reserve balance against an actual bank balance, posting a single
`adjustment` entry for the difference.

**Permission:** `finance.manage_reserve`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `actual_balance` | decimal | Yes | The real bank balance, ≤2dp, bounded to the same max as other money fields. |
| `occurred_on` | date | Yes | |
| `description` | string, ≤2000 chars \| null | No | |

```json
{
  "actual_balance": "246500.00",
  "occurred_on": "2026-07-10",
  "description": "Monthly bank reconciliation"
}
```

#### Response — `200 OK`

`LedgerEntryOut` — the posted `adjustment` entry. Direction is `inflow` if
`actual_balance > computed_balance`, else `outflow`; `amount` is the absolute difference.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Reserve already reconciled; no difference to adjust."` | | `actual_balance` exactly equals the computed balance — there's nothing to post. |
| 422 | `validation_error` | Money-validation messages | | Bad `actual_balance`. |

---

## Analytics

All five endpoints below are read-only aggregates, computed at request time — nothing is
persisted or cached. They're gated by `finance.read` only (see the "Who can call what" note
above — this means residents can call them too).

### `GET /finance/analytics/collection`

Expected vs. collected vs. outstanding, society-wide and per-house.

**Permission:** `finance.read`.

#### Request

| Param | Type | Notes |
|-------|------|-------|
| `year` | integer, 2000–9999 (query) | Optional filter. |
| `month` | integer, 1–12 (query) | Optional filter. |

Both independent and optional — supply either, both, or neither.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `period_year` | integer \| null | Echoed filter. |
| `period_month` | integer \| null | Echoed filter. |
| `expected` | decimal | Sum of `amount_due` matching the filter. |
| `collected` | decimal | Sum of `amount_due` where `status: "paid"`. |
| `outstanding` | decimal | `expected − collected`. |
| `per_house` | array of `{house_id, expected, collected, outstanding}` | Same breakdown per house. |

```json
{
  "period_year": 2026,
  "period_month": 7,
  "expected": "100000.00",
  "collected": "82500.00",
  "outstanding": "17500.00",
  "per_house": [
    {"house_id": 103, "expected": "2500.00", "collected": "2500.00", "outstanding": "0.00"}
  ]
}
```

#### Errors

None beyond query-param validation.

---

### `GET /finance/analytics/arrears`

Houses currently in arrears, with totals and how far back they go.

**Permission:** `finance.read`.

#### Request

No parameters.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `total_outstanding` | decimal | Sum across all houses in arrears. |
| `houses` | array | Only houses with ≥1 outstanding due. |

Each `houses` entry: `house_id`, `outstanding_total` (decimal), `oldest_period_year`,
`oldest_period_month`, `months_outstanding` (integer count).

```json
{
  "total_outstanding": "17500.00",
  "houses": [
    {"house_id": 103, "outstanding_total": "5000.00", "oldest_period_year": 2026, "oldest_period_month": 6, "months_outstanding": 2}
  ]
}
```

#### Errors

None.

---

### `GET /finance/analytics/expenses`

Total expenses and a breakdown by category. **Voided expenses are excluded.**

**Permission:** `finance.read`.

#### Request

Same optional `year`/`month` query filters as `/analytics/collection`.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `period_year` | integer \| null | |
| `period_month` | integer \| null | |
| `total_expense` | decimal | Recorded (non-voided) expenses matching the filter. |
| `by_category` | array of `{category_id, category_name, total}` | |

```json
{
  "period_year": 2026,
  "period_month": 6,
  "total_expense": "8500.00",
  "by_category": [
    {"category_id": 1, "category_name": "Electricity", "total": "8500.00"}
  ]
}
```

#### Errors

None beyond query-param validation.

---

### `GET /finance/analytics/income`

Total income, collection, and expense for the period, netted together.

**Permission:** `finance.read`.

#### Request

Same optional `year`/`month` query filters.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `period_year` | integer \| null | |
| `period_month` | integer \| null | |
| `total_income` | decimal | Manual `income`-type ledger entries, net of reversals. |
| `total_collection` | decimal | From payments, net of reversals (voided payments excluded). |
| `total_expense` | decimal | Net of reversals (voided expenses excluded). |
| `net` | decimal | `total_income + total_collection − total_expense`. |

```json
{
  "period_year": 2026,
  "period_month": 6,
  "total_income": "0.00",
  "total_collection": "82500.00",
  "total_expense": "8500.00",
  "net": "74000.00"
}
```

#### Errors

None beyond query-param validation.

---

### `GET /finance/analytics/trends`

Month-over-month collected/expense/net, for charting.

**Permission:** `finance.read`.

#### Request

No parameters.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `points` | array of `{period_year, period_month, collected, expense, net}` | One point per month with any ledger activity, oldest to newest, net of reversals. |

```json
{
  "points": [
    {"period_year": 2026, "period_month": 5, "collected": "80000.00", "expense": "6000.00", "net": "74000.00"},
    {"period_year": 2026, "period_month": 6, "collected": "82500.00", "expense": "8500.00", "net": "74000.00"}
  ]
}
```

#### Errors

None.

---

## Worker trigger

### `POST /finance/dues/generate`

Manually triggers dues generation for the society — the same logic the nightly worker runs
automatically at 02:00 UTC on each society's configured due-day. Useful to force generation
without waiting for the scheduled run (e.g. right after setting a new rate, or during testing).
Idempotent — periods that already have dues are skipped, so calling it repeatedly is harmless.

**Permission:** `finance.manage_rate`.

#### Request

No parameters.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `created` | integer | Number of new `house_dues` rows created by this run. `0` if there was nothing to generate. |

```json
{
  "created": 3
}
```

#### Errors

None beyond the shared module/permission errors above — this endpoint is idempotent and always
succeeds.
