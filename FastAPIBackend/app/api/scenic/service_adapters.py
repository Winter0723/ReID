"""Bind shared domain services to the scenic ORM models."""

import scenic_database as database

from app.services.audit_service import add_audit_log
from app.services.permission_service import ensure_project_access, ensure_project_membership
from app.services.persistence_service import create_media_record, persist_hit_result
from app.services.task_service import create_processing_job, enqueue_job_or_503


def audit(db, username, action, resource_type, resource_id=None, detail=""):
    return add_audit_log(db, database.AuditLog, username, action, resource_type, resource_id, detail)


def ensure_access(project_id, username, db, permission="read"):
    return ensure_project_access(
        db,
        project_model=database.Project,
        project_member_model=database.ProjectMember,
        user_model=database.User,
        project_id=project_id,
        username=username,
        permission=permission,
    )


def ensure_membership(db, project_id, username, role="owner"):
    return ensure_project_membership(db, database.ProjectMember, project_id, username, role)


def create_scenic_media_record(db, **kwargs):
    return create_media_record(db, database.MediaFile, **kwargs)


def create_scenic_processing_job(db, project_id, media_id, job_type, message):
    return create_processing_job(db, database.ProcessingJob, project_id, media_id, job_type, message)


def enqueue_scenic_job_or_503(db, job_id, task_path, *args, project_id=None, media_id=None):
    return enqueue_job_or_503(
        db,
        database.ProcessingJob,
        job_id,
        task_path,
        *args,
        project_id=project_id,
        media_id=media_id,
    )


def persist_scenic_hit_result(db, task_id, hit, rank):
    return persist_hit_result(db, database.SearchResult, task_id, hit, rank)
