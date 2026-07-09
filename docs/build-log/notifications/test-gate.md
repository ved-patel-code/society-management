# Notifications (Module 7) — Test Gate

> Phase D. Shared harness `tests/_notifications_helpers.py` (isolated per-worker
> `society_test` DB, truncate+reseed per test). Run:
> `docker compose exec backend bash scripts/run-tests.sh`.

## Files
- `tests/_notifications_helpers.py` — enable, bearers, owner/admin provisioning,
  drive-emitters-over-HTTP, feed reads, DB introspection, second society, crafted
  bearer, event capture.
- `tests/test_notifications_smoke.py` — harness self-check (complaint→admin,
  notice→owner, dues worker + idempotency).
- `tests/test_notifications_feed.py` — feed/badge/mark-read happy + bad + edge.
- `tests/test_notifications_security.py` — 401/403 per route, with/without
  `notifications.read`/`configure`, cross-society isolation, own-only, crafted JWT.
- `tests/test_notifications_events.py` — each event → correct recipients/type/
  payload; batched notice fan-out; soft-dependency no-ops.
- `tests/test_notifications_e2e_and_markread.py` — clear-on-read (direct + via
  source-item open), full lifecycle, reopen-after-read.
- `tests/test_notifications_dues.py` — cadence (is_fire_day), consolidation,
  idempotency, auto-stop, read-purge.
- `tests/test_notifications_scale_resilience.py` — batched fan-out (one INSERT for
  N owners), multi-society dues scan, per-society failure isolation, crash/
  idempotent replay, handler-failure containment.

## Coverage (required, per plan §D)
Happy · bad/exception · edge (dedupe idempotency, soft-disabled no-op, paid house,
reopen, empty owner set, retention boundary) · security (perms + cross-society +
own-only) · e2e across complaints/notices/finance · **scale** (batched fan-out /
no N+1) · multi-society · **crash-resilience** (kill mid-scan → re-run exactly
once; down-a-day → catch-up) · **handler-failure containment** (one bad
subscriber/society doesn't drop others).

## Counts
**62 notifications tests** across 7 files: smoke 3 · feed 11 · security 16 ·
events 9 · e2e+markread 7 · dues 11 · scale/resilience 5. All green together (no
cross-file pollution after the session-threading fix).

## Result
Full suite **green in Docker**: 1085 passed, 2 skipped (prior 1023 + 62 new
notifications tests). Two pre-existing notices tests updated to ignore the new
`session` transport key on the event payload; one date-fragile complaints
regression test pinned to a deterministic `created_at` (both unrelated to
notification behavior). Run: `docker compose exec backend bash scripts/run-tests.sh`.
