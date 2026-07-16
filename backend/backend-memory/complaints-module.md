---
name: complaints-module
description: "Module 5 Complaints BUILT — house-scoped complaints, status workflow, Vault images, and the new in-process event dispatcher"
metadata: 
  node_type: memory
  type: project
  originSessionId: 926cc168-8299-4e79-83b2-0a992bbc92eb
---

Module 5 — **Complaints** — is BUILT (branch `feat/complaints`; 4 commits: frozen core → waves → code-review fixes → test gate). As-built: `docs/implemented/complaints.md`. Full backend suite **857 passed, 2 skipped** (168 complaints tests). Followed the standard [[workflow_model_assignment]] / [[implementation-workflow]] process on the shared [[test-infra]] harness.

**What it is:** house-scoped complaints. Owner raises (title + description + category + ≤2 report photos) → admin drives `open→in_progress→resolved→closed→archived` → worker auto-archives closed complaints after `auto_archive_days` (default 15). Photos → Vault under `Houses/<house>/Complaints/<reference>/`. `depends_on: houses`; image routes also gate `require_module('vault')`. 6 perms (create/read/read_all/update_status/manage_categories/configure). Reference `C-000123` via a per-society FOR-UPDATE counter row.

**Key build decisions (not obvious from the code):**
- **A real in-process event dispatcher was built one module early** at `app/common/events.py` (subscribe/emit; synchronous, in-txn; no-op with no subscribers; handler errors swallowed+logged). The design docs marked Complaints→Notifications "✅ wired," but **Notifications is NOT built** — so Complaints emits `complaint.created/withdrawn/status_changed` + a clear-on-read signal now, and Notifications (Module 7) will `subscribe` at startup with zero change. Confirmed with the user (chose "build a real dispatcher" over a bare no-op skeleton).
- **Proof images attach ONLY at the `in_progress→resolved` transition** (multipart `POST /complaints/{id}/resolve`: note + ≤2 proof photos) and are **locked** after — no standalone proof endpoint; cap is per resolve call. User decision refining the spec.
- **Categories seed LAZILY on first use** (`support.ensure_default_categories`), not at enable — same "no edits to the shared foundation enable flow" rule Finance followed.
- **Config PUT is a partial merge** (unspecified keys unchanged).
- Added a `complaint_category.reactivated` audit action (not in the original design §5 — now documented).
- Houses provider extended: `current_owned_houses(society_id, user_id)` + `house_display_code(...)`, consumed via the service interface.

**Integration/QA gotchas worth remembering:**
- An `async def` service method (resolve) MUST be `await`ed in its async router handler — an unawaited coroutine poisons the request session and surfaces later as cascading `AdminShutdown` errors on the next test's DB reset (misleading — looks like a DB bug, is actually the missing await).
- The shared xdist `society_test*` DBs are sensitive to **orphaned idle-in-transaction connections** from killed/overlapping test runs — they make the next `TRUNCATE` race, surfacing as spurious `AdminShutdown`/401/`ConflictError`. Fix: don't overlap runs; run gate files serially (`-n0`) while iterating; `pg_terminate_backend` stale `society_test*` backends between churn-heavy sessions. NOT a logic bug.
- Code-review gate caught real bugs the per-wave tests missed: a status-detail crash when a proof image's Vault doc is trashed (both detail builders now share `support.assemble_detail` + guarded preview), a `date_to` end-day off-by-one, an image-cap check-then-act race (now `get_complaint(lock=True)` FOR UPDATE), and LIKE-metacharacter injection in the `q` filter.

Next: **Notice Board** (Module 6), then **Notifications** (Module 7 — wires the dispatcher + the `maintenance_due` reminder rule Finance left as a seam).
