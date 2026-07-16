---
name: implementation-workflow
description: "How the Society backend is being implemented — build order, agent fan-out, QA gates, git/PR"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1f571a20-3780-4435-b9c7-82794e0ab22f
---

Implementation phase started 2026-07-05 (design was already complete). Building **backend-first, Module 0 = Platform Foundation** first.

**Repo:** github.com/ved-patel-code/society-management (now public). Local `git init` done; code in `d:\society\backend\`. Remote reached via git credential-manager; `gh` CLI 2.96.0 installed via winget but needs a user-provided PAT (`gh auth login --with-token`) to open PRs.

**Build strategy (the user's explicit process):**
1. **Lead builds the shared core FIRST** (frozen foundation everything imports): docker stack, pinned+CVE-vetted deps, `core/` glue, all 11 models, single Alembic migration, seed CLI, minimal AuditService, worker, app factory + pre-stubbed routers. Must pass a green gate (compose healthy, migrate, seed idempotent, /health 200, smoke tests) BEFORE fan-out.
2. **Parallel sub-agents** build feature packages against the frozen core, in dependency **waves**, on ONE integration branch `feat/foundation-features` (not isolated worktrees — interface coupling made a single branch + explicit per-wave contract files the reliable choice). Wave 1 = roles(P2)+societies(P3); Wave 2 = auth(P4)+users/provisioning(P5); Wave 3 = /me(P6)+tenant-gate wiring(P7). Each agent: reads a scratchpad contract file with EXACT interface signatures, edits only its own `platform/<feature>/` folder, verifies `import app.main` + pytest + a real Docker e2e before reporting.
3. **Code-review gate** (one expert agent, fix until clean), THEN **test gate** (multiple agents: happy/bad/exception/edge/security-with-&-without-roles/vulnerability + e2e) — all in Docker.

**Confirmed tech choices this phase:** sync SQLAlchemy 2.x + psycopg v3; PyJWT + HS256 (pinned **PyJWT==2.13.0** — fixes 2026 alg-confusion CVEs); APScheduler worker; access 15m / refresh 14d (env-overridable); Argon2id via passlib+argon2-cffi.

**Key impl decisions made during build (not in design docs):**
- super_admin authority = the `is_platform_super_admin` boolean flag ONLY; NO society-scoped user_roles row for it. Login allows a super_admin with zero user_roles (active_society_id=None, portals=['platform']) — otherwise the "reject if no user_roles" rule would lock them out.
- Foundation registers a `platform` ModuleSpec with empty permissions (platform ops gate on the flag, not permission rows) → `permissions` table is legitimately empty after seed.
- `core/deps.py` enforces the must-change lockout centrally (all endpoints except /auth/change-password) so features don't repeat it.
- All foundation models live in one `app/platform/models.py` (frozen schema; feature agents add NO tables).

**Vet rule:** any NEW dependency gets a web CVE/supply-chain search before install (agents may search without asking).

See [[tech-stack]] [[modularity-model]] [[docs-structure]] [[dual-role-portals]].
