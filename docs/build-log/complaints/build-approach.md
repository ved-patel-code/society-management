# Complaints (Module 5) — Build Approach

How Module 5 was built. Design source of truth: `docs/modules/complaints.md`.
As-built index: `docs/implemented/complaints.md`. QA records: `code-review-findings.md`,
`test-gate.md` (this folder).

## Process (lead-core-then-waves, all in Docker)
Same proven process as Modules 0–4, on one integration branch `feat/complaints`
(feature branch → PR → `main`; never commit to `main` directly).

**Phase 0 — Lead builds the frozen core** (green gate before any fan-out):
- `app/modules/complaints/` package: `models.py` (5 tables), `schemas.py` (frozen
  contracts + `ALLOWED_TRANSITIONS` + `ComplaintsConfig`), `repository.py` (SQL-only,
  society-scoped, FOR-UPDATE reference allocator, resident-scoped list — visibility
  in the query, not the endpoint), `service.py` facade + `services/{support,
  categories,complaints_crud,status,images,config_svc,jobs}.py` (support implemented;
  the six concern files stubbed per wave with `NotImplementedError`), `router.py`
  (15 thin dual-gated routes), `api.py` (`open_complaint_count`), `events.py`
  (notification call surface), `spec.py` (`COMPLAINTS_SPEC`, `depends_on: houses`,
  6 perms, config, admin=5/resident=create+read).
- New shared infra `app/common/events.py` — a real lightweight in-process
  domain-event dispatcher (docs/05 §3), built one module early: Complaints emits
  `complaint.created/withdrawn/status_changed` + a clear-on-read signal; with no
  subscribers it is a safe no-op, and Notifications subscribes when built (the
  docs said "wired," but Notifications is not built — resolved with the user).
- Migration `0006_complaints` chained off `0005_finance`; `alembic/env.py` +
  `app/main.py` + worker entrypoint (auto-archive @01:30 UTC) wiring.
- House & Occupancy provider added (`current_owned_houses`, `house_display_code`)
  consumed via the service interface, never table reads.
- Split the service into a `services/` subpackage so the parallel waves owned
  disjoint files with no collision (same tactic as Vault/Finance).
- Gate: migration applies + `alembic check` no drift + down/up round-trip, module
  registers with correct perms/deps, all 15 routes live + auth-gated, `import
  app.main` clean, worker job registered, event dispatcher works, existing 689
  tests green.

**Phase 1 — Waves** (six Opus 4.8 agents, disjoint files → parallel):
- **A — Categories:** list (lazy-seed 6 system cats), create, rename/reactivate,
  soft-deactivate.
- **B — Complaints CRUD:** raise (owner-house resolution 422/403 split, active
  category, reference allocation, initial history, `complaint.created`), edit-while-
  open, withdraw, list (resident vs read_all), detail (clear-on-read).
- **C — Status + resolve:** admin transition guard; resolve is multipart (note +
  proof images to Vault, cap before upload, proof locked after).
- **D — Report images:** add (while open, capped) / remove (Vault soft-delete).
- **E — Config:** GET + partial-merge PUT.
- **F — Worker:** daily auto-archive, per-society commit + failure isolation,
  idempotent.
Each agent read the frozen-core contract, edited only its own file + test file, and
verified `import app.main` + its targeted pytest before reporting.

Integration surfaced two real bugs fixed during merge: the router did not `await`
the async `resolve()` handler (left an unawaited coroutine that poisoned the request
session — surfaced as cascading `AdminShutdown` errors on the next test's DB reset),
and a test used a raw-SQL `IN :tuple` psycopg3 rejects (→ `= ANY(:ids)`). Full
complaints suite green (87) + existing suite (689) = 776 after integration.

**Phase 2 — Code-review gate** (Opus 4.8 medium, read-only) → findings applied:
see `code-review-findings.md`. Confirmed isolation / allocator / transitions / worker
/ config / events sound; fixed 2 must-fix (status-detail crash on a trashed proof
image; `date_to` end-day off-by-one) + should-fixes (image-cap check-then-act race →
`FOR UPDATE`; worker window from a real instant; LIKE-metacharacter escaping;
consolidated the two `_detail` helpers into `support.assemble_detail`). 776 green.

**Phase 3 — Test gate** (Opus 4.8 designed the matrix → Sonnet 5 implemented + ran
to green): see `test-gate.md`. 81 cross-module e2e / enable / security-with-&-without
-roles / tenant-isolation / regression / edge / robustness tests. No app bug
surfaced. Full suite **857 passed, 2 skipped**, deterministic.

## Sub-agent model assignment (user decision)
- Codebase exploration → **Sonnet 5**.
- Frozen core + 6 wave implementation → **Opus 4.8** (low effort).
- Code-review gate → **Opus 4.8** (medium).
- Test-matrix design → **Opus 4.8**; test implementation + running → **Sonnet 5**.
Every subagent had its model set explicitly.

## Libraries
No new third-party libraries. The event dispatcher is a ~90-line stdlib module;
month/day math is stdlib `datetime`; the worker reuses the vendored APScheduler; the
reference allocator is a `FOR UPDATE` row lock (stdlib SQLAlchemy). No
`requirements.txt` change, so no supply-chain vet was needed this module.

## Test-DB operational note
The shared xdist test DBs are sensitive to orphaned idle-in-transaction connections
left by killed/overlapping runs (they make the next run's `TRUNCATE` race, surfacing
as spurious `AdminShutdown`/401/`ConflictError`). Run gate files serially while
iterating (`-n0`), avoid overlapping runs, and terminate stale `society_test*`
backends between churn-heavy sessions. This is environment hygiene, not module logic.
