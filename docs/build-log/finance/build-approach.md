# Finance (Module 4) — Build Approach

How Module 4 was built. Design source of truth: `docs/modules/finance.md`.
As-built index: `docs/implemented/finance.md`. QA records: `code-review-findings.md`,
`test-gate.md` (this folder).

## Process (lead-core-then-waves, all in Docker)
Same proven process as Modules 0–3, on one integration branch `feat/finance`
(feature branch → PR → `main`; never commit to `main` directly).

**Phase 0 — Lead builds the frozen core** (green gate before any fan-out):
- `app/modules/finance/` package: `models.py` (8 tables), `schemas.py` (frozen
  contracts + domains + `quantize_money`), `periods.py` (stdlib month math),
  `repository.py` (SQL-only, society-scoped, FOR UPDATE on the allocation path,
  DB-side aggregates), `service.py` facade + `services/{support,rates,dues,
  collection,expenses,reserve,analytics,jobs}.py` (reads implemented, writes
  stubbed per wave), `api.py` (cross-module contract), `router.py` (19 thin
  dual-gated routes), `spec.py` (`FINANCE_SPEC`, `depends_on: houses`, 5 perms,
  config, admin=all/resident=read).
- Migration `0005_finance.py` chained off `0004_vault`; `alembic/env.py` +
  `app/main.py` + worker entrypoint wiring.
- House & Occupancy provider added (`houses_owing`, `house_by_number`,
  `house_exists`) consumed via the service interface, never table reads.
- Split the service into a `services/` subpackage so the parallel waves owned
  disjoint files with no `service.py` collision (same tactic as Vault).
- Gate: migration applies, `alembic check` no drift, module registers with correct
  perms/deps, all 19 routes live + auth-gated, `import app.main` clean, existing
  521 tests green.

**Phase 1 — Waves** (seven Opus 4.8 agents, disjoint files → parallel):
- **A — Rates:** effective-dated set, resolution, preview.
- **B — Dues generation:** idempotent monthly materialization, backfill, no-rate skip.
- **C — Collection & prepaid:** oldest-first whole-month allocation (FOR UPDATE),
  prepaid blocks (arrears-first, locked, house-tied), payment void.
- **D — Expenses & income:** categories (lazy default seed) + record/void.
- **E — Reserve ledger:** computed balance, manual entries, reverse, reconcile.
- **F — Analytics:** collection/arrears/expenses/income/trends, reversal-netting.
- **G — Worker:** daily dues scan, per-society commit + failure isolation.
Each agent read the frozen-core contract, edited only its own file, wrote its own
tests, and verified `import app.main` + targeted pytest before reporting.

Two foundation/harness fixes surfaced by finance's field-validators + enlarged
truncate set were made during integration (see `docs/implemented/finance.md`
deviations 5 + the conftest note): the `jsonable_encoder` validation-handler fix
and the `_reset_db` engine-dispose deadlock fix. Full suite green (614) after
integration.

**Phase 2 — Code-review gate** (Opus 4.8 medium, read-only) → findings applied:
see `code-review-findings.md`. Confirmed tenant isolation / money correctness /
oldest-first + no-partial / void-reversal transparency / reserve balance / audit /
gating all correct; fixed 2 must-fix (prepaid future-window, prepaid
materialize-then-arrears) + several should-fix/nits (payment-void reversal period,
prepaid-void block unwind, expenses pagination envelope, house-link tenant check,
money-rounding consolidation, reconcile audit shape, dead-code prune). Tests
realigned to the corrected behavior with stronger assertions added.

**Phase 3 — Test gate** (Opus 4.8 designed the matrix → Sonnet 5 implemented + ran
to green): see `test-gate.md`. Adds cross-module e2e (Foundation→Onboarding→House&
Occupancy→Vault→Finance), config/permission-seeding, security/vulnerability, and
deep edge-case coverage beyond the per-feature wave tests.

## Sub-agent model assignment (user decision)
- Codebase exploration → **Sonnet 5**.
- Core + 7 wave implementation → **Opus 4.8** (low effort).
- Code-review gate → **Opus 4.8** (medium).
- Test-matrix design → **Opus 4.8**; test implementation + running → **Sonnet 5**.
Every subagent had its model set explicitly (never inherited).

## Libraries
No new third-party libraries. Money = stdlib `Decimal` + SQLAlchemy `NUMERIC(12,2)`;
month arithmetic = stdlib `calendar`/`datetime`; worker = the already-vendored
APScheduler. A pre-use safety search confirmed the deliberate choices: `py-moneyed`
was unnecessary (stdlib is strictly less attack surface) and `python-dateutil` was
avoided (not needed, and its `python-dateutils` typosquat was actively
distributing a cryptominer). No `requirements.txt` change.
