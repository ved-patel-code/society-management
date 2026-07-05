# Module Design Template

> Foundation doc 4 of 5. The fixed structure every per-module doc follows.
> Companion docs: [01-project-overview](01-project-overview.md) · [02-architecture](02-architecture.md) · [03-backend-and-db-principles](03-backend-and-db-principles.md) · [05-cross-module-contracts](05-cross-module-contracts.md)

## Design docs (`docs/modules/<module>.md`)
Written **before** building a module. Every design doc uses this structure so all modules are consistent and reviewable:

```
# <Module> — Design

## 1. Purpose & scope
What it does; what it explicitly does NOT do (now / future).

## 2. Audience & permissions
Which roles use it; the permission keys it defines.

## 3. Entities (data model)
Tables: columns, types, PK/FK/UNIQUE/NOT NULL, indexes, and WHY each index exists.
Enums and their allowed transitions.

## 4. Business rules
Every rule the service layer enforces (validation, workflows, edge cases, what is rejected).

## 5. Audited actions
Every state-changing action this module writes to `audit_log`: the `action` key + what
before/after captures. (Auditing is a first-class requirement — see docs/03 §7.)

## 6. Endpoints
Method + path, purpose, request/response shape, permission + module gates,
and the queries each makes (to keep them efficient / no N+1).

## 7. Inter-module contracts
What this module needs from others / provides to others (interfaces, not table access).
Skeleton-then-wire notes for deps not yet built.

## 8. Feature flag / config
What society_modules.config holds for this module.

## 9. Background jobs (if any)
Worker tasks, schedules, idempotency/dedupe.

## 10. Open questions / future enhancements
```

## As-built docs (`docs/implemented/<module>.md`)
Written/updated **during & after** building. This is a **lean navigation index that points to the code — NOT a copy of it.** Its only job: let a reader (human or LLM) find the right file+function fast, with few tokens, without re-reading the whole module.

**It does NOT contain** actual code, SQL/queries, or endpoint request/response schemas — that would just duplicate the code and go stale. To read real code, open the file the index points to. To see the API surface, open the module's `router.py`.

Structure:
```
# <Module> — As-Built Index

## File map
One line per file: path — what lives there.
  e.g. structure/service.py   — house generation + status-transition logic
       structure/repository.py — house / floor / building queries

## Functions
Per function: name — one-line summary. deps: what it calls / tables / modules it uses. @ file path.
  e.g.
  - generate_building_houses — makes all house rows for a building per the chosen
    numbering mode. deps: create_house, common/numbering.py, houses table.
    @ modules/onboarding/structure/service.py
  - create_house — inserts one house row (status=empty). deps: houses table.
    @ modules/onboarding/structure/service.py

## Tables owned
Table names this module owns (columns/DDL live in migrations + the design doc, not here).

## Cross-module wiring
Interfaces this module provides to others / consumes from others (name + one line).

## Deviations from design
Anything built differently from docs/modules/<module>.md, and why.
```

**Fields per function are fixed:** `summary` + `deps` + `file location`. The file location is the point — it's what lets a reader jump straight to the code instead of searching.

Rule of thumb: if a line could go stale when someone edits the code without touching behavior, it doesn't belong here. Summaries and dependencies describe *intent and wiring* (stable); code and queries are *implementation* (belongs in the code, referenced by path).
