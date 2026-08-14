from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("ffroom", broker=settings.celery_broker_url, backend=settings.celery_result_backend)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "dispatch-due-jobs": {"task": "app.workers.tasks.dispatch_due_jobs", "schedule": 5.0},
        "recheck-channel-admin": {
            "task": "app.workers.tasks.recheck_channel_admin",
            "schedule": crontab(minute=15),
        },
        "purge-old-credentials": {
            "task": "app.workers.tasks.purge_old_credentials",
            "schedule": crontab(hour=3, minute=10),
        },
    },
    task_routes={
        "app.workers.tasks.send_telegram_message": {"queue": "telegram"},
        "app.workers.tasks.run_broadcast": {"queue": "broadcasts"},
        "app.workers.tasks.dispatch_due_jobs": {"queue": "default"},
    },
)

celery_app.autodiscover_tasks(["app.workers"])
