"""Per-worker test database setup — MUST run before the app is imported.

Enables parallel tests (pytest-xdist) without workers colliding on one shared DB.
Each xdist worker (``gw0``, ``gw1``, …) gets its own database
(``society_test_gw0`` …); a serial run uses ``society_test``. This module:

1. reads the worker id from ``PYTEST_XDIST_WORKER`` (absent → serial run),
2. rewrites ``DATABASE_URL`` in the environment to that worker's DB,
3. creates the database if needed and runs ``alembic upgrade head`` on it,

all at import time, so ``app.core.config`` / ``app.core.db`` bind to the correct
per-worker database when the app is first imported by ``conftest``.

``conftest.py`` imports this module FIRST (before any ``app.*`` import).
"""
from __future__ import annotations

import os
import subprocess

_BASE_TEST_DB = "society_test"


def _worker_db_name() -> str:
    worker = os.environ.get("PYTEST_XDIST_WORKER")  # e.g. "gw0"; None if serial
    return f"{_BASE_TEST_DB}_{worker}" if worker else _BASE_TEST_DB


def _swap_db_name(url: str, db_name: str) -> str:
    base, _, _tail = url.rpartition("/")
    return f"{base}/{db_name}"


def _ensure_database(admin_url: str, db_name: str) -> None:
    import psycopg  # local import: only needed in the test env

    libpq = admin_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(libpq, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{db_name}"')


def setup_worker_database() -> str:
    """Point ``DATABASE_URL`` at this worker's DB, create + migrate it. Idempotent."""
    base_url = os.environ["DATABASE_URL"]
    db_name = _worker_db_name()
    worker_url = _swap_db_name(base_url, db_name)

    admin_url = _swap_db_name(base_url, "postgres")
    _ensure_database(admin_url, db_name)

    # Bind the app (config/engine) to this worker's DB before it is imported.
    os.environ["DATABASE_URL"] = worker_url

    # Migrate to head. Run in a subprocess so Alembic reads the updated env
    # cleanly and does not interfere with the test process's app import.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": worker_url},
        capture_output=True,
    )
    return worker_url


# Run at import (before app.* is imported by conftest).
setup_worker_database()
