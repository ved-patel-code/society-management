# Complaints (Module 5) — Code-Review Gate Findings

Phase 2 of the build. One Opus 4.8 reviewer audited all wave code read-only against
tenant isolation, the reference-allocator race, the transition table, image caps /
proof lock, resolve atomicity, config merge safety, worker idempotency/isolation,
audit completeness, the event dispatcher, N+1, and error-code correctness. The
reviewer confirmed those areas fundamentally sound; the defects below were fixed and
the suite re-run green (87 complaints / 776 full).

## Fixed

- **MUST — `status._detail` crashed on a trashed/purged proof image.** `status.py`
  called `vault_api.get_preview_url(...).url` unguarded while `crud._detail` guarded
  it. An admin status change on a complaint whose proof document was later trashed
  would 404 an already-applied transition. **Fix:** consolidated both into one
  `support.assemble_detail` + `support.preview_url_or_none` (guarded → `None`), used
  by every returning path in both services. Also resolves the B/C `_detail`
  divergence (finding #6).
- **MUST — `date_to` list filter dropped the whole end day (off-by-one).**
  `created_at < date_to` (a `date` at midnight) excluded complaints raised on
  `date_to` itself. **Fix:** `created_at < date_to + 1 day` (inclusive of the day).
  `repository.list_complaints`.
- **SHOULD — report/proof image-cap check-then-act race.** Two concurrent adds could
  both pass `count < max` and over-commit. **Fix:** `get_complaint(..., lock=True)`
  (`SELECT … FOR UPDATE`) at the start of `images.add_report_image` and
  `status.resolve`, serializing per-complaint image mutation.
- **SHOULD — auto-archive cutoff was midnight-truncated (≥ N-to-N+1 days).** **Fix:**
  the worker now measures the window from a real aware-UTC `utcnow()` instant, not
  the run date floored to midnight. `jobs._run_for_societies(now: datetime)`; the
  test drives a fixed `NOW`.
- **SHOULD — `q` search treated `%`/`_` as wildcards (LIKE-metacharacter, not SQL,
  injection).** **Fix:** escape `\ % _` and pass `escape="\\"` to `.like`.
- **SHOULD — two divergent `_detail` helpers.** Consolidated (see the MUST above).

## Documented (not a code change)

- **NIT — `complaint_category.reactivated` audit action** wasn't in the design's §5
  list. It is a legitimate distinct state change; **docs/modules/complaints.md §5
  updated** to list it.

## Deferred (tracked, low risk — not a live bug)

- **NIT — image/history repository queries key only on `complaint_id`, not
  `society_id`.** Not exploitable today (every call path first fetches the parent
  complaint society-scoped via `get_complaint`), but it doesn't match the module's
  "all society-scoped" invariant. Follow-up: thread `society_id` into
  `get_image`/`list_images`/`count_images`/`image_counts_for`/`list_status_history`
  (both tables carry `society_id`, so the scoping is free) — deferred to avoid a
  broad signature change immediately before the test gate.
- **NIT — config audit uses `entity_type="society_module"`, `entity_id=society_id`**
  (a stand-in; config is per-society). Harmless; left as-is.

## Verified clean (no change needed)

Reference allocator (`FOR UPDATE` counter + partial-unique backstop); tenant
isolation in list/detail (`house_ids=[]` → no rows, never "all"; raise 403/422
split); transition table matches §3 (withdraw = owner open→withdrawn only, archive =
worker only, admin endpoint refuses resolved/withdrawn/archived, reopen clears
`resolved_at`); proof genuinely un-add/removable after resolve; resolve atomicity
(one txn; Vault 413/415 rolls back the whole request); worker per-society commit +
failure isolation + idempotency; config partial-merge (whitelist, preserve, revalidate,
reassign-JSON); event dispatcher (swallow+log handler errors, no-op with no
subscribers); audit completeness; no N+1 in list.
