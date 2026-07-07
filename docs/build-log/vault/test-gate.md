# Vault (Module 3) — Test Gate

Opus designed the matrix (~150 cases); Sonnet 5 implemented and ran it to green in
Docker. Tests use the in-memory `ObjectStorage` fake for the bulk suite (fast,
xdist-parallel-safe) and a small real-MinIO subset for the storage wiring.

## Result
- Full parallel suite (`scripts/run-tests.sh`): **521 passed, 1 skipped** (0 failed).
  - The skip is by design: `test_presigned_url_http_fetch_when_reachable` self-skips
    because `MINIO_PUBLIC_ENDPOINT` (localhost:9000) ≠ `MINIO_ENDPOINT` (minio:9000),
    so the localhost-signed URL is not reachable from inside the container.
- 150 new Vault tests across 9 files.
- The pre-existing ~15%-flaky JWT tamper test (repo PR #5) flickered once mid-run in
  the agent's session but passed on re-run and in the final verification; unrelated
  to Vault, not touched.

## Files
`tests/_vault_helpers.py`, `test_vault_smoke.py`, `_happy.py`, `_badpaths.py`,
`_edge.py`, `_security.py`, `_vulnerabilities.py`, `_e2e.py`, `_storage_realminio.py`.

## Coverage
Happy (folder CRUD, nested + breadcrumb, upload/usage, preview/download, rename/move,
soft-delete→trash→restore, empty-trash, pagination), bad paths (404/409/422 across
folders/documents/trash), edge (deep nesting, cycle-guarded move, cascade
delete+restore subtree, restore name-collision loop, quota boundary at exactly the
limit and +1, denylist incl. case/double-ext/trailing-dot/space, config override),
security **with and without roles** (resident denied, read-only principal, module
disabled, cross-society isolation → 404, forged tokens/roles, super-admin bypass,
must-change lockout), vulnerabilities (path traversal, storage-key confinement,
cross-society presigned-URL denial, executable content-type block, quota bypass,
denylist bypass via rename/uppercase), e2e (foundation→onboarding→houses→vault
ID-proof journey, rename-safe derived folder name, idempotent Proof folder, worker
purge + reconcile jobs), and real-MinIO (bucket bootstrap, put/stat round-trip,
presigned-URL shape + disposition, delete/idempotent-delete, full HTTP upload).

## Product bugs found by the tests (fixed in app/, not papered over)
1. **`services/jobs.py` (purge_trash) + `services/trash.py` (empty_trash)** — folders
   were sorted deepest-first in Python but deleted with a single trailing `flush()`;
   SQLAlchemy batches same-table deletes ordered by PK, discarding that order and
   able to delete a parent before its child → `ForeignKeyViolation` on `parent_id`.
   Fix: `flush()` after each folder delete to preserve child-before-parent order.
   Caught by `test_vault_e2e.py::test_purge_cascaded_folder_subtree`.
2. **`core/storage/minio_storage.py`** — neither Minio client pinned a `region`, so
   the SDK's presign path did a live `GetBucketLocation` round-trip against the
   signing client's endpoint (the public `localhost:9000`, unreachable from inside
   the container) → every presigned-URL call failed with a connection error. Fix:
   pass `region="us-east-1"` to both clients, removing the network call. Caught by
   the real-MinIO presign tests.

No migration or frozen-schema changes were needed.
