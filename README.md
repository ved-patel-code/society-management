# Society Management App

A multi-tenant, modular platform for managing residential societies — structure mapping, residents, finances, complaints, notices, documents, and notifications.

- **Architecture:** modular monolith. FastAPI backend (built first) + Next.js frontend (later) + PostgreSQL + MinIO, all in Docker.
- **Modularity:** per-society feature flags; each module is self-contained; new modules add without touching old code.

## Documentation
See [`docs/`](docs/README.md) — start with the foundation docs (01–05), then per-module design docs.

## Repo layout
```
society/
  docs/        # design & architecture docs + per-module design + as-built + build-log
  backend/     # FastAPI modular monolith (built first)
  frontend/    # Next.js (built later — placeholder)
  infra/       # Dockerfile, env template, service init
  docker-compose.yml
```

## Running (all in Docker)
```
cp infra/.env.template .env        # fill in secrets (JWT_SECRET must be >= 32 bytes)
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.cli.seed
docker compose exec backend bash scripts/run-tests.sh   # tests (isolated society_test DB)
```
API + Swagger UI at `http://localhost:8000/docs`.

> Status: **implementation, backend-first.** Built, tested, and on `main`: Module 0
> (Platform Foundation), Module 1 (Onboarding), Module 2 (House & Occupancy), and
> Module 3 (Vault — document storage on MinIO). All modules are designed (`docs/`);
> the remaining modules are built one at a time on feature branches. See
> `docs/implemented/` for as-built indexes.
