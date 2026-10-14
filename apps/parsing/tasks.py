from celery import shared_task

from .models import ParseJob
from .services import run_job


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def parse_file_task(self, job_id):
    job = ParseJob.objects.select_related("file", "file__vendor").get(pk=job_id)
    if job.finished:
        return
    run_job(job)
