from celery import Celery

from backend.core.config import get_settings

settings = get_settings()
# SQLite is the lightweight/test mode and must not require a local Redis daemon.
broker = (
    "memory://"
    if settings.database_url.startswith("sqlite")
    else settings.celery_broker_url
)
backend = (
    "cache+memory://"
    if settings.database_url.startswith("sqlite")
    else settings.celery_result_backend
)
celery_app = Celery(
    "paper_reader", broker=broker, backend=backend, include=["backend.worker.tasks"]
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": settings.celery_visibility_timeout},
    result_expires=86400,
    task_track_started=True,
)
