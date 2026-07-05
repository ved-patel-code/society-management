# Society Management App

A multi-tenant, modular platform for managing residential societies — structure mapping, residents, finances, complaints, notices, documents, and notifications.

- **Architecture:** modular monolith. FastAPI backend (built first) + Next.js frontend (later) + PostgreSQL + MinIO, all in Docker.
- **Modularity:** per-society feature flags; each module is self-contained; new modules add without touching old code.

## Documentation
See [`docs/`](docs/README.md) — start with the foundation docs (01–05), then per-module design docs.

## Repo layout
```
society/
  docs/        # design & architecture documentation
  backend/     # FastAPI modular monolith (built first)
  frontend/    # Next.js (built later)
  infra/       # docker-compose, env templates, service init
```

> Status: design phase. No application code yet — documenting the foundation and designing modules one at a time.
