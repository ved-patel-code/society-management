"""User + user-role queries for provisioning (docs/PF §8, docs/03 §2/§4).

Pure DB access: the service owns the rules, this layer owns the SQL. Columns are
selected narrowly and lookups are keyed so provisioning stays cheap (docs/03 §4).
No business decisions are made here.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models import Role, Society, User, UserRole


class UserRepository:
    """Queries over ``users``, ``user_roles``, ``roles`` and ``societies``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- users -------------------------------------------------------------

    def get(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def find_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup (``users.email`` is CITEXT — docs/PF §3)."""
        return self._session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

    def add(self, user: User) -> User:
        """Stage a new user and flush so its ``id`` is assigned in-txn."""
        self._session.add(user)
        self._session.flush()
        return user

    # --- societies ---------------------------------------------------------

    def get_society(self, society_id: int) -> Society | None:
        return self._session.get(Society, society_id)

    # --- roles -------------------------------------------------------------

    def society_role_by_key(self, society_id: int, key: str) -> Role | None:
        """The society-scoped role row for ``(society_id, key)`` (docs/PF §5)."""
        return self._session.execute(
            select(Role).where(Role.society_id == society_id, Role.key == key)
        ).scalar_one_or_none()

    # --- user_roles --------------------------------------------------------

    def get_user_role(
        self, user_id: int, society_id: int, role_id: int
    ) -> UserRole | None:
        return self._session.execute(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.society_id == society_id,
                UserRole.role_id == role_id,
            )
        ).scalar_one_or_none()

    def user_society_ids(self, user_id: int) -> set[int]:
        """Distinct societies the user already holds a role in (one-society check)."""
        rows = self._session.execute(
            select(UserRole.society_id)
            .where(UserRole.user_id == user_id)
            .distinct()
        ).all()
        return {r[0] for r in rows}

    def count_user_roles(self, user_id: int) -> int:
        """Number of remaining ``user_roles`` for the user (orphan detection)."""
        rows = self._session.execute(
            select(UserRole.id).where(UserRole.user_id == user_id)
        ).all()
        return len(rows)

    def admin_society_ids(self, user_id: int) -> list[int]:
        """Societies where ``user_id`` holds the ``society_admin`` role.

        Used before deactivating a user to know which societies to re-check for an
        emptied admin set (warn-but-allow). Uses the seeded admin key directly to
        avoid coupling the repository to bootstrap constants at import time.
        """
        rows = self._session.execute(
            select(UserRole.society_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id, Role.key == "society_admin")
            .distinct()
        ).all()
        return [r[0] for r in rows]

    def count_role_holders(self, society_id: int, role_key: str) -> int:
        """How many active users still hold ``role_key`` in ``society_id``.

        Used to detect when a society is about to lose its last admin (docs/PF —
        warn-but-allow). Counts only active users so a deactivated holder does not
        mask an emptied admin set.
        """
        rows = self._session.execute(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                UserRole.society_id == society_id,
                Role.key == role_key,
                User.is_active.is_(True),
            )
            .distinct()
        ).all()
        return len(rows)

    def add_user_role(self, user_role: UserRole) -> UserRole:
        self._session.add(user_role)
        self._session.flush()  # assign PK within the txn (no commit)
        return user_role

    def delete_user_role(self, user_role: UserRole) -> None:
        self._session.delete(user_role)
        self._session.flush()
