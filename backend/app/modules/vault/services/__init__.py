"""Vault service internals, split by concern so parallel waves own disjoint files.

- :mod:`folders`   — folder tree (Wave B)
- :mod:`documents` — upload/preview/download/rename/move/soft-delete (Wave C)
- :mod:`trash`     — restore, empty trash, quota accounting (Wave D)
- :mod:`jobs`      — auto-purge + reconcile worker jobs (Wave D)

The public entry point is :class:`app.modules.vault.service.VaultService` (facade)
and :mod:`app.modules.vault.api` (cross-module contract). Read methods are
implemented in the Phase-0 core; write methods raise ``NotImplementedError`` until
their wave lands.
"""
