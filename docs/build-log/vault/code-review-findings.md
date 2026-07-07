# Vault (Module 3) — Code-Review Gate Findings

Read-only Opus review of the Phase-1 wave code (vault module + storage + houses
wiring) against `docs/modules/vault.md`. 13 findings triaged below; all real ones
fixed on `feat/vault`.

## Verified correct (no change)
Tenant isolation (every service method resolves objects society-scoped),
presigned-URL authorization (preview/download require a live society-scoped doc
before signing), storage-key derivation (server-trusted `society_id`+doc id, no
traversal), system-folder protection (create/rename/move/delete all reject
`is_system`; cycle guard on move), cascade delete + restore correctness, FK-safe
deletion order in empty_trash/purge, migration 0004 (FKs/indexes/partial-uniques
match models; id-proof FK is `ON DELETE SET NULL`), and router gating (reads vs
manage; id-proof route requires both `houses`+`vault`).

## Fixed
| # | Sev | File | Issue | Fix |
|---|-----|------|-------|-----|
| 1 | must | houses/router.py | `UploadFile.content_type`/`filename` can be `None` → NOT NULL `IntegrityError` → 500 on the id-proof route (vault's own route already defended) | `file.content_type or "application/octet-stream"`, `file.filename or "unnamed"` |
| 3 | should | documents.py, repository.py, trash.py, jobs.py | Quota check is TOCTOU + Python read-add on `used_bytes` → concurrent uploads can exceed the limit / lose an increment | `get_or_create_usage(lock=True)` takes `SELECT … FOR UPDATE`; used in upload + empty_trash + purge so the check-increment is serialized |
| 6 | should | trash.py | Restore appended `" (restored)"` once with no re-check → a second restore could hit the folder partial-unique → 500 | `_unique_restored_name` loops `(restored)`, `(restored 2)`, … until free (folders + documents) |
| 8 | nit→sec | documents.py | `evil.exe.` / `evil.exe ` (trailing dot/space) bypassed the extension denylist | `_sanitize_filename` strips trailing dots/spaces before extension analysis |
| 4 | should | trash.py | `_restore_subtree` re-ran a society-wide trashed-docs query per folder (O(folders×docs)) | Bucket trashed docs by `folder_id` once |
| 5 | should | documents.py | Combined rename+move disambiguated the name against the SOURCE folder → spurious `(1)` even when free at the destination | Resolve destination first; disambiguate rename against the destination |

## Also fixed (initially accepted, then fixed on request)
- **#2 orphan MinIO object** — object storage is not transactional with the DB, so
  a crash between `put_object` and the request commit can leave an object with no
  backing row (never an orphan ROW). Now swept: `reconcile_usage` lists keys under
  each society's `societies/{id}/` prefix and deletes any with no live-or-trashed
  `vault_documents` row (added `ObjectStorage.list_keys` + `all_storage_keys` repo
  helper). Idempotent, nightly. Tests: `test_reconcile_sweeps_orphan_object`,
  `test_reconcile_keeps_referenced_objects`.
- **#7 house folder derived-vs-stored name** — `_assert_no_sibling` now compares
  against each sibling's DERIVED display name (house folders show the current house
  code), so a custom folder can't duplicate a renamed house folder's visible name.
  Test: `test_create_folder_colliding_with_house_derived_name_409`.

## Deliberately not changed (documented)
- **empty_trash `_folder_depth` per-folder walk** — bounded by (shallow) tree depth
  on a rare admin action; not worth pre-computing.

Post-fix: app imports clean, `alembic check` clean, full suite green (524 passed,
1 skipped).
