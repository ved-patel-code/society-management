# Project Overview

> Foundation doc 1 of 5. Read this first for a map of the whole app.
> Companion docs: [02-architecture](02-architecture.md) · [03-backend-and-db-principles](03-backend-and-db-principles.md) · [04-module-template](04-module-template.md) · [05-cross-module-contracts](05-cross-module-contracts.md)

## 1. What this app is
A multi-tenant **Society Management** platform for residential societies. Each **society** (a housing complex — buildings or individual houses) is an isolated tenant. The app helps society staff run day-to-day operations and gives residents a self-service view.

## 2. Two audiences, two views
- **Society staff** (admin / secretary): manage structure, residents, finances, complaints, notices, documents.
- **Residents** (owners / tenants): see notices (landing page), raise & track complaints, view their maintenance dues, receive notifications.

The **same backend** serves both; what each user sees is decided by their **role** and the society's **enabled modules**.

## 3. Core principle: modular monolith + per-society modules
- One deployable backend (a **modular monolith**) internally divided into **self-contained modules**.
- Each module can be **enabled/disabled per society** (feature flags).
- New modules/features are added **without editing existing modules**.

## 4. The modules
| Module | Audience | One-line summary |
|---|---|---|
| **Onboarding** | staff | Map a society's structure (buildings/floors/houses or rows/houses), auto/sequential/manual house numbering, initial setup. |
| **House & Occupancy** | staff | House status (empty → owned/rented/to_let/for_sale, never back to empty), owner/tenant details, ID proof, status filters. |
| **Finance** | staff (+ residents view dues) | Maintenance rate (effective-dated), per-house monthly dues, payments (no partial; prepaid blocks), expenses/income/reserve, analytics + rate-change preview. |
| **Vault** | staff | Per-society document storage (pdf/image/excel) with GB limit; preview/download; hosts ID-proof & complaint images in system folders. |
| **Complaints** | both | Residents raise complaints (≤2 images) & track status; staff update status + add completion proof. Images stored in vault. |
| **Notifications** | both | Residents get complaint updates + maintenance-due reminders; staff configure due-day & reminder interval. Needs a background worker. |
| **Notice Board** | both | Staff broadcast notices society-wide; residents' **landing page**; staff have a sent-notices tab + compose. |
| **Elections** (future) | both | In-app election to hand over the `society_admin` role to a different resident — nominate/vote/close, then reassign the role via `user_roles`. Relies on the data-driven roles model + audit trail. |

**Cross-cutting (not a toggleable module):** an **audit trail** records every state-changing action by society_admins (and super_admin) — see [03-backend-and-db-principles](03-backend-and-db-principles.md#7-audit-trail-first-class-requirement). It underpins accountability and the Elections handover history.

## 5. Module priority tiers
- **Foundation (must exist first):** module framework + feature flags, auth + data-driven roles, societies, **Onboarding**, **House & Occupancy**, **minimal Vault core** (needed by ID proofs + complaint images).
- **High value:** Finance, Complaints.
- **Supporting:** Notice Board, Notifications (+ worker), full Vault UI.
- **Future:** Elections (admin handover), online payment gateway, super-admin dedicated frontend (super-admin runs via Swagger UI for now).

## 6. Build sequencing
Backend first. Foundation + Onboarding + House/Occupancy + minimal Vault core, then Finance, Complaints, Notice Board, Notifications. Frontend (Next.js) after the backend API is stable.

## 7. Design order (deep-dive sessions)
Each area gets its own detailed design doc, designed one at a time in this order:
0. **Platform Foundation** ✅ designed — `docs/platform/platform-foundation.md` (auth, users, roles/permissions, societies, module allocation, tenant scoping). The always-on bedrock; must exist before any module runs.
1. **Onboarding** ✅ designed — `docs/modules/onboarding.md` (society structure + numbering)
2. **House & Occupancy** ✅ designed — `docs/modules/house-occupancy.md` (status, owner/tenant, ID proof, filters)
3. **Vault** ✅ designed — `docs/modules/vault.md` (file-manager storage, house-centric folders, Trash, GB limit)
4. **Finance** ✅ designed — `docs/modules/finance.md` (effective-dated rate, materialized dues, prepaid, reserve ledger, analytics)
5. **Complaints** ✅ designed — `docs/modules/complaints.md` (house-scoped, status workflow + auto-archive, extendable categories, status-only + admin note, ≤2 report / ≤2 proof images in Vault)
6. **Notice Board** ✅ designed — `docs/modules/notice-board.md` (society-wide broadcast, draft→published, edit/pin/withdraw/expiry, rich text + Vault attachments, admin read receipts, resident landing feed)
7. **Notifications** ✅ designed — `docs/modules/notifications.md` (in-app engine: event-driven + scheduled dues reminders, advance/due-day/every-N cadence, owners + admin alerts, clear-on-read feed) — **all modules now designed**
