"""Safety guard: refuse to run the destructive test suite against a non-test DB.

The suite TRUNCATEs every table before each test (see conftest.py). To make it
impossible to wipe the dev/prod database by accident, importing the test harness
asserts the configured database name ends in ``_test``. Run the suite with
``DATABASE_URL`` pointing at the dedicated test DB (see infra/run-tests.sh).
"""
from __future__ import annotations

from app.core.config import settings


import re

# Accept the base test DB (``…_test``) and per-xdist-worker DBs (``…_test_gw0``).
_TEST_DB_RE = re.compile(r"_test(_gw\d+)?$")


def assert_test_database() -> None:
    url = settings.database_url
    # crude but sufficient: the DB name is the path segment after the last '/'
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not _TEST_DB_RE.search(db_name):
        raise RuntimeError(
            f"Refusing to run the destructive test suite against database "
            f"'{db_name}'. Point DATABASE_URL at a *_test database "
            f"(see backend/scripts/run-tests.sh)."
        )
