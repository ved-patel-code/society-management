"""Complaints module (docs/modules/complaints.md).

The fifth toggleable feature module: a resident (owner) raises a house-scoped
maintenance/issue complaint (title + description + category + optional report
photos), tracks its status; the society_admin sees all complaints, drives the
status workflow (open → in_progress → resolved → closed → archived), attaches a
solution note + optional proof photos when resolving, and manages the category
list. Complaint photos are filed into the Vault. A worker auto-archives closed
complaints after a configurable number of days.

``depends_on: houses`` (needs the house registry + owner occupancy). Images also
require the ``vault`` module at the route level. Emits domain events
(``complaint.created`` / ``complaint.withdrawn`` / ``complaint.status_changed``)
to the in-process dispatcher (``app.common.events``); Notifications subscribes
when built.
"""
