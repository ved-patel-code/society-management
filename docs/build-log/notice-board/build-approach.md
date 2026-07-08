# Notice Board (Module 6) — Build Approach

How Module 6 was built. Design source of truth: `docs/modules/notice-board.md`.
As-built index: `docs/implemented/notice-board.md`. QA records:
`code-review-findings.md`, `test-gate.md` (this folder).

## Process (lead-core-then-waves, all in Docker)
Same proven process as Modules 0–5, on one integration branch `feat/notice-board`
(feature branch → PR → `main`; never commit to `main` directly).

**Phase 0 — Lead builds the frozen core** (green gate before any fan-out):
- `app/modules/notices/` package: `models.py` (3 tables), `schemas.py` (frozen
  contracts + `ALLOWED_TRANSITIONS` + status domain), `repository.py` (SQL-only,
  society-scoped: pinned-first feed with batched attachment-count + caller-read
  set — no N+1, `ON CONFLICT DO NOTHING` read upsert, `FOR UPDATE` notice fetch,
  active/archive filters), `service.py` facade + `services/support.py`
  (implemented — the shared choke-points: `sanitize_body`, `apply_publish`,
  `assemble_detail`, `assert_transition_allowed`, `is_active`/`is_expired`,
  `current_owner_ids`) + `services/{notices_crud,lifecycle,attachments,receipts}.py`
  (frozen wave stubs raising `NotImplementedError`), `router.py` (thin dual-gated
  routes; attachment routes also `require_module('vault')`), `api.py`
  (`active_notice_count`), `events.py` (`notice_posted` + `mark_read_for`),
  `spec.py` (`NOTICES_SPEC`, `depends_on: houses`, 3 perms, resident=read /
  society_admin=all 3).
- New shared Foundation util `app/common/html_sanitizer.py` — `sanitize_html` via
  **`nh3`** (the Rust "ammonia" binding). Chosen over the deprecated/archived
  `bleach` (a quick supply-chain check confirmed bleach's final release 2026-06 with
  no future security patches; nh3 is actively maintained, memory-safe, no known
  CVEs — vetted 2026-07-08). `nh3==0.3.6` pinned in `requirements.txt`.
- Migration `0007_notices` chained off `0006_complaints`; `alembic/env.py` +
  `app/main.py` wiring (register + include_router, zero edits to existing modules).
- **Notifications wired as a skeleton no-op** (the design said "wired," but
  Notifications is Module 7 — resolved with the user; same pattern as Complaints).
- Split the service into a `services/` subpackage so the parallel waves owned
  disjoint files with no collision (same tactic as Vault/Finance/Complaints).
- Gate: migration applies + `alembic check` no drift + down/up round-trip; module
  registers with 3 perms + `depends_on: houses`; all route ops live + dual-gated +
  401 unauth (verified in live OpenAPI); `import app.main` clean; nh3 baked into
  the image; event dispatcher no-op; sanitizer strips XSS; existing 857 tests green.

**Phase 1 — Waves** (four Opus 4.8 agents, disjoint files → parallel):
- **A — Notices CRUD + feed:** create (sanitize; publish-on-create via
  `apply_publish`), edit (content-only `last_edited_at`; `model_fields_set`
  clear-vs-omit expiry; withdrawn→409; empty→422), list_feed (residents=active,
  admins=status/scope + drafts; batched, no N+1; unread badge), get_detail
  (draft/withdrawn→404 for non-managers; idempotent read + `mark_read_for`).
- **B — Lifecycle:** publish (via the shared `apply_publish` — single stamp+emit),
  withdraw (soft-delete; attachments left in Vault; double→409).
- **C — Attachments:** add (`get_notice(lock=True)`; sync `file.file.read` like
  complaints to avoid the session-poisoning bug; Vault folder + store; 413/415
  propagate with no orphan row; no cap), remove (Vault soft-delete BEFORE dropping
  the row so a Vault error rolls back).
- **D — Receipts + archive + read-all:** read_all (idempotent), receipts
  (current-owner denominator, in-memory split, no per-owner loop), archive
  (expired + withdrawn, admin-only).
Each agent read the frozen-core contract, edited only its own service file + test
file, and verified `import app.main` + its targeted pytest before reporting.
Integration surfaced no cross-wave bugs — all 56 per-wave tests passed together on
the first integrated run (867 existing note: full suite 913). Three waves
independently arrived at the same correct audit-datetime detail (ISO strings for
the JSONB before/after), and Wave A correctly routed `utcnow` through `support` for
freeze-compatibility.

**Phase 2 — Code-review gate** (Opus 4.8, read-only) → findings applied: see
`code-review-findings.md`. **No MUST-FIX** — isolation, the sanitizer choke-point,
single-emit publish, receipts, N+1 batching, and the attachment rollback ordering
all verified sound. Applied a real test gap (cross-tenant isolation suite), a
test-reliability fix (`freeze_utcnow` missed two module-local `utcnow` bindings),
and nits (negative sanitizer test, import tidy, ISO event payload). 917 green.

**Phase 3 — Test gate** (Opus 4.8 designed the matrix → Sonnet 5 implemented + ran
to green): see `test-gate.md`. Cross-module e2e / enable / security-with-&-without
-roles / edge / robustness (incl. the stored-XSS battery) coverage the per-wave
suites structurally cannot.

## Sub-agent model assignment (user decision)
- Codebase exploration → Sonnet 5.
- Frozen core (lead) + 4 wave implementations + code-review gate → Opus 4.8.
- Test-matrix design → Opus 4.8; test implementation + running → Sonnet 5.

## Libraries
One new third-party dependency: **`nh3==0.3.6`** (HTML sanitizer). Supply-chain
vetted before adding (bleach is deprecated/archived — not used). No other new deps;
the event dispatcher + Vault + Occupancy interfaces were reused as-is.

## Test-DB operational note
Same as prior modules: the shared xdist test DBs are sensitive to orphaned
idle-in-transaction connections; run gate files serially (`-n0`) while iterating
and avoid overlapping runs. Environment hygiene, not module logic. One environment
hiccup during this build: the dev backend's `uvicorn --reload` watcher hit an
inotify/memory limit (`os error 12`) after long uptime + many new files — a
`docker compose restart backend` cleared it (not app code).
