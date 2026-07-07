# House & Occupancy — Code Review Findings

Code-review gate (Phase 2) for Module 2. An independent reviewer (Opus 4.8,
read-only) audited the `main...feat/house-occupancy` diff against
[../../modules/house-occupancy.md](../../modules/house-occupancy.md) and the
established layering / tenant-scoping / audit conventions.

**Outcome:** one Must-fix (data loss) — **fixed**; the rest are should-fix/nits
with dispositions below. The state machine, close-before-insert ordering,
tenant-scoping, gating, foundation deviations, and migration were all verified
correct.

## Must-fix (applied)

**1. Owner/tenant ID-proof silently wiped on same-email status changes / in-place edits.**
`app/modules/houses/service.py` — `_reconcile_owner` same-email path and
`_reconcile_tenant` in-place edit path assigned `id_proof_type` /
`id_proof_document_id` directly from the payload, which defaults to `None`.

Failure scenario: an `owned` house with a stored owner ID proof, moved to
`to_let` with a `{name,email,phone}` payload, had its retained ID proof destroyed
— contradicting the spec (§0/§4: "owner data incl. ID proof retained across
statuses + editable").

**Fix:** ID-proof fields now use carry-over semantics — overwritten only when the
payload supplies a non-None value — in both same-email owner update and tenant
in-place edit. `persons_living` is still taken as-is (required for owned/rented,
intentionally cleared to None for to_let/for_sale per §3 decision 3). The
edit-driven replacement path (`_merged_owner_payload`) and `edit_occupancy`'s
plain-edit path already carried over correctly and were unchanged.

## Should-fix (dispositions)

**2. Route gate evaluated twice (decorator `dependencies=` + signature param).**
*Kept as-is.* This is the exact pattern Onboarding's router uses
(`dependencies=_MANAGE` + `auth = Depends(require_permission(...))`); the param is
needed to obtain `auth.user_id`. Diverging here would make `houses` inconsistent
with the rest of the codebase. No security impact (both checks must pass).

**3. No module tests yet.** *Expected* — this is the next phase (Phase 3 test
gate). Covered there.

**4. `list_houses` point-loads a `Building` per row (mild N+1).** *Acceptable for
v1.* `session.get` hits the identity map, so it is bounded by distinct buildings
per page (≤ page size). Logged for a future batch-fetch optimization.

## Nits (no action)

- **5.** `_validate_transition` returns 409 for `→empty` and 422 for an unknown
  target — two codes for two flavors of "not allowed"; consistent and defensible.
- **6.** `HouseStatusHistory.changed_at` is DB-defaulted and not refreshed after
  flush — harmless (`get_history` re-queries); noted for future in-session reads.

## Verified correct

- Close-before-insert ordering for the `uq_house_occupancy_current` partial unique
  (no path creates two current rows for the same house/party).
- `first_left_empty_on` once-only; never set on non-empty→non-empty; never cleared.
- Tenant lifecycle: open on entering rented, edit in place on rented→rented, close
  on leaving rented; `user_id` stays NULL throughout.
- Required-field validation runs before any mutation/provisioning (no half-provision).
- Owner identity by normalized email; cross-society link raises ConflictError and
  the (never-committing) service rolls back atomically — no partial state.
- Tenant/IDOR scoping: all reads + both writes scoped by `society_id`; society only
  from `TenantContext`.
- All 5 routes gate `require_module('houses')` + the correct permission.
- `revoke_house_access` deactivates only when no roles AND no current occupancy;
  an owner keeping access across owned→rented never triggers it.
- Onboarding delete guard blocks on current occupancy, allows when empty; lazy
  imports avoid a backward platform/onboarding→houses load-time dependency.
- Audit completeness across status_changed / occupancy_created / occupancy_updated
  / owner_replaced / access_revoked; same-status POST writes no spurious history.
- Migration 0003 chain, nullable no-FK `id_proof_document_id`, indexes, and
  downgrade all correct.
