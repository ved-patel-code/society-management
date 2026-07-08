"""Finance service concerns (docs/modules/finance.md §4).

Split by feature so the parallel build waves own disjoint files:
``rates``, ``dues``, ``collection`` (payments + prepaid), ``expenses``,
``reserve``, ``analytics``, ``jobs`` (worker). The public :class:`FinanceService`
facade (``finance/service.py``) wires them together over one request session.
Shared internals (config, default-category seeding, ledger posting) live in
``support.py``.
"""
