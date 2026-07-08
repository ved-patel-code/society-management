"""Notice Board module (Module 6) — docs/modules/notice-board.md.

A society-wide broadcast board: ``society_admin`` composes a rich-text notice
(+ Vault attachments) and publishes it to all owners at once; residents read the
active feed (their portal landing page); admins see read receipts + an archive.

Lifecycle ``draft → published → withdrawn``; edit-after-publish, pin, and an
optional query-time expiry. Notices are society-scoped (no ``house_id``). The
package mirrors the Complaints split (models / schemas / spec / repository /
router / service + a ``services/`` subpackage of disjoint concerns) so the
parallel build waves own non-overlapping files.
"""
