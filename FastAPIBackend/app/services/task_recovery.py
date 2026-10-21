"""Reconcile the durable variant outbox with RQ after crashes or Redis loss.

Variant rows and their immutable vlog snapshots are committed together. Delivery
is at least once; the render worker's per-job file lock makes duplicates no-ops.
API, workers and this process must share OBJECT_STORAGE_DIR.
"""
from datetime import timedelta

from filelock import FileLock, Timeout
from rq.job import Job
from rq.exceptions import NoSuchJobError

import scenic_database as database
from app.services.job_manager import job_manager
from app.services.time_utils import as_utc, utc_now
from app.workers.scenic_editing import execution_lock_path, refresh_parent


def queue_state(job_id):
    # Do not interpret an unavailable Redis as lost jobs.
    if not job_manager.ping():
        if not job_manager.allow_local_job_fallback:
            raise RuntimeError("Redis unavailable; reconciliation deferred")
        record = job_manager.get(job_id)
        return record.status if record else None
    try:
        job = Job.fetch(job_id, connection=job_manager.queue.connection)
    except NoSuchJobError:
        return None
    status = job.get_status(refresh=True)
    status = getattr(status, "value", status)
    if status == "queued" and job_manager.queue.get_job_position(job_id) is None:
        return None
    return status


def recover_variants(batch_size=100, after_id=0):
    cutoff = utc_now() - timedelta(seconds=120)
    with database.SessionLocal() as db:
        rows = db.query(database.ScenicVlogVariant).filter(
            database.ScenicVlogVariant.id > after_id,
            database.ScenicVlogVariant.status.in_(("queued", "running")),
            database.ScenicVlogVariant.updated_at < cutoff,
        ).order_by(database.ScenicVlogVariant.id).limit(batch_size).all()
        last_id = rows[-1].id if len(rows) == batch_size else 0
        for variant in rows:
            state = queue_state(variant.processing_job_id)
            if state in {"queued", "running", "started", "deferred", "scheduled"}:
                continue
            try:
                lock_path = execution_lock_path(variant.id, variant.processing_job_id)
                with FileLock(str(lock_path) + ".dispatch", timeout=0):
                    with FileLock(lock_path, timeout=0):
                        db.refresh(variant)
                        if variant.status not in {"queued", "running"} or as_utc(variant.updated_at) >= cutoff:
                            continue
                        vlog = db.get(database.ScenicVlogJob, variant.vlog_job_id)
                        if state in {"failed", "stopped", "canceled", "finished", "succeeded"}:
                            variant.status, variant.error = "failed", "队列任务已结束但未完成交付，请重试"
                        else:
                            variant.status, variant.error = "queued", ""
                        variant.updated_at = utc_now()
                        variant.message = "已恢复任务队列" if variant.status == "queued" else variant.error
                        row = db.get(database.ProcessingJob, variant.processing_job_id)
                        if row:
                            row.status, row.message = variant.status, variant.message
                        db.commit()
                    # Release the execution lock BEFORE dispatch: a local worker may start immediately.
                    if variant.status == "queued":
                        job_manager.enqueue(variant.processing_job_id, "app.workers.scenic_editing.render_scenic_variant",
                            variant.processing_job_id, variant.id, vlog.created_by_username)
                    refresh_parent(db, vlog.id)
            except Timeout:
                continue
        return last_id
