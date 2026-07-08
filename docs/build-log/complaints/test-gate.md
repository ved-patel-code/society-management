# Complaints (Module 5) — Test Gate (Phase 3)

Opus 4.8 designed the matrix; Sonnet 5 implemented + ran it to green. The gate adds
the cross-module, cross-society, adversarial, and regression coverage the per-wave
suites structurally cannot (each wave sees only its own file). No app-code bug
surfaced — the code-review gate had already caught them.

## Result
- **81 gate tests** across 7 files, all green.
- Full complaints suite: **168** (87 per-wave + 81 gate).
- **Full backend suite: 857 passed, 2 skipped** (689 prior + 168 complaints),
  deterministic across repeated clean runs.
- Run: `docker compose exec backend bash scripts/run-tests.sh`.

## Files
- `tests/test_complaints_e2e.py` (8) — full HTTP journeys Foundation→Onboarding→
  Houses→Vault→Complaints: raise (+report image to Vault) → in_progress → resolve
  (note + proof to `Houses/<house>/Complaints/<ref>/`) → close → worker archive;
  asserts the status timeline, ordered audit trail, Vault folder path, image
  counts, emitted domain-event payloads (via a captured subscriber), reopen/
  re-resolve, and a text-only complaint with Vault disabled.
- `tests/test_complaints_enable.py` (7) — enable seeds the 6 perms + role grants;
  `depends_on: houses` enforced; absent/disabled module → 403; Vault-off → image/
  resolve routes 403 while text complaints still work.
- `tests/test_complaints_security.py` (25) — every endpoint with AND without the
  required role: resident forbidden from admin ops (403); no-perm caller (403);
  unauth (401); super-admin bypass; the read-vs-read_all resident scoping; a
  resident can't open/list another house's complaint; crafted cross-society token
  can't act; category CUD needs `manage_categories`.
- `tests/test_complaints_isolation.py` (7) — society A can never see/act on B's
  complaints, categories, or config; sequential-id guess across societies → 404;
  the reference counter is per-society (both start `C-000001`); audit rows scoped.
- `tests/test_complaints_regression.py` (9) — locks in the code-review fixes:
  `date_to` inclusive of the end day; detail/status survive a trashed proof
  document (`preview_url=None`, no crash); report cap → 409 and proof cap → 422
  (with rollback / no orphan); `q` treats `%`/`_` literally; worker window measured
  from a real instant (archives at N days + hours, not-yet-due at N days − 1h).
- `tests/test_complaints_edge.py` (18) — reference serial run (no gaps, zero-pad);
  image ops locked after in_progress; illegal transitions from every state;
  archived/withdrawn terminal; reopen clears `resolved_at`; deactivated category
  hidden from the create list but kept on existing complaints; raise against a
  deactivated category → 422; multi-house raise (422 / 403); config partial-merge;
  proof not add/removable outside resolve.
- `tests/test_complaints_robustness.py` (7) — Vault 413 (quota) / 415 (denied type)
  surface from report-add AND resolve without leaving an orphan row or corrupting
  state (transaction rolls back; status unchanged); malformed create → 422;
  nonexistent ids → 404; double-withdraw / double-resolve / double-close → 409.

## Helpers added (tests/_complaints_helpers.py, append-only)
`raise_complaint`, `add_report_image_http`, `resolve_http`, `trash_vault_document`,
`society_with_tiny_quota` (forces Vault 413), `event_capture` fixture (captures
`complaint.*` domain events), `crafted_bearer` (cross-society JWT via `make_token`),
`PNG_BYTES` / `EXE_BYTES`. No existing signature changed.

## Matrix adjustments (documented, not app bugs)
1. **Absent-module + super-admin** — `require_module` deliberately bypasses for a
   platform super-admin (documented in `core/deps.py`), so the "even super-admin →
   403" line was corrected to assert the by-design bypass (200) plus the ordinary
   403 for society callers.
2. **Reopen proof cap is per-call, not cumulative** — `StatusService.resolve` caps
   the images in the current call (matching its docstring), not the running total
   across a reopen; the e2e test asserts the real per-call semantics.
3. **Audit-sequence filtering** — the e2e audit assertion filters by
   `entity_type='complaint'` (the onboarding `society.created` row shares
   `entity_id=1`).
