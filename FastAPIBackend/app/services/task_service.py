from fastapi import HTTPException

from app.schemas import JobOut
from app.services.job_manager import LocalJobFallbackDisabled, job_manager


def job_to_schema(record):
    return JobOut(
        id=record.id,
        type=record.type,
        status=record.status,
        progress=record.progress,
        message=record.message,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def sync_job_to_db(
    db,
    processing_job_model,
    record,
    project_id: int | None = None,
    media_id: int | None = None,
    *,
    commit: bool = True,
):
    db_job = db.query(processing_job_model).filter(processing_job_model.id == record.id).first()
    if not db_job:
        db_job = processing_job_model(
            id=record.id,
            project_id=project_id,
            media_id=media_id,
            job_type=record.type,
            status=record.status,
            progress=record.progress,
            message=record.message,
            error=record.error,
        )
        db.add(db_job)
    else:
        db_job.status = record.status
        db_job.progress = record.progress
        db_job.message = record.message
        db_job.error = record.error
    if commit:
        db.commit()


def create_processing_job(db, processing_job_model, project_id: int, media_id: int | None, job_type: str, message: str):
    job = job_manager.create(job_type, message)
    db.add(
        processing_job_model(
            id=job.id,
            project_id=project_id,
            media_id=media_id,
            job_type=job.type,
            status=job.status,
            progress=job.progress,
            message=job.message,
        )
    )
    return job


def enqueue_job(job_id: str, task_path: str, *args) -> None:
    job_manager.enqueue(job_id, task_path, *args)


def mark_job_enqueue_failed(
    db,
    processing_job_model,
    job_id: str,
    project_id: int | None,
    media_id: int | None,
    message: str,
) -> None:
    record = job_manager.update(job_id, status="failed", progress=1.0, message="任务入队失败", error=message)
    if record:
        sync_job_to_db(db, processing_job_model, record, project_id, media_id)
        return
    db_job = db.query(processing_job_model).filter(processing_job_model.id == job_id).first()
    if db_job:
        db_job.status = "failed"
        db_job.progress = 1.0
        db_job.message = "任务入队失败"
        db_job.error = message


def enqueue_job_or_503(
    db,
    processing_job_model,
    job_id: str,
    task_path: str,
    *args,
    project_id: int | None = None,
    media_id: int | None = None,
) -> None:
    try:
        enqueue_job(job_id, task_path, *args)
    except LocalJobFallbackDisabled as exc:
        mark_job_enqueue_failed(db, processing_job_model, job_id, project_id, media_id, str(exc))
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
