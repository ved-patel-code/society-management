---
name: tech-stack
description: Confirmed tech stack for the Society Management app (backend-first)
metadata: 
  node_type: memory
  type: project
  originSessionId: a92a120b-70b1-4de9-817d-84fa741891a8
---

Society Management app (d:\society, greenfield). Confirmed stack:
- Frontend: Next.js (built later — backend first)
- Backend: Python FastAPI + SQLAlchemy + Alembic + Pydantic
- DB: Postgres
- File storage: MinIO (S3-compatible) container; per-society storage GB limits; signed URLs for preview/download
- Infra: Docker Compose, separate containers (frontend, backend, postgres, minio, + worker for notifications)

**Why:** User chose these explicitly on 2026-07-04. Backend is the priority.
See [[modularity-model]].
