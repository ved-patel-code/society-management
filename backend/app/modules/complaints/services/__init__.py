"""Complaints service concerns (docs/modules/complaints.md §4).

``support.py`` holds shared internals implemented in the frozen core; the other
modules (categories, complaints_crud, status, images, config_svc, jobs) are the
disjoint per-wave concern files. The ``ComplaintsService`` facade composes them.
"""
