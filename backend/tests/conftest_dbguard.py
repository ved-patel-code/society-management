"""Safety guard: refuse to run the destructive test suite against a non-test DB.

The suite TRUNCATEs every table before each test (see conftest.py). To make it
impossible to wipe the dev/prod database by accident, importing the test harness
asserts the configured database name ends in ``_test``. Run the suite with
``DATABASE_URL`` pointing at the dedicated test DB (see infra/run-tests.sh).
"""
from __future__ import annotations

from app.core.config import settings


def assert_test_database() -> None:
    url = settings.database_url
    # crude but sufficient: the DB name is the path segment after the last '/'
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not db_name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run the destructive test suite against database "
            f"'{db_name}'. Point DATABASE_URL at a *_test database "
            f"(see infra/run-tests.sh)."
        )
