# Notice Board (Module 6) — Code-Review Gate Findings

Phase 2 of the build. One Opus 4.8 reviewer audited all wave code read-only
against tenant isolation, draft/withdrawn visibility, active-feed correctness,
the sanitizer choke-point, single-emit publish, `last_edited_at` semantics, the
attachment race/rollback ordering, trashed-attachment read safety, the receipts
denominator + N+1, idempotent reads, audit completeness, error codes, and
psycopg3 gotchas. The reviewer confirmed **no MUST-FIX crashes or security
holes** — those areas are fundamentally sound. The findings below (a real test
gap + a test-reliability bug + nits) were applied and the suite re-run green
(60 notices / 917 full).

## Applied

- **SHOULD (S1) — no cross-tenant isolation test existed.** The single most
  important threat area had zero coverage; `second_society_with_notices` was built
  but unused. **Fix:** added `tests/test_notices_isolation.py` (3 tests): admin B
  cannot read/receipt/attach/withdraw/edit A's notice (all 404, no existence
  leak); B's feed + archive never contain A's notices; A's reads/owners never
  leak into B's receipts. (The code already scoped every query — this is the
  guard-rail.)
- **SHOULD (S2) — `freeze_utcnow` did not patch the archive/lifecycle `now`.**
  `receipts.py` and `lifecycle.py` bind a module-local `utcnow` (`from
  app.common.time import utcnow`) the helper's freeze list never touched, so the
  archive-path determinism was vacuous. **Fix:** added
  `...services.receipts.utcnow` + `...services.lifecycle.utcnow` to
  `_UTCNOW_CONSUMERS` in `tests/_notices_helpers.py`. (Test-reliability only;
  production `utcnow` is correct.)
- **NIT (N1) — no negative sanitizer test for `javascript:`/`data:` URLs or
  `img`.** Tests covered `<script>`/`onclick` but not the URL-scheme + image
  policy (spec §4). **Fix:** added `test_create_sanitizes_dangerous_urls_and_images`
  asserting a `javascript:`/`data:` href, `<img onerror>`, and `<iframe>` are all
  stripped while the one safe `https` link + text survive — locks the nh3
  whitelist against future edits.
- **NIT (N2) — `repository.py` re-derived a `STATUS_WITHDRAWN_LITERAL` at the
  module bottom** to "keep the import graph flat," inconsistent with the rest of
  the module (it already imports `STATUS_PUBLISHED` from `schemas`). **Fix:**
  import `STATUS_WITHDRAWN` from `schemas` and delete the literal.
- **NIT (N4) — `notice_posted` emitted a raw `datetime`.** No-op today
  (Notifications unbuilt), but a future subscriber serializing the payload would
  need special handling. **Fix:** emit `published_at` as `.isoformat()` (matches
  this module's audit idiom; JSON-safe for the Notifications wire-up).
- **NIT (S3/N3) — attachment response `is_read` + `read_all` docstring.** The
  attachment-add response now reports `is_read=False` (consistent with `create`;
  the managing admin hasn't "read" the notice). The `read_all` docstring was
  softened to state it returns the active-notice count and the route discards it.

## Verified clean (no change needed)

Tenant isolation in every repository query (`society_id`-scoped; cross-society id
→ 404); draft/withdrawn visibility (residents' feed forced `active_only`;
`get_detail` returns 404 not 403 for non-published to non-managers — no existence
leak); active/archive filters (`published AND (expires_at IS NULL OR > now)` /
`withdrawn OR (published AND <= now)`, consistent boundary, pinned-first ordering);
sanitizer single choke-point (body written only via `support.sanitize_body` on
create + edit); single-emit publish (both create-with-`publish` and the publish
endpoint funnel through `support.apply_publish`, `notice.id` present); attachment
rollback ordering (store-then-insert on add, Vault-soft-delete-before-drop on
remove — no orphan row on 413/415); trashed-attachment read safety
(`preview_url_or_none`/`download_url_or_none` swallow → None); receipts
(current-owner denominator, in-memory split, no per-owner loop); N+1 batching
(`attachment_counts_for` + `read_notice_ids_for`); idempotent read insert
(`ON CONFLICT DO NOTHING`, preserves first `read_at`); audit completeness
(all six mutations in-transaction with spec §5 action names, datetimes ISO-safe;
reads/receipts not audited); error codes (404/403/401/409/422 all correct);
psycopg3 (`.in_()` everywhere, no raw `IN :tuple`); migration matches models.
