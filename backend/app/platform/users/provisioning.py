"""UserProvisioningService — create/link accounts + manage access (docs/PF §8).

The brain of the users package. Backs the super-admin user endpoints and is the
interface later consumed by the House & Occupancy and Elections modules to
auto-provision owners, hand over admin, and revoke access (docs/PF §8/§14.8).

Key rules encoded here (docs/PF §5.1/§8):
- New email  → create a ``User`` with the SOCIETY's default member password hash
  (already Argon2id — copied verbatim from the ``societies`` row) + password_state
  ``must_change``; then add the ``user_role``.
- Existing email → NO duplicate login: add the role to the existing account. This
  is how one person becomes admin AND resident (dual-role, §5.1).
- ONE SOCIETY PER USER (v1): linking an email that already holds roles in a
  DIFFERENT society is a conflict (docs/PF §8/§9).
- Removing a role / deactivating / revoking house access revokes refresh tokens
  via P4's ``TokenService`` so an old email can't get back in (docs/PF §4/§14.8).

Every state change is audited in the SAME session; the service NEVER commits
(``get_session`` commits once at request end — docs/PF §12). Passwords are only
ever handled as hashes here — no plaintext, never logged.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError
from app.platform.audit.service import AuditService
from app.platform.auth.token_service import TokenService
from app.platform.bootstrap import SOCIETY_ADMIN
from app.platform.models import User, UserRole
from app.platform.users.repository import UserRepository


class UserProvisioningService:
    """Account provisioning + access lifecycle (see the wave-2 contract)."""

    # The role whose emptying leaves a society leaderless (warn-but-allow).
    _ADMIN_ROLE_KEY = SOCIETY_ADMIN.key

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = UserRepository(session)
        self._audit = AuditService(session)

    # --- create / link -----------------------------------------------------

    def create_or_link_user(
        self,
        *,
        email: str,
        society_id: int,
        role_key: str,
        profile: dict[str, Any],
        actor_user_id: int | None,
    ) -> User:
        """Create a new account or link a role onto an existing one (docs/PF §8/§5.1).

        NEW email → create the user with the society's default member password hash
        (Argon2id, copied from the ``societies`` row) + ``must_change``, then attach
        the ``(society_id, role_key)`` role. EXISTING email → add the role to the
        existing account (no duplicate login), enforcing one-society-per-user.
        Audits ``user.created`` (new only) and ``role.assigned``.
        """
        society = self._repo.get_society(society_id)
        if society is None:
            raise NotFoundError(
                "Society not found.", details={"society_id": society_id}
            )

        role = self._repo.society_role_by_key(society_id, role_key)
        if role is None:
            raise NotFoundError(
                f"Role '{role_key}' does not exist for this society.",
                details={"society_id": society_id, "role_key": role_key},
            )

        user = self._repo.find_by_email(email)
        if user is None:
            user = self._create_user(email, society, profile, actor_user_id)
        else:
            # Existing login — enforce one-society-per-user (v1) before linking.
            other_societies = self._repo.user_society_ids(user.id) - {society_id}
            if other_societies:
                raise ConflictError(
                    "This email already belongs to another society.",
                    details={
                        "email": email,
                        "society_id": society_id,
                        "existing_society_ids": sorted(other_societies),
                    },
                )

        self._attach_role(
            user, society_id=society_id, role_id=role.id, actor_user_id=actor_user_id
        )
        return user

    # --- role assignment ---------------------------------------------------

    def assign_role(
        self,
        *,
        user_id: int,
        society_id: int,
        role_key: str,
        actor_user_id: int | None,
    ) -> None:
        """Add a role to an existing user, respecting one-society-per-user (§8).

        Audits ``role.assigned``. Idempotent: re-assigning a held role is a no-op.
        """
        user = self._require_user(user_id)

        role = self._repo.society_role_by_key(society_id, role_key)
        if role is None:
            raise NotFoundError(
                f"Role '{role_key}' does not exist for this society.",
                details={"society_id": society_id, "role_key": role_key},
            )

        other_societies = self._repo.user_society_ids(user.id) - {society_id}
        if other_societies:
            raise ConflictError(
                "This user already belongs to another society.",
                details={
                    "user_id": user_id,
                    "society_id": society_id,
                    "existing_society_ids": sorted(other_societies),
                },
            )

        self._attach_role(
            user, society_id=society_id, role_id=role.id, actor_user_id=actor_user_id
        )

    def remove_role(
        self,
        *,
        user_id: int,
        society_id: int,
        role_key: str,
        actor_user_id: int | None,
    ) -> None:
        """Remove a user's role, then revoke all their refresh tokens (docs/PF §4).

        Revoking tokens forces the effective-permission set to be recomputed on the
        next login so a dropped role cannot linger in a live session. Audits
        ``role.removed``.
        """
        user = self._require_user(user_id)

        role = self._repo.society_role_by_key(society_id, role_key)
        if role is None:
            raise NotFoundError(
                f"Role '{role_key}' does not exist for this society.",
                details={"society_id": society_id, "role_key": role_key},
            )

        user_role = self._repo.get_user_role(user.id, society_id, role.id)
        if user_role is None:
            raise NotFoundError(
                "This user does not hold that role.",
                details={
                    "user_id": user_id,
                    "society_id": society_id,
                    "role_key": role_key,
                },
            )

        self._repo.delete_user_role(user_role)
        self._session.flush()
        TokenService(self._session).revoke_all_for_user(user.id)

        self._audit.record(
            action="role.removed",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="user",
            entity_id=user.id,
            before={"role_key": role_key, "role_id": role.id},
        )

        # Warn-but-allow: if this drops the society's last active admin, record a
        # distinct audit event so the emptied-admin state is visible in the trail
        # (docs/PF — no hard block; super-admin can re-provision / hand over).
        self._warn_if_admin_emptied(society_id, role_key, actor_user_id)

    # --- deactivation ------------------------------------------------------

    def deactivate_user(self, *, user_id: int, actor_user_id: int | None) -> None:
        """Disable an account and revoke all its refresh tokens (docs/PF §4).

        Idempotent: deactivating an already-inactive user still revokes tokens
        (defence-in-depth) but the audit before/after reflects the real change.
        Audits ``user.deactivated``.
        """
        user = self._require_user(user_id)
        was_active = user.is_active

        # Societies where this (currently active) user is an admin — captured
        # BEFORE deactivation so the emptied-admin check below is accurate.
        admin_societies = (
            self._repo.admin_society_ids(user.id) if was_active else []
        )

        user.is_active = False
        self._session.flush()
        TokenService(self._session).revoke_all_for_user(user.id)

        self._audit.record(
            action="user.deactivated",
            actor_user_id=actor_user_id,
            entity_type="user",
            entity_id=user.id,
            before={"is_active": was_active},
            after={"is_active": False},
        )

        # Warn-but-allow: deactivating the last active admin of any society.
        for society_id in admin_societies:
            self._warn_if_admin_emptied(
                society_id, self._ADMIN_ROLE_KEY, actor_user_id
            )

    # --- house access (SKELETON — docs/PF §8/§14.8) ------------------------

    def revoke_house_access(
        self, *, user_id: int, house_id: int, actor_user_id: int | None
    ) -> None:
        """Revoke a user's access to a house (occupant removal — docs/PF §8/§14.8).

        SKELETON. The ``houses`` / ``house_occupancies`` tables are NOT part of the
        Platform Foundation, so the occupancy unlink cannot be performed yet. What
        IS implemented and correct today:

        - Revoke all the user's refresh tokens so an old email can't get back in.
        - Deactivate the account ONLY if it is now orphaned — i.e. it holds no
          remaining ``user_roles``. A user who still has a role (e.g. an owner who
          keeps their login across owned→rented, §14.8) stays active.

        NOTE: the ``house_occupancies`` unlink wires in when the House & Occupancy
        module is built; ``house_id`` is currently unused beyond the audit trail.
        The signature is per the wave-2 contract so that module composes cleanly.
        Audits ``house.access_revoked``.
        """
        user = self._require_user(user_id)

        # TODO(house-module): delete the house_occupancies row linking
        # (user_id, house_id) once that table exists (docs/PF §3/§14.8).

        TokenService(self._session).revoke_all_for_user(user.id)

        # Orphan = no remaining roles. (Once house_occupancies exists, "orphaned"
        # also requires no remaining occupancy — docs/PF §14.8 item 8.)
        remaining_roles = self._repo.count_user_roles(user.id)
        orphaned = remaining_roles == 0
        deactivated = False
        if orphaned and user.is_active:
            user.is_active = False
            self._session.flush()
            deactivated = True

        self._audit.record(
            action="house.access_revoked",
            actor_user_id=actor_user_id,
            entity_type="user",
            entity_id=user.id,
            before={"house_id": house_id},
            after={
                "tokens_revoked": True,
                "orphaned": orphaned,
                "deactivated": deactivated,
            },
        )

    # --- helpers -----------------------------------------------------------

    def _require_user(self, user_id: int) -> User:
        user = self._repo.get(user_id)
        if user is None:
            raise NotFoundError(
                "User not found.", details={"user_id": user_id}
            )
        return user

    def _warn_if_admin_emptied(
        self, society_id: int, role_key: str, actor_user_id: int | None
    ) -> None:
        """Record ``society.admin_emptied`` when a society loses its last active
        ``society_admin`` (warn-but-allow — docs/PF, reviewed decision H2).

        No hard block: the super-admin remains able to re-provision or hand over
        the admin role. Only fires for the admin role, and only when the emptying
        change (a removal/deactivation) has already been flushed, so the count
        reflects the post-change state.
        """
        if role_key != self._ADMIN_ROLE_KEY:
            return
        if self._repo.count_role_holders(society_id, role_key) == 0:
            self._audit.record(
                action="society.admin_emptied",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="society",
                entity_id=society_id,
                after={"role_key": role_key, "active_holders": 0},
            )

    def _create_user(
        self,
        email: str,
        society: Any,
        profile: dict[str, Any],
        actor_user_id: int | None,
    ) -> User:
        """Create a fresh account seeded with the society's default password hash.

        The hash is copied VERBATIM from the ``societies`` row — it is already an
        Argon2id digest (docs/PF §14.6), so it must never be re-hashed or logged.
        ``password_state`` is ``must_change`` so the member is forced to set their
        own password on first login (docs/PF §4).
        """
        user = self._repo.add(
            User(
                email=email,
                password_hash=society.default_member_password_hash,
                password_state="must_change",
                is_active=True,
                full_name=profile.get("full_name"),
                phone=profile.get("phone"),
            )
        )
        self._audit.record(
            action="user.created",
            actor_user_id=actor_user_id,
            society_id=society.id,
            entity_type="user",
            entity_id=user.id,
            after={
                "email": user.email,
                "full_name": user.full_name,
                "phone": user.phone,
                "password_state": user.password_state,
            },
        )
        return user

    def _attach_role(
        self,
        user: User,
        *,
        society_id: int,
        role_id: int,
        actor_user_id: int | None,
    ) -> None:
        """Attach ``role_id`` to ``user`` in ``society_id`` (idempotent).

        The DB UNIQUE on ``(user_id, society_id, role_id)`` is the concurrency
        safety net; the pre-check keeps re-assignment a clean no-op. Audits
        ``role.assigned`` only when a row is actually created.
        """
        existing = self._repo.get_user_role(user.id, society_id, role_id)
        if existing is not None:
            return

        self._repo.add_user_role(
            UserRole(
                user_id=user.id,
                society_id=society_id,
                role_id=role_id,
                assigned_by=actor_user_id,
            )
        )
        self._audit.record(
            action="role.assigned",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="user",
            entity_id=user.id,
            after={"role_id": role_id},
        )
