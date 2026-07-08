"""Shared Complaints service internals (docs/modules/complaints.md §3/§4/§8).

Small, focused helpers every concern reuses so logic lives in ONE place
(docs/03 §1). Implemented in the frozen core; the wave services call these
read/helper functions and never reimplement them:

- ``load_config`` / ``write_config`` — the validated per-society complaints config
  (``society_modules.config``); ``write_config`` is a PARTIAL MERGE (missing keys
  keep their current value) — no finance precedent, defined here (user decision).
- ``ensure_default_categories`` — idempotent lazy seed of the 6 system categories
  on first use (mirrors finance's ``ensure_default_categories`` — no edit to the
  shared platform enable flow; a documented deviation from docs §3/§8's
  "on enable").
- ``record_transition`` — THE single status-history write choke-point. Every
  status change (admin, resident withdraw, worker archive, and the initial create)
  goes through here so the timeline write + timestamp stamping is uniform even
  though AUTHORIZATION differs by actor. This is where the transition table is
  enforced and where ``resolved_at``/``closed_at``/``archived_at``/``withdrawn_at``
  are set (and ``resolved_at`` cleared on reopen).
- ``current_owned_houses`` / ``house_display_code`` / ``house_exists`` — reach
  House & Occupancy via its service interface, never its tables (docs/05).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import ConflictError, ValidationError
from app.common.time import utcnow
from app.modules.complaints.models import (
    Complaint,
    ComplaintCategory,
    ComplaintStatusHistory,
)
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    ALLOWED_TRANSITIONS,
    COMPLAINT_STATUSES,
    DEFAULT_CATEGORIES,
    STATUS_ARCHIVED,
    STATUS_CLOSED,
    STATUS_IN_PROGRESS,
    STATUS_RESOLVED,
    STATUS_WITHDRAWN,
    ComplaintsConfig,
)
from app.platform.models import SocietyModule

MODULE_KEY = "complaints"

# The config keys this module owns in ``society_modules.config`` (whitelist for
# both read and partial-merge write, so unrelated config is never touched).
_CONFIG_KEYS = ("auto_archive_days", "max_report_images", "max_proof_images")


# --- config ------------------------------------------------------------------


def _society_module(
    session: Session, society_id: int
) -> SocietyModule | None:
    return session.execute(
        select(SocietyModule).where(
            SocietyModule.society_id == society_id,
            SocietyModule.module_key == MODULE_KEY,
        )
    ).scalar_one_or_none()


def load_config(session: Session, society_id: int) -> ComplaintsConfig:
    """The validated complaints config for a society (docs §8).

    Reads ``society_modules.config`` for the complaints module and validates it
    through :class:`ComplaintsConfig` (falling back to defaults for missing keys).
    Only the module's own keys are pulled; unrelated config is ignored.
    """
    module = _society_module(session, society_id)
    raw = (module.config or {}) if module is not None else {}
    data = {k: raw[k] for k in _CONFIG_KEYS if k in raw}
    return ComplaintsConfig(**data)


def write_config(
    session: Session, society_id: int, changes: dict[str, int]
) -> ComplaintsConfig:
    """PARTIAL-MERGE the given keys into the society's complaints config (docs §8).

    Only keys present in ``changes`` (already validated by the request schema and
    whitelisted here) are written; every other key in ``society_modules.config``
    (this module's untouched keys AND any other module's — though config is
    per-module) is preserved. Returns the resulting validated config. Raises if
    the complaints module row is absent (module not enabled) — the route is
    module-gated, so this is a defensive guard.
    """
    module = _society_module(session, society_id)
    if module is None:
        raise ValidationError(
            "Complaints module is not enabled for this society.",
            details={"module_key": MODULE_KEY},
        )
    merged = dict(module.config or {})
    for key in _CONFIG_KEYS:
        if key in changes and changes[key] is not None:
            merged[key] = changes[key]
    # Validate the merged result before persisting (defence in depth).
    validated = ComplaintsConfig(
        **{k: merged[k] for k in _CONFIG_KEYS if k in merged}
    )
    # Reassign (not in-place mutate) so SQLAlchemy tracks the JSON change.
    module.config = merged
    session.add(module)
    session.flush()
    return validated


# --- categories (lazy seed) --------------------------------------------------


def ensure_default_categories(
    session: Session, society_id: int, repo: ComplaintRepository
) -> None:
    """Idempotently seed the 6 system categories for a society (docs §3).

    Called on first use of the categories feature (mirrors finance's lazy
    ``ensure_default_categories`` + Vault's lazy folder creation) so no edit to the
    platform enable flow is needed. Grant-only: never removes admin-added or
    renamed categories, and skips any name already present (guards a concurrent
    seeder).
    """
    if repo.count_categories(society_id) > 0:
        return
    existing = {c.name for c in repo.list_categories(society_id, active_only=False)}
    for name in DEFAULT_CATEGORIES:
        if name not in existing:
            repo.add_category(
                ComplaintCategory(
                    society_id=society_id,
                    name=name,
                    is_active=True,
                    is_system=True,
                    created_by=None,
                )
            )


# --- status transitions (the single write choke-point) -----------------------

# Which timestamp column each terminal-ish target stamps on entry (docs §4).
_ENTRY_TIMESTAMP = {
    STATUS_RESOLVED: "resolved_at",
    STATUS_CLOSED: "closed_at",
    STATUS_ARCHIVED: "archived_at",
    STATUS_WITHDRAWN: "withdrawn_at",
}


def assert_transition_allowed(from_status: str, to_status: str) -> None:
    """Raise 409 if ``from_status -> to_status`` is not a legal edge (docs §3).

    The legal set is :data:`ALLOWED_TRANSITIONS` (actor-independent). Callers add
    their OWN actor authorization on top (admin/resident/worker); this guards the
    edge itself so no service can invent an illegal transition.
    """
    if to_status not in COMPLAINT_STATUSES:
        raise ValidationError(
            "Unknown target status.", details={"to_status": to_status}
        )
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
        raise ConflictError(
            f"Cannot move a complaint from '{from_status}' to '{to_status}'.",
            details={"from_status": from_status, "to_status": to_status},
        )


def record_transition(
    repo: ComplaintRepository,
    complaint: Complaint,
    *,
    to_status: str,
    note: str | None,
    changed_by: int | None,
    at: datetime | None = None,
) -> ComplaintStatusHistory:
    """Apply a status change to ``complaint`` and append its timeline row (docs §4).

    THE single place a complaint's status/timestamps change:
    - stamps the entry timestamp for the target (resolved/closed/archived/
      withdrawn), and CLEARS ``resolved_at`` on a reopen (resolved -> in_progress);
    - sets ``complaint.status``;
    - writes one ``complaint_status_history`` row (``from_status`` = the prior
      status, ``changed_by`` = actor or None for the worker).

    Transition legality (:func:`assert_transition_allowed`) plus per-actor
    authorization are the CALLER's responsibility — this helper is the write, not
    the gate, so the two never drift. Used for real transitions; the initial
    ``NULL -> open`` create row is written via :func:`record_initial`.
    """
    when = at or utcnow()
    from_status = complaint.status

    # Entry-timestamp effects (docs §4).
    column = _ENTRY_TIMESTAMP.get(to_status)
    if column is not None:
        setattr(complaint, column, when)
    # Reopen clears the resolved marker (docs §4: resolved -> in_progress).
    if to_status == STATUS_IN_PROGRESS and from_status == STATUS_RESOLVED:
        complaint.resolved_at = None

    complaint.status = to_status

    return repo.add_status_history(
        ComplaintStatusHistory(
            society_id=complaint.society_id,
            complaint_id=complaint.id,
            from_status=from_status,
            to_status=to_status,
            note=note,
            changed_by=changed_by,
        )
    )


def record_initial(
    repo: ComplaintRepository,
    complaint: Complaint,
    *,
    changed_by: int | None,
) -> ComplaintStatusHistory:
    """Write the initial ``NULL -> open`` timeline row on create (docs §4)."""
    return repo.add_status_history(
        ComplaintStatusHistory(
            society_id=complaint.society_id,
            complaint_id=complaint.id,
            from_status=None,
            to_status=complaint.status,
            note=None,
            changed_by=changed_by,
        )
    )
