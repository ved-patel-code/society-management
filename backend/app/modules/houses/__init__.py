"""House & Occupancy module (Module 2) — docs/modules/house-occupancy.md.

Manages each house's occupancy lifecycle after onboarding: status transitions
(empty → owned/rented/to_let/for_sale, never back to empty), owner/tenant capture
+ edit, auto-provisioned owner logins, and status filters. Writes the
``status`` / ``first_left_empty_on`` columns of the shared ``houses`` rows that
Onboarding created, and owns the ``house_occupancies`` + ``house_status_history``
tables.

Flat package layout (mirrors ``modules/onboarding``): models · spec · schemas ·
repository · service · router.
"""
