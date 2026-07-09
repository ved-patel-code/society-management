"""Alembic environment. Single linear history (docs/03 §8).

Imports ALL model modules so ``Base.metadata`` sees every table (autogenerate
works for future modules with no edits here). DB URL comes from Settings, never
hardcoded. New modules add a migration and import their models below.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base

# Import every model module so all tables register on Base.metadata.
import app.platform.models  # noqa: F401,E402
import app.modules.onboarding.models  # noqa: F401,E402
import app.modules.houses.models  # noqa: F401,E402
import app.modules.vault.models  # noqa: F401,E402
import app.modules.finance.models  # noqa: F401,E402
import app.modules.complaints.models  # noqa: F401,E402
import app.modules.notices.models  # noqa: F401,E402
import app.modules.notifications.models  # noqa: F401,E402

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
