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

## Deliberately not changed (documented)
- **#2 orphan MinIO object** — if anything after `put_object` (audit/commit) fails,
  the row rolls back but the object may remain. This is the *acceptable,
  reconcilable* failure mode (no orphan ROW is ever produced; `reconcile_usage`
  re-sums rows and purge only touches keyed objects). Not a data-integrity break.
- **#7 house folder derived-vs-stored name** — collision checks use the stored
  name while the UI shows the derived house code. Impact is negligible (system
  folders are few, admin-only) and fixing it would couple every collision check to
  onboarding lookups. Left as-is.
- **empty_trash `_folder_depth` per-folder walk** — bounded by (shallow) tree depth
  on a rare admin action; not worth pre-computing.

Post-fix: app imports clean, `alembic check` clean, full suite green (372).
