#!/usr/bin/env bash
# Run the backend test suite against DEDICATED, isolated test database(s).
#
# The suite truncates every table between tests, so it must never touch the
# dev/prod DB. Tests run IN PARALLEL by default (pytest-xdist): each worker gets
# its OWN database (society_test_gw0, society_test_gw1, …), created + migrated on
# demand by tests/_worker_db.py — so workers never collide on the shared-truncate
# reset. A serial run (-n0) uses society_test.
#
# Usage (from the repo root):
#   docker compose exec backend bash scripts/run-tests.sh                 # parallel, all tests
#   docker compose exec backend bash scripts/run-tests.sh tests/test_auth.py
#   docker compose exec backend bash scripts/run-tests.sh -n0 -q          # serial (debug)
#   docker compose exec backend bash scripts/run-tests.sh -k login -x     # any pytest args
set -euo pipefail

BASE_URL="${DATABASE_URL:?DATABASE_URL must be set}"

# Point the app at the BASE test DB name; each xdist worker swaps in its own
# suffix (see tests/_worker_db.py). Guarded by conftest_dbguard (must end in _test).
export DATABASE_URL="${BASE_URL%/*}/society_test"

# Fast, test-ONLY Argon2id params (production keeps passlib's strong defaults;
# these are NEVER set in the real .env). ~83ms/hash -> ~0.5ms/hash.
export ARGON2_TIME_COST="${ARGON2_TIME_COST:-1}"
export ARGON2_MEMORY_COST="${ARGON2_MEMORY_COST:-8}"
export ARGON2_PARALLELISM="${ARGON2_PARALLELISM:-1}"

# Default to parallel across available cores unless the caller passes their own
# -n / -p no:xdist. Detect whether any -n flag is present in the args.
has_n=false
for a in "$@"; do
  case "$a" in
    -n|-n*) has_n=true ;;
  esac
done

if $has_n; then
  exec pytest "$@"
else
  # Default worker count. Each worker migrates its own DB, so very high counts add
  # setup overhead that only pays off with many tests. TEST_WORKERS lets you tune
  # it (e.g. TEST_WORKERS=auto for a large suite on a big box). Default: 4.
  exec pytest -n "${TEST_WORKERS:-4}" "$@"
fi
