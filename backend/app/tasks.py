from celery import Celery

from app.config import Settings

celery_app = Celery("car_manual_rag", broker=Settings().redis_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json",
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    broker_connection_timeout=3, broker_transport_options={"visibility_timeout": 1800},
    task_soft_time_limit=850, task_time_limit=900,
    beat_schedule={"recover-imports": {"task": "rag.recover", "schedule": 30.0}},
)


@celery_app.task(name="rag.import")
def import_manual(job_id):
    from app.importing import process_job
    process_job(job_id)


@celery_app.task(name="rag.recover")
def reconcile_imports():
    from app.importing import recover_jobs
    return recover_jobs()
