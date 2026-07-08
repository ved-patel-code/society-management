"""Reserve ledger concern (docs/modules/finance.md §4/§6 — Reserve).

Computed running balance (Σ inflow − Σ outflow), manual dated entries
(opening/deposit/interest/resale/income/adjustment), entry reversal (negating
entry, both visible), and reconcile-to-bank (post an adjustment for the diff).
Reads are implemented; writes are frozen stubs — Wave E implements them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    LedgerEntryOut,
    ReserveEntryCreateRequest,
    ReserveOut,
    ReserveReconcileRequest,
)


class ReserveService:
    def __init__(self, session: Session, repo: FinanceRepository) -> None:
        self._session = session
        self._repo = repo

    # --- reads (implemented in core) ---------------------------------------

    def get_reserve(
        self, society_id: int, *, offset: int, limit: int
    ) -> ReserveOut:
        """Computed balance + paginated ledger (reversals visible) (docs §4/§6)."""
        balance = self._repo.reserve_balance(society_id)
        entries, total = self._repo.list_ledger(
            society_id, offset=offset, limit=limit
        )
        return ReserveOut(
            balance=balance,
            entries=[LedgerEntryOut.model_validate(e) for e in entries],
            total=total,
        )

    def balance(self, society_id: int):
        """The computed reserve balance (Σ inflow − Σ outflow)."""
        return self._repo.reserve_balance(society_id)

    # --- writes (FROZEN — Wave E implements) -------------------------------

    def post_entry(
        self,
        society_id: int,
        req: ReserveEntryCreateRequest,
        *,
        actor_user_id: int,
    ) -> LedgerEntryOut:
        """Post a manual reserve entry (docs §4/§6).

        Wave E: resolve direction (fixed per ``entry_type``; ``adjustment``
        requires an explicit ``req.direction``); post via
        ``support.post_ledger_entry``; audit ``finance.reserve_entry_posted``.
        """
        raise NotImplementedError("Wave E: post_entry")

    def reverse_entry(
        self, society_id: int, entry_id: int, *, actor_user_id: int
    ) -> LedgerEntryOut:
        """Reverse a ledger entry (docs §4 transparency).

        Wave E: reject if the target is already reversed, is itself a reversal, or
        is a system-posted collection/expense entry (those reverse via payment/
        expense void, not here); post a negating entry (opposite direction,
        ``entry_type=reversal``, ``reverses_entry_id``); flag the original
        ``is_reversed=true``; audit ``finance.reserve_entry_reversed``. Both stay
        visible.
        """
        raise NotImplementedError("Wave E: reverse_entry")

    def reconcile(
        self,
        society_id: int,
        req: ReserveReconcileRequest,
        *,
        actor_user_id: int,
    ) -> LedgerEntryOut:
        """Reconcile-to-bank (docs §4).

        Wave E: compute ``diff = actual_balance − current computed balance``; if
        non-zero post an ``adjustment`` entry (inflow if positive, outflow if
        negative) for ``abs(diff)``; audit ``finance.reserve_reconciled``. A zero
        diff is a no-op (nothing posted).
        """
        raise NotImplementedError("Wave E: reconcile")
