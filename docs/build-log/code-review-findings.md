# Code-Review Findings — Platform Foundation

> What the **expert code-review agent** (Phase 3 gate) flagged when it audited the
> Platform Foundation feature code, why each issue mattered, how it was fixed, and
> how it was detected. This is the record for the automated review gate only.
> Build-time problems caught during development (not by the review agent) live in
> [`build-corrections.md`](build-corrections.md).

- **Reviewed:** all Phase 2 feature packages (auth, users/provisioning, roles, societies, /me) + the shared core they depend on.
- **Method:** a single read-only expert agent read every feature file against the authoritative specs (`docs/platform/platform-foundation.md` = PF, `docs/03-backend-and-db-principles.md` = BDB, `docs/02-architecture.md`) and produced a severity-ranked findings list. No files were edited during review.
- **Verdict:** architecture clean (strict layer separation, no raw SQL / SQLi-safe, JWT algorithm always pinned, password hashes never in responses, tenant scoping holds, roles-by-copy + one-society-per-user correct). "Close, not shippable exactly as-is" — a small must-fix set.
- **Outcome:** all CRITICAL/HIGH/relevant-MEDIUM must-fixes applied and re-verified in Docker (16/16 tests, theft-flow behavior preserved, new audit event present). Deferred items are policy/altitude and explicitly logged below.

Severity scale: **CRITICAL** (security/correctness, must fix) · **HIGH** · **MEDIUM** · **LOW** · **NIT**.

---

## Fixed in this pass

### C1 — Theft-path `commit()` committed the whole request session and skipped the audit
- **Severity:** CRITICAL
- **File:** `backend/app/platform/auth/token_service.py` (`TokenService.rotate`, theft branch)
- **What was wrong:** on refresh-token reuse (theft), the code revoked the user's tokens on the **request session** and called `self._session.commit()` before raising `AuthenticationError`. `Session.commit()` is not scoped to the revocation — it commits **all** pending state in the session. It was safe today only by luck of ordering (nothing else is pending in `/auth/refresh` before `rotate`), but `rotate` is a shared service any future caller could invoke after other writes, which would then get committed unintentionally on the theft path. Separately, the token-reuse security event was **not audited** at all (violates PF §12 audit expectations).
- **How detected:** the reviewer traced the transaction boundary of `get_session` (rolls back on the raised 401) against the explicit `commit()`, and cross-checked the audit requirement in PF §12/§14.5.
- **Root cause:** need to persist a security side effect (revocation) through an exception that the request-session's error handling rolls back — solved with a broad `commit()` instead of an isolated one.
- **Fix:** the theft branch now calls a new `_revoke_and_audit_reuse(user_id)` helper that opens a **fresh isolated `SessionLocal()`**, revokes all the user's active refresh tokens, writes an `auth.token_reuse_detected` audit row (`after={"reason":"refresh_token_reuse","revoked_count":n}`), commits, and closes — then raises `AuthenticationError`. The request session is never committed. Only the revocation + its audit persist, regardless of any other pending state.
- **Verified:** login → refresh(rotate) → reuse OLD token → 401 AND reuse NEW token → 401 (chain revoked), exactly as before; and `SELECT ... WHERE action='auth.token_reuse_detected'` now returns the row (proving the isolated transaction persisted through the request rollback).

### M1 — Login was timing-distinguishable (account enumeration via response latency)
- **Severity:** MEDIUM (security — enumeration)
- **File:** `backend/app/platform/auth/service.py` (`AuthService.login`)
- **What was wrong:** for a missing/inactive email, login returned immediately **without** running the Argon2id password verify; for a real email with a wrong password, it ran the full (tens-of-ms) verify. The response body/status were identical (good), but the **latency difference** let an attacker distinguish "no such account" from "account exists" — the exact enumeration PF §4 forbids.
- **How detected:** reviewer noted the early-return branch skips `verify_password` while the real branch performs it, creating a measurable timing side channel.
- **Root cause:** short-circuiting the expensive hash on the "no user" path.
- **Fix:** added a module-level `_DUMMY_HASH = hash_password(...)` computed once, and the reject branches (missing/inactive user, and role-less non-super-admin) now run `verify_password(password, _DUMMY_HASH)` to equalize timing before raising the same generic error.
- **Verified:** `import app.main` OK; 16/16 tests; behavior/body unchanged.

### M2 — Forgot-password was timing- and side-effect-distinguishable + double-hashed the temp
- **Severity:** MEDIUM (security — enumeration + waste)
- **File:** `backend/app/platform/auth/service.py` (`AuthService.forgot_password`)
- **What was wrong:** a real, role-bearing email triggered multiple Argon2id hashes + a DB insert + an email send; unknown/inactive/role-less/malformed emails returned early at near-zero cost. Same generic body, but a large, reliably measurable latency delta re-enabled enumeration (PF §4). Separately, the real branch hashed the temp password **twice** (once for `password_resets.temp_password_hash`, once for `users.password_hash`), producing two different salts for the same value — wasteful.
- **How detected:** reviewer compared the cost of the real vs no-op branches.
- **Fix:** (a) added `_equalize_forgot_timing()` (one dummy `hash_password`) called on all no-op branches to blunt the timing signal; (b) the real branch computes the temp hash **once** and reuses the digest for both fields. Response/message unchanged (generic, no enumeration).
- **Verified:** unchanged generic response; 16/16 tests.
- **Note:** a fuller hardening (move the email send off the request path to the worker for constant-time response) is deferred — see M2-followup under Deferred.

### M5 — No-op `module.toggled` audit rows polluted the append-only log
- **Severity:** MEDIUM (audit integrity)
- **File:** `backend/app/platform/societies/service.py` (`SocietyService.set_modules`, update branch)
- **What was wrong:** re-submitting an identical module allocation (same `enabled`, same `config`) still wrote a `module.toggled` audit row with `before == after` — noise in an append-only, accountability-critical log (BDB §7).
- **How detected:** reviewer compared `set_modules` (always audited) to `update_society` (correctly skips unchanged fields).
- **Fix:** the update branch now skips the write **and** the audit when `row.enabled == alloc.enabled and row.config == alloc.config`, while keeping the running enabled-set coherent. New-row `module.allocated` path unchanged.
- **Verified:** 16/16 tests.

### N1 — Misleading "chain-walk" wording in the token docstring
- **Severity:** NIT (doc accuracy)
- **File:** `backend/app/platform/auth/token_service.py` (module + `rotate` docstrings)
- **What was wrong:** the comment described a "chain-walk … revoke the WHOLE chain," but the code revokes **all of the user's active refresh tokens** (no walk). The behavior is correct and loop-free; the wording was misleading (and could imply an unbounded traversal that doesn't exist).
- **Fix:** reworded the docstrings to accurately state it revokes all the user's active refresh tokens on reuse (theft response).

### N2 — Dead import
- **Severity:** NIT
- **File:** `backend/app/platform/users/provisioning.py`
- **What was wrong:** `ValidationError` was imported but never raised.
- **Fix:** removed the unused import.

### L4 — Super-admin seed password was not policy-checked
- **Severity:** LOW (consistency/hardening)
- **File:** `backend/app/cli/seed.py` (`seed_super_admin`)
- **What was wrong:** society default passwords are policy-checked (`societies/service.py`), but the env-provided `SUPERADMIN_PASSWORD` was hashed with no `validate_password_policy` call — a weak bootstrap password would be silently accepted.
- **Fix:** `seed_super_admin` now calls `validate_password_policy(settings.superadmin_password)` before hashing (only when a password is set — the "skip if unset" behavior is preserved). The `.env.template` default satisfies the policy, so seeding still works out of the box.

### H2 — A society could be left with zero admins with no visible trace (warn-but-allow)
- **Severity:** HIGH (operational/policy) — **user-decided outcome**
- **Files:** `backend/app/platform/users/provisioning.py` (`remove_role`, `deactivate_user` + `_warn_if_admin_emptied`), `backend/app/platform/users/repository.py` (`count_role_holders`, `admin_society_ids`)
- **What was wrong:** a super-admin could remove/deactivate the **last `society_admin`** of a society, leaving it leaderless with no in-app recovery path, and nothing recorded the event. PF is silent on this.
- **Decision (user):** **warn-but-allow** — do not hard-block; instead record a distinct audit event so the emptied-admin state is visible in the trail (super-admin can still re-provision / hand over later).
- **Fix:** after a removal/deactivation is flushed, `_warn_if_admin_emptied` checks the count of remaining **active** `society_admin` holders; if zero, it writes a `society.admin_emptied` audit row. `deactivate_user` captures the user's admin societies **before** deactivation so the post-change count is accurate.
- **Verified:** e2e — creating a society, provisioning a sole admin, then removing that admin role fires exactly one `society.admin_emptied` row; test data rolled back.

---

## Deferred (acknowledged, not blocking the foundation)

These were flagged as valid but are policy/altitude/perf improvements, not correctness or security defects. Logged here so they are not lost.

| ID | Severity | File | Item | Why deferred |
|----|----------|------|------|--------------|
| H1 | HIGH→MED | auth/token_service.py | `must_change` users can keep refreshing indefinitely (no upper bound). | Not an auth bypass — the access token is still gated on every other endpoint by the central `must_change` lockout; `/auth/refresh` must stay reachable so the client can get to change-password. Documented; may add a bound later. |
| M2-followup | MED | auth/service.py | Move forgot-password email/heavy work to the worker for constant-time response. | The dummy-hash equalization (M2) blunts the dominant signal; full constant-time needs the async/worker path. Revisit when the worker grows. |
| M3 | MED | auth/repository.py | Multi-society defensive `min(society_id)` tie-break for token `role_ids`. | The `role_ids` JWT claim is informational only — `require_permission` re-derives permissions from the DB every request, so a stale claim can never grant access. One-society-per-user is enforced on all link paths. |
| M4 | MED | users/me_service.py | `/me` issues 3 queries (portals + modules + permissions). | Not a true N+1 (no per-row loop); a consolidation is a perf nicety. |
| M6 | MED | societies/service.py | `settings`/`config` JSONB is full-replace (no deep-merge); in-place mutation wouldn't be tracked. | Current code reassigns (tracked correctly); no code mutates in place. Behavioral note for future PATCH semantics. |
| L1 | LOW | auth/schemas.py | `LoginRequest` fields lack `min_length`. | Empty password harmlessly fails verify; cosmetic inconsistency. |
| L2 | LOW | auth/service.py | change-password only checks "differs from current," not password history. | In the must-change flow the current hash IS the temp/default, so reuse of the temp is correctly rejected; history checks aren't required by PF §4. |
| L3 | LOW | core/deps.py | `must_change` allowlist matches exact `request.url.path`. | Resolves correctly today; would only break under a future mount-prefix change. |
| L5 | LOW | auth/router.py | client IP ignores `X-Forwarded-For`. | Only affects the optional forensic `ip` field, never a security decision. |
| N3–N5 | NIT | roles/service.py, users/router.py, models.py | style/oversize-column/`from_attributes` nits. | Cosmetic; `refresh_tokens.token_hash` is oversized (String(128) vs 64-char SHA-256) but harmless. |

---

## Positive findings (verified correct — no change needed)

The reviewer explicitly confirmed these hold, which is worth recording:
- **Layer separation** genuinely respected (routers thin, services own logic, repos own queries).
- **No raw string SQL anywhere** — all ORM/Core `select()`; SQL-injection-safe.
- **JWT decode always pins `algorithms=[...]`** (never `none`) — closes algorithm confusion.
- **Password hashes never leave the API** — `UserOut`/`SocietyOut`/`MeUser` omit them; `default_member_password_hash` never surfaces.
- **Only refresh-token hashes** (SHA-256) are stored/compared, never raw tokens.
- **Audit rows written in the same transaction** as the change everywhere (except the now-fixed isolated theft path).
- **Tenant scoping** — every society-scoped query filters `society_id`; writes stamp it (matches the P7 audit).
- **Roles-by-copy** idempotent, copies permissions + visibility, excludes `super_admin`; **one-society-per-user** enforced on all link/assign paths; **must-change lockout** centralized with no feature-route bypass.
