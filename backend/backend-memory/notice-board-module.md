---
name: notice-board-module
description: "Module 6 Notice Board BUILT — society-wide broadcast, draft→published→withdrawn, query-time expiry, Vault attachments, sanitized rich text, read receipts"
metadata: 
  node_type: memory
  type: project
  originSessionId: 92bec0c1-e96f-4f16-917a-69a454293afa
---

Module 6 — **Notice Board** — is BUILT (branch `feat/notice-board`; 4 commits: frozen core → 4 waves → code-review fixes → test gate + sanitization hardening). As-built: `docs/implemented/notice-board.md`. Full backend suite **1010 passed, 2 skipped** (153 notices tests). Standard [[workflow_model_assignment]] / [[implementation-workflow]] process on the [[test-infra]] harness; builds on [[complaints-module]]'s event dispatcher.

**What it is:** a society-wide **broadcast board** (no `house_id` — notices are society-scoped). Admin composes a rich-text notice (+ unlimited Vault attachments) → `draft → published → withdrawn`; edit-after-publish (stamps `last_edited_at`, content-only), pin, optional **expiry evaluated at QUERY TIME** (`expired` is never a stored status — no worker). Residents read the active feed (portal landing page); admin sees per-notice **read receipts** + an archive. `depends_on: houses`; attachment routes also gate `require_module('vault')`. 3 perms: `notices.read` (residents+admin), `notices.publish` (admin manage), `notices.read_receipts` (admin receipts+archive). 11 routes under `/notices/*`.

**Key build decisions (not obvious from the code):**
- **New `nh3` dependency + `app/common/html_sanitizer.py`** (Foundation-level util). nh3 = the Rust "ammonia" binding — chosen over the **deprecated/archived `bleach`** (a quick supply-chain check confirmed bleach's final release 2026-06, no future security patches). `sanitize_html` (formatting whitelist for body) + `sanitize_plain_text` (strip-all for title).
- **BOTH title AND body are sanitized** (user decision — defense-in-depth beyond the spec, which only promised body). Title is markup-stripped to plain text; a markup-only title → 422. Body rejects empty-after-strip content (whitespace-only / `<p></p>` → 422) so the two blank guards are consistent. Single choke-points: `support.sanitize_title` / `support.sanitize_body`.
- **`support.apply_publish` is the SINGLE publish write** (stamp `published_at` + emit `notice_posted` ONCE) shared by create-with-`publish=true` (Wave A) and the explicit publish endpoint (Wave B) so they never diverge.
- **Notifications wired as a skeleton no-op** (same as [[complaints-module]]): emits `notice_posted` + `mark_read_for` to `app/common/events`; no subscribers yet → no-op; Notifications (Module 7) subscribes with zero call-site change. `notice_posted` payload `published_at` is ISO-string (JSON-safe for the future subscriber).
- **Read receipts denominator = CURRENT owners** via Occupancy `current_owner_user_ids` — a broadcast, not a frozen snapshot; owners provisioned after a post count as unread; a reader who is no longer an owner is excluded. Built in-memory from one owner-set + one reads fetch (no per-owner loop).
- **`mark_read_for` fires on EVERY notice open** (the `notice_reads` row is idempotent via `ON CONFLICT DO NOTHING`, the clear-on-read signal is not deduped).
- **Draft/withdrawn → 404 for non-managers** (a resident id-guess for an unpublished notice is indistinguishable from a nonexistent one — no existence leak), not 403.
- Vault was **already pre-wired for notices** (`ensure_notice_folder`, `source='notice'`, `notices_root`/`notice` system keys) — reused, no Vault changes. Attachments under `Notices/<notice id>/`.

**QA notes:** Code-review gate found **no MUST-FIX bugs** — isolation, sanitizer choke-point, single-emit publish, receipts, N+1 batching, attachment rollback all sound. Applied a real missing cross-tenant isolation *test* + a `freeze_utcnow` test-reliability fix (it missed the `receipts.utcnow`/`lifecycle.utcnow` module-local bindings) + nits. Expiry boundary is `expires_at <= now ⇒ expired` in code (assert the code, not the spec prose which says `< now`). Same xdist orphaned-connection hygiene as prior modules.

Next: **Notifications** (Module 7) — subscribes the dispatcher to the `complaint.*` and `notice_posted` events already emitted, + wires Finance's `maintenance_due` reminder seam.
