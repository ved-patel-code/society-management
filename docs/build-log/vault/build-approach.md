# Vault (Module 3) — Build Approach

How Module 3 was built. Design source of truth: `docs/modules/vault.md`. As-built
index: `docs/implemented/vault.md`. QA records: `code-review-findings.md`,
`test-gate.md` (this folder).

## Process (lead-core-then-waves, all in Docker)
Same proven process as Modules 0–2, on one integration branch `feat/vault` (feature
branch → PR → `main`; never commit to `main` directly).

**Phase 0 — Lead builds the frozen core** (green gate before any fan-out):
- `app/modules/vault/` package: `models.py` (3 tables), `schemas.py` (frozen
  contracts + domains), `repository.py` (SQL-only, society-scoped), `service.py`
  facade + `services/{folders,documents,trash,jobs}.py` (reads implemented; writes
  stubbed per wave), `api.py` (public inter-module contract), `router.py` (thin,
  dual-gated), `spec.py` (`VAULT_SPEC`, `depends_on: onboarding`, denylist+retention
  config, `register_vault`).
- Extended the foundation `ObjectStorage` ABC; added `provider.get_storage()` stub.
- Migration `0004_vault.py` (chained off `0003`): 3 tables + the deferred
  `house_occupancies.id_proof_document_id → vault_documents.id` FK (`ON DELETE SET
  NULL`), reflected on the `HouseOccupancy` model. `alembic/env.py` + `main.py`
  wiring; `config.py` MinIO bucket/public-endpoint; pinned MinIO image.
- Split the service into a `services/` subpackage (facade + 4 concern files) so the
  parallel waves owned disjoint files with no `service.py` collision.
- Gate: migration applies, `alembic check` no drift, module registers with correct
  perms/deps, all 12 routes live + auth-gated, 372 existing tests green. Updated 7
  houses ID-proof tests to reference a real vault document (the new FK rejects the
  old placeholder ids).

**Phase 1 — Waves** (five Opus 4.8 agents, disjoint files → parallel):
- **A — storage:** `MinIOStorage` (bucket bootstrap, presign against a separate
  public-endpoint client, delete) + in-memory fake + provider override seam.
- **B — folder tree:** create/rename/move/delete, cycle-guarded, system-folder
  protection, cascade soft-delete, `ensure_house/notice_folder`, rename-safe display
  name.
- **C — documents:** atomic upload (denylist/quota/collision/checksum/storage-key),
  presigned preview/download, DB-only rename/move, soft-delete.
- **D — trash/quota/jobs:** restore (subtree+ancestor rehydration), empty-trash,
  usage accounting, `purge_trash`+`reconcile_usage` worker jobs.
- **E — consumer wiring:** House & Occupancy ID-proof upload endpoint calling the
  vault api (`ensure_house_folder`+`store_document`), sets the FK.
Verified: app imports clean, no drift, 372 existing tests green.

**Phase 2 — Code-review gate** (Opus 4.8 reviewer, read-only) → findings applied:
see `code-review-findings.md`. Confirmed tenant isolation / presigned-URL auth /
storage-key safety / system-folder protection / migration / gating all correct;
fixed 6 real issues (id-proof content-type None→500, quota TOCTOU/lost-update →
`FOR UPDATE` lock, restore collision-suffix loop, trailing-dot denylist bypass,
restore N+1, move+rename destination disambiguation).

**Phase 3 — Test gate** (Opus 4.8 designed the ~150-case matrix → Sonnet 5
implemented + ran to green): see `test-gate.md`. 521 pass / 1 skip. The tests
surfaced 2 genuine product bugs (FK-order batched-delete in purge/empty-trash; a
MinIO presign region round-trip against the unreachable public endpoint) — both
fixed in `app/`, not papered over.

## Sub-agent model assignment (user decision)
- Codebase exploration → **Sonnet 5** (medium). [Note: the initial explore agents
  this build were accidentally run on Opus; corrected going forward — always set the
  model explicitly per stage.]
- Code writing/implementation (core + 5 waves) → **Opus 4.8**.
- Code-review gate → **Opus 4.8** (medium).
- Test-case design (matrix) → **Opus 4.8**.
- Test implementation + running → **Sonnet 5**.

## Libraries
No new third-party libraries were introduced. The two the module needs —
`minio==7.2.20` (client SDK) and `python-multipart==0.0.32` (upload parsing) — were
already vendored by the foundation. Both were safety-checked before use:
`python-multipart` 0.0.32 is past the CVE-2024-53981 fix (0.0.18); the `minio`
Python client has no known client-side CVEs (the CVEs found are MinIO-server-side).
The MinIO **server** image was pinned off `:latest` to a vetted release for
reproducible builds.
