"""Notice Board service concerns (docs/modules/notice-board.md §4).

``support`` holds shared frozen internals every concern reuses (sanitize, detail
assembly, transition/expiry guards, owner set, the single ``apply_publish``
choke-point). The other files are DISJOINT per-wave concerns so the parallel
build waves never touch the same file:
- ``notices_crud`` (Wave A) — create / edit / list feed / detail-marks-read.
- ``lifecycle``    (Wave B) — publish / withdraw.
- ``attachments``  (Wave C) — add / remove Vault attachments.
- ``receipts``     (Wave D) — read-all / receipts / archive.
"""
