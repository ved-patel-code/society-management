# Notifications (Module 7) — Code Review Findings

> Phase C: three parallel adversarial review agents (engine+handlers; dues+jobs+
> foundation adds; router+feed+config+schemas). Findings + resolutions.

## CRITICAL (fixed + verified)
1. **`insert_many` ON CONFLICT on a PARTIAL unique index omitted `index_where`** —
   `repository.py`. The conflict target `uq_notifications_society_dedupe` is a
   partial index (`WHERE dedupe_key IS NOT NULL`). Postgres requires the arbiter's
   predicate to match, else it raises `InvalidColumnReference: there is no unique
   or exclusion constraint matching the ON CONFLICT specification` — which would
   make **every** notification insert throw (silently swallowed by the bus →
   zero notifications ever created). Found independently by two review agents.
   **Fix:** add `index_where=text("dedupe_key IS NOT NULL")` to
   `on_conflict_do_nothing(...)`. **Verified** against real Postgres: dedupe row
   inserts 1 then 0 on re-fire; NULL-dedupe fan-out rows all insert. The count via
   `RETURNING ... .all()` correctly excludes conflict-skipped rows.

## MEDIUM (fixed)
2. **Dues worker N+1 — batched `owner_user_ids_by_house` existed but was unused.**
   `jobs.py` resolved owners one-query-per-house. **Fix:** the dues job now
   batch-resolves owners for all owing houses in ONE query and passes the set into
   `build_for_house` (new optional `owners=` param); the per-house path remains for
   on-demand/single use.
3. **Dead + wrong iteration fallback** — `row[0] if isinstance(row, tuple) else
   row.house_id`. `houses_owing` returns real tuples, so the `else` was dead code
   and referenced a nonexistent `.house_id` attribute (a refactor booby-trap).
   **Fix:** simplified to `for house_id, _ in owing`.

## LOW (fixed / hardened)
4. **Fresh session per society in both worker scans** — the loops shared one
   session; a connection-level failure in one society could taint the next.
   **Fix:** each society now gets its own `SessionLocal` (commit + rollback +
   close per society) — hard containment, matching the resilience requirement.
5. **`GET /config` redundant double permission-check + unused `_auth` param.**
   **Fix:** dropped the `_auth` param; `dependencies=_CONFIGURE` already gates it.
6. **Dead `get_owned_unread` repo method** (feed used `mark_one_read` +
   `exists_owned`). **Fix:** removed.

## HIGH (found in Phase D by the test agents — fixed)
7. **Handler own-session design was not atomic and broke test isolation.** The
   handlers originally opened their own `SessionLocal` and committed independently.
   Two problems: (a) notifications were a SEPARATE transaction from the source
   action (a crash between the two commits loses the notification — violates the
   atomicity/no-data-loss requirement and the design §4.1 "same transaction"
   intent); (b) the orphaned committed connections held locks that deadlocked the
   next test's `TRUNCATE`, cascading errors across test files. **Fix:** thread the
   emitter's request `session` onto the event payload; handlers run in that
   session inside a **SAVEPOINT** (`begin_nested`) — atomic with the source action,
   with a handler failure rolling back only its own writes (logged+swallowed). The
   emitters gained one additive `session=` kwarg; the bus is unchanged. Verified:
   all 76 notifications tests pass together (no pollution); complaints/notices
   suites still green.

## Confirmed-correct (checked, no change)
- Per-recipient dedupe suffix (`:{user_id}`) prevents fan-out collapse while
  keeping each recipient independently idempotent.
- Handler session isolation + failure containment (`_in_own_session`): opens own
  session, commits, rolls back + logs + never re-raises; works from payload only.
- Cross-tenant safety: feed/mark-read scoped by `society_id`+`user_id`;
  `mark_entity_read` by `user_id`+entity is exact (a user has rows only in their
  society). No cross-society read/clear possible.
- Cadence math (`is_fire_day`): advance at `delta==-X`, due-day at `0`, recurring
  at `delta>0 and delta%N==0`; `N>=1` so no div-by-zero; anchor = most-recent
  outstanding due date (per §4.3).
- Consolidation: one notification per fire = Σ all unpaid months (never
  one-per-month). Decimal → `str()` in payload (JSONB-safe).
- Eligibility: dues scan = notifications ∩ finance enabled; purge =
  notifications-enabled.
- `user_ids_with_permission`: dual-role union, tenant-scoped (no cross-society
  leak). `owner_user_ids_by_house`: single IN query, grouped, scoped.
- Route ordering: static routes before dynamic `/{id}/read`; all gates present.
- Config partial-merge: preserves untouched keys, re-validates, bounds enforced,
  all-None → 422, extra keys forbidden.
