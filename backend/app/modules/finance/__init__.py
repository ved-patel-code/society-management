"""Finance module (docs/modules/finance.md).

A toggleable module (``depends_on: houses``) that runs a society's money:
effective-dated maintenance rate, materialized monthly dues, payment collection
(incl. prepaid blocks), expenses/income, a computed reserve ledger, and full
analytics. See ``spec.py`` for the ``ModuleSpec`` and ``api.py`` for the public
inter-module contract other modules import.
"""
