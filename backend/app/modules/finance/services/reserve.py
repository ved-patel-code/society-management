"""Reserve ledger concern (docs/modules/finance.md §4/§6 — Reserve).

Computed running balance (Σ inflow − Σ outflow), manual dated entries
(opening/deposit/interest/resale/income/adjustment), entry reversal (negating
entry, both visible), and reconcile-to-bank (post an adjustment for the diff).
Reads are implemented; writes are frozen stubs — Wave E implements them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    ENTRY_TYPE_DIRECTION,
    LEDGER_DIRECTIONS,
    LEDGER_SOURCE_TYPES,
    LedgerEntryOut,
    ReserveEntryCreateRequest,
    ReserveOut,
    ReserveReconcileRequest,
)
from app.modules.finance.services.support import money, post_ledger_entry
from app.platform.audit.service import AuditService

# System-posted entry types corrected via their own void flow, not here (docs §4).
_SYSTEM_ENTRY_TYPES = frozenset({"collection", "expense"})


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
        # entry_type is schema-validated to be in RESERVE_POSTABLE_ENTRY_TYPES.
        if req.entry_type == "adjustment":
            # Adjustment has no natural direction — the caller must supply one.
            if req.direction is None:
                raise ValidationError(
                    "direction is required for an adjustment entry "
                    "(inflow or outflow)."
                )
            direction = req.direction
        else:
            direction = ENTRY_TYPE_DIRECTION[req.entry_type]

        # Defensive: direction is validated in the schema, but never trust a
        # fixed-type mapping to have produced an unexpected value.
        if direction not in LEDGER_DIRECTIONS:
            raise ValidationError(
                f"direction must be one of {sorted(LEDGER_DIRECTIONS)}."
            )

        # Optional link (e.g. resale_transfer tied to a house).
        if req.source_type is not None and req.source_type not in LEDGER_SOURCE_TYPES:
            raise ValidationError(
                f"source_type must be one of {sorted(LEDGER_SOURCE_TYPES)}."
            )
        # A house link must resolve WITHIN this society — never let a caller store
        # another tenant's house id as a label (tenant isolation, docs/PF §7).
        if req.source_type == "house":
            if req.source_id is None:
                raise ValidationError(
                    "source_id is required when source_type=house."
                )
            from app.modules.houses.service import HouseService

            if not HouseService(self._session).house_exists(
                society_id, req.source_id
            ):
                raise NotFoundError(
                    "Linked house not found in this society.",
                    details={"house_id": req.source_id},
                )

        entry = post_ledger_entry(
            self._repo,
            society_id=society_id,
            entry_type=req.entry_type,
            direction=direction,
            amount=money(req.amount),
            occurred_on=req.occurred_on,
            description=req.description,
            source_type=req.source_type,
            source_id=req.source_id,
            recorded_by=actor_user_id,
        )
        AuditService(self._session).record(
            action="finance.reserve_entry_posted",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="ledger_entry",
            entity_id=entry.id,
            after={
                "entry_type": entry.entry_type,
                "direction": entry.direction,
                "amount": str(entry.amount),
                "occurred_on": entry.occurred_on.isoformat(),
            },
        )
        return LedgerEntryOut.model_validate(entry)

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
        original = self._repo.get_ledger_entry(society_id, entry_id)
        if original is None:
            raise NotFoundError("Ledger entry not found.")

        if original.is_reversed:
            raise ConflictError("This entry has already been reversed.")
        if original.entry_type == "reversal":
            raise ValidationError(
                "A reversal entry cannot itself be reversed."
            )
        if original.entry_type in _SYSTEM_ENTRY_TYPES:
            raise ValidationError(
                f"A system-posted '{original.entry_type}' entry is corrected by "
                "voiding the underlying payment/expense, not via reserve reversal."
            )

        # Negating entry: opposite direction, same amount, sits on the original's
        # date so it nets in reports at the same point in time (docs §4).
        opposite = "outflow" if original.direction == "inflow" else "inflow"
        reversal = post_ledger_entry(
            self._repo,
            society_id=society_id,
            entry_type="reversal",
            direction=opposite,
            amount=money(original.amount),
            occurred_on=original.occurred_on,
            description=f"Reversal of ledger entry #{original.id}",
            source_type=original.source_type,
            source_id=original.source_id,
            recorded_by=actor_user_id,
            reverses_entry_id=original.id,
        )
        original.is_reversed = True
        self._session.flush()

        AuditService(self._session).record(
            action="finance.reserve_entry_reversed",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="ledger_entry",
            entity_id=original.id,
            before={
                "entry_type": original.entry_type,
                "direction": original.direction,
                "amount": str(original.amount),
            },
            after={"reversal_entry_id": reversal.id},
        )
        return LedgerEntryOut.model_validate(reversal)

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
        computed = self._repo.reserve_balance(society_id)
        actual = money(req.actual_balance)
        diff = actual - computed

        # Zero diff: the ledger already matches the bank. The API contract is
        # "an adjustment is posted only when there is a difference", so we do NOT
        # create a phantom zero-amount entry — we surface a clear 422 instead.
        if diff == 0:
            raise ValidationError(
                "Reserve already reconciled; no difference to adjust."
            )

        direction = "inflow" if diff > 0 else "outflow"
        entry = post_ledger_entry(
            self._repo,
            society_id=society_id,
            entry_type="adjustment",
            direction=direction,
            amount=money(abs(diff)),
            occurred_on=req.occurred_on,
            description=req.description or "Reconcile to bank",
            source_type=None,
            source_id=None,
            recorded_by=actor_user_id,
        )
        AuditService(self._session).record(
            action="finance.reserve_reconciled",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="ledger_entry",
            entity_id=entry.id,
            before={"computed": str(computed)},
            after={
                "actual": str(actual),
                # Signed difference plus how it was posted, so the audit fully
                # describes the entry (which stores the unsigned amount + direction).
                "difference": str(diff),
                "direction": direction,
                "amount": str(money(abs(diff))),
            },
        )
        return LedgerEntryOut.model_validate(entry)
