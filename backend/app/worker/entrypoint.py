"""Worker entrypoint (docs/PF §13, docs/02 §7).

Runs in the ``worker`` service (same image, different command). Foundation has a
single scheduled job: purge dead auth rows daily. Feature modules register their
own jobs here later (dues generation, notification reminders, trash purge, …).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app.modules.vault.services.jobs import purge_trash, reconcile_usage
from app.worker.jobs.cleanup import purge_expired_auth_rows

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.worker")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    # Daily auth-row cleanup at 03:15 UTC.
    scheduler.add_job(
        purge_expired_auth_rows,
        trigger="cron",
        hour=3,
        minute=15,
        id="purge_expired_auth_rows",
        replace_existing=True,
    )
    # Vault trash auto-purge daily at 03:30 UTC (docs vault.md §9).
    scheduler.add_job(
        purge_trash,
        trigger="cron",
        hour=3,
        minute=30,
        id="vault_purge_trash",
        replace_existing=True,
    )
    # Vault usage reconcile nightly at 04:00 UTC (docs vault.md §9).
    scheduler.add_job(
        reconcile_usage,
        trigger="cron",
        hour=4,
        minute=0,
        id="vault_reconcile_usage",
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    logger.info("Worker starting; scheduling foundation jobs.")
    scheduler = build_scheduler()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover
        logger.info("Worker shutting down.")


if __name__ == "__main__":
    main()
