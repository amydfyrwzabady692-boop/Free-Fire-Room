from __future__ import annotations

import asyncio

from app.core.logging import get_logger

log = get_logger("jobs")


def spawn(task, *args) -> None:
    """Run a Celery task in-process so we do not need a worker container."""

    def _call() -> None:
        try:
            task.run(*args)
        except Exception:
            log.exception("background_job_failed", task=getattr(task, "name", str(task)))

    try:
        asyncio.get_running_loop().run_in_executor(None, _call)
    except RuntimeError:
        _call()
