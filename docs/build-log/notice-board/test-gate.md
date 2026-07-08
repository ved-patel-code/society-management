# Notice Board (Module 6) — Test Gate (Phase 3)

Opus 4.8 designed the matrix; Sonnet 5 implemented + ran it to green. The gate adds
the cross-module, cross-society, adversarial, and regression coverage the per-wave
suites structurally cannot (each wave saw only its own file).

## Result
- **93 gate tests** across 6 files (e2e 8, enable 8, security 27, edge 11,
  robustness 36, isolation +3 → 6 total incl. the 3 from the review gate).
- Full notices suite: **153** (60 per-wave/review + 93 gate).
- **Full backend suite: 1010 passed, 2 skipped** (857 prior + 153 notices),
  deterministic across repeated clean runs, both serial (`-n0`) and parallel
  (xdist, 8 workers).
- Run: `docker compose exec backend bash scripts/run-tests.sh`.

## Files
- `tests/test_notices_e2e.py` (8) — full HTTP journeys Foundation→Onboarding→
  Houses→Vault→Notices: draft → edit (`last_edited_at`) → publish (asserts the
  `notice_posted` payload via a captured subscriber) → pin/expiry → attachment
  filed under `Notices/<id>/` in Vault → multiple owners read → receipts reflect
  read/unread → withdraw → archive; the ordered audit trail
  (created/edited/published/attachment_added/withdrawn, filtered by
  `entity_type='notice'`); the `notice.mark_read` event on every open (read row
  idempotent); pin+expiry feed position; a text-only notice with Vault disabled;
  a published-then-expired notice openable by direct id but off the feed + in
  archive.
- `tests/test_notices_enable.py` (8) — enable seeds exactly the 3 perms + role
  grants (resident=read; society_admin=all 3); `depends_on: houses` enforced
  (409); absent/disabled module → all routes 403; the super-admin `require_module`
  bypass (200); vault-off → attachment routes 403 while text notices work fully;
  re-enable idempotent (no duplicate perms/grants).
- `tests/test_notices_security.py` (27) — every endpoint with AND without the
  required role: 401 unauth across all 11 routes; a resident (only `notices.read`)
  allowed feed/detail/read-all but forbidden create/edit/publish/withdraw/attach/
  remove/receipts/archive (403); a no-notices-perm caller → 403 even on read; the
  **read vs read_receipts split** (a caller with read+publish but not
  read_receipts → 403 on receipts + archive, still 200 on feed + publish);
  super-admin bypass (receipts, drafts); a crafted cross-society token cannot act
  (scopes to empty / 403).
- `tests/test_notices_isolation.py` (6) — society A never sees/acts on B's
  notices/attachments/reads/receipts (404, no existence leak); feed + archive
  never cross; A's read-state never leaks into B's receipts; cross-society
  attachment-id guess → 404; per-society audit scoping; read-all is per-society.
- `tests/test_notices_edge.py` (11) — legal draft edges (publish/discard);
  published→withdraw→publish (409, terminal); published still editable; the expiry
  boundary asserted against the **code's `<= now` ⇒ expired** semantics
  (exactly-equal / just-before / just-after, via `freeze_utcnow`); multi-pin
  ordering (`published_at` DESC among pins); the full unread-count lifecycle
  (publish/open/read-all/re-publish); a late-provisioned owner sees the notice
  unread on the feed; empty feed; pagination (total vs page, no overlap/gap).
- `tests/test_notices_robustness.py` (36) — the **stored-XSS battery** (10
  payloads: `<script>`, `<img onerror>`, `javascript:`/`data:`/`vbscript:` hrefs,
  `<svg onload>`, `<style>`, `<iframe>`, event handlers, nested/broken tags) all
  neutralized in the stored + returned **body** (embedded in real surrounding
  content — the realistic injection) on create AND edit AND publish-on-create;
  **title** markup fully stripped (title is plain text); a markup-only title or an
  empty-content body → 422; Vault 413 (quota) / 415 (denied type) on attach roll
  back with no orphan `notice_attachments` row and unchanged notice state + no
  audit; malformed create → 422 (missing/blank title, missing/blank/over-max
  body); nonexistent ids → 404 sweep across all id-bearing routes; duplicate read
  stays idempotent.

## Helpers added (tests/_notices_helpers.py, append-only)
Promoted `society_with_tiny_quota` (forces Vault 413) from the attachments wave;
added `crafted_bearer` (cross-society JWT via `make_token`). No existing signature
changed.

## Ambiguities resolved against the code (documented, not app bugs)
1. **Expiry boundary `== now` ⇒ expired** — `support.is_expired` uses
   `expires_at <= now`; the exact-equal instant is off the active feed / in
   archive. Tests assert the code, not the (slightly divergent) spec prose.
2. **`mark_read_for` fires on every open** — the read *row* is idempotent
   (`ON CONFLICT DO NOTHING`), but the clear-on-read *signal* is emitted each open.
   Asserted as real behavior.
3. **Title sanitization added mid-gate (user decision).** The matrix flagged that
   only `body` was sanitized; the user chose defense-in-depth, so `title` is now
   markup-stripped too (see `code-review-findings.md`). Tests assert the new
   behavior (title stripped; markup-only title → 422). Empty-content body also →
   422 (the title/body blank guards are now consistent).
4. **Crafted-token `role_ids` are not trusted** — effective permissions are
   computed live from `user_roles` for the JWT's `active_society_id`
   (`core/deps.py`), not the token's `role_ids` claim. The real guarantee is the
   `active_society_id` scope; tests assert cross-society denial.
5. **Super-admin bypass** — `is_super_admin` bypasses `require_module` +
   `require_permission`. Asserted as by-design 200s, never "even super-admin →
   403" (matches the Complaints gate convention).
