# House & Occupancy (Module 2) — Build Approach

How Module 2 was built. Design source of truth: `docs/modules/house-occupancy.md`.
As-built index: `docs/implemented/house-occupancy.md`.

## Process (lead-core-then-waves, all in Docker)
Same proven process as Modules 0–1, on one integration branch `feat/house-occupancy`
(feature branch → PR → `main`; never commit to `main` directly).

**Phase 0 — Lead builds the frozen core** (green gate before any fan-out):
- `app/modules/houses/` package: `models.py` (`house_occupancies` +
  `house_status_history`, partial-unique current slot), `schemas.py` (frozen
  Pydantic contracts + status/party domains, email normalization), `repository.py`
  (SQL-only, society-scoped; occupancy CRUD, history, `current_owner_user_ids`),
  `service.py` (read methods + display-code derivation implemented; `change_status`/
  `edit_occupancy` stubbed for the write wave), `router.py` (thin, dual-gated),
  `spec.py` (`HOUSES_SPEC`, `depends_on: onboarding`, `register_houses`).
- Alembic migration `0003_house_occupancy.py` (chained off `0002_onboarding`);
  `id_proof_document_id` nullable with no FK (Vault deferred). `alembic/env.py` +
  `main.py` wiring.
- Read side folded into the core (it only depends on the frozen repository), so the
  two follow-on waves had no file collision on `service.py`.
- Gate: migration applies, `alembic check` reports no drift, module registers with
  correct perms/deps, all 5 routes live + auth-gated, 218 existing tests green.

**Phase 1 — Waves** (two Opus 4.8 agents, disjoint files → parallel):
- **Wave A — foundation deviations:** completed `revoke_house_access` (occupancy
  unlink + refined orphan check) and the onboarding occupancy-aware delete guard
  (lazy imports to avoid a backward platform/onboarding→houses load-time dep).
- **Wave C — write-side state machine:** `change_status` + `edit_occupancy`,
  owner-identity/replacement (close→flush→revoke→provision, honoring the
  partial-unique current slot), tenant open/edit/close, `first_left_empty_on`
  once-only, audit + status-history.
Verified: app imports clean, 218 existing tests green.

**Phase 2 — Code-review gate** (Opus 4.8 reviewer, read-only) → must-fixes applied:
see `code-review-findings.md`. Found + fixed an ID-proof data-loss bug (retention
on status change) and promoted the `list_houses` N+1 to a must-fix (batched).

**Phase 3 — Test gate** (Opus 4.8 designed the 155-case matrix → Sonnet 5
implemented + ran to green): see `test-gate.md`. 372 total tests, zero product
bugs.

## Sub-agent model assignment (user decision)
- Codebase exploration → **Sonnet 5** (medium).
- Code writing/implementation (core + waves, foundation edits) → **Opus 4.8**.
- Code-review gate → **Opus 4.8**.
- Test-case design (matrix) → **Opus 4.8**.
- Test implementation + running → **Sonnet 5**.

## Libraries
No new third-party libraries were introduced — the module reuses the existing
stack (FastAPI, SQLAlchemy 2.x, Pydantic v2, Alembic). The one candidate
(`email-validator` for Pydantic `EmailStr`) was **avoided**: the codebase already
normalizes/validates emails via `app.common.validators.normalize_email`, so the
module reuses that instead of adding a dependency.
