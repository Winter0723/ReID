"""Scenic project, history, media, audit, and job endpoints."""

import datetime
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

import scenic_database as database
from app.api.auth_dependencies import (
    get_scenic_asset_username,
    get_scenic_current_username as get_current_username,
)
from app.api.scenic.service_adapters import (
    audit as _audit,
    ensure_access as _ensure_project_access,
    ensure_membership as _ensure_project_membership,
)
from app.api.scenic.system_routes import (
    _camera_wall_payload,
    _project_payload,
    _scenic_storage_project_id,
    _tourist_payload,
)
from app.services.scenic_operations import filter_created_at, work_queue_counts
from app.schemas import JobOut
from app.services.asset_delivery import deliver_object, signed_asset_url
from app.services.camera_stream import scenic_camera_stream_manager as camera_stream_manager
from app.services.job_manager import job_manager
from app.services.media_service import video_preview_payload as _video_preview_payload
from app.services.search_service import (
    pagination_payload as _pagination_payload,
    query_filter_summary as _query_filter_summary,
    search_result_payload as _search_result_payload,
)
from app.services.task_service import job_to_schema as _job_to_schema


router = APIRouter()


from app.schemas import CameraInput


class ScenicProjectCreate(CameraInput):
    reid_model_id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    camera_source: str = Field(default="0", max_length=128)


def scenic_list_projects(
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    user = db.query(database.User).filter(database.User.username == username).first()
    if user.role == "admin":
        rows = db.query(database.Project).order_by(database.Project.created_at.desc()).all()
    else:
        rows = (
            db.query(database.Project)
            .join(database.ProjectMember, database.ProjectMember.project_id == database.Project.id)
            .filter(database.ProjectMember.username == username)
            .order_by(database.Project.created_at.desc())
            .all()
        )
    return [_project_payload(row) for row in rows]


def scenic_create_project(
    payload: ScenicProjectCreate,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    user = db.query(database.User).filter(database.User.username == username).first()
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="viewer 无权创建景区项目")
    camera_source = (payload.camera_source or "0").strip() or "0"
    project = database.Project(
        name=payload.name,
        description=payload.description,
        project_type="scenic",
        camera_source=camera_source,
        owner_username=username,
    )
    from app.services.model_registry import bind_project
    bind_project(project, payload.reid_model_id)
    db.add(project)
    db.flush()
    _ensure_project_membership(db, project.id, username, "owner")
    from app.core.config import get_settings
    db.add(database.VisitorService(
        project_id=project.id, enabled=True,
        price_cents=get_settings().scenic_order_price_cents,
    ))
    _audit(db, username, "scenic.project.created", "project", str(project.id), payload.name)
    db.commit()
    db.refresh(project)
    return _project_payload(project)


def scenic_project_status(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    job_counts = dict(
        db.query(database.ProcessingJob.status, func.count(database.ProcessingJob.id))
        .filter(database.ProcessingJob.project_id == project_id)
        .group_by(database.ProcessingJob.status)
        .all()
    )
    active_job_count = sum(int(job_counts.get(status, 0)) for status in ("queued", "running"))
    job_total = sum(int(count) for count in job_counts.values())
    succeeded_jobs = int(job_counts.get("succeeded", 0))
    indexed_targets = db.query(database.DetectionTarget).filter(database.DetectionTarget.project_id == project_id).count()
    media_counts = dict(
        db.query(database.MediaFile.status, func.count(database.MediaFile.id))
        .filter(database.MediaFile.project_id == project_id)
        .group_by(database.MediaFile.status)
        .all()
    )
    media_total = sum(int(count) for count in media_counts.values())
    indexed_media = int(media_counts.get("indexed", 0))
    processing_media = sum(
        int(media_counts.get(status, 0)) for status in ("uploaded", "processing", "streaming")
    )
    failed_media = int(media_counts.get("failed", 0))
    latest_job = (
        db.query(database.ProcessingJob)
        .filter(database.ProcessingJob.project_id == project_id)
        .order_by(database.ProcessingJob.created_at.desc(), database.ProcessingJob.id.desc())
        .first()
    )
    if active_job_count:
        status_text, total, current = "processing", job_total, succeeded_jobs
    elif indexed_targets > 0:
        status_text, total, current = "ready", indexed_targets, indexed_targets
    else:
        status_text, total, current = "idle", 0, 0
    return {
        "status": status_text,
        "current": current,
        "total": total,
        "indexed_targets": indexed_targets,
        "media_total": media_total,
        "indexed_media": indexed_media,
        "processing_media": processing_media,
        "failed_media": failed_media,
        "latest_job": {
            "id": latest_job.id,
            "type": latest_job.job_type,
            "status": latest_job.status,
            "progress": latest_job.progress,
            "message": latest_job.message,
            "error": latest_job.error,
        } if latest_job else None,
    }


def scenic_project_preview(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    targets = (
        db.query(database.DetectionTarget)
        .filter(database.DetectionTarget.project_id == project_id)
        .order_by(database.DetectionTarget.created_at.desc())
        .limit(4)
        .all()
    )
    return {
        "previews": [
            signed_asset_url(f"/api/v1/scenic/projects/{project_id}/targets/{target.id}/thumbnail")
            for target in targets
        ]
    }


def scenic_list_project_media(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    media_query = db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id)
    total = media_query.count()
    media_rows = (
        media_query.options(selectinload(database.MediaFile.detection_targets))
        .order_by(database.MediaFile.created_at.desc(), database.MediaFile.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "status": "success",
        "media": [
            {
                "id": media.id,
                "project_id": media.project_id,
                "filename": media.filename,
                "media_type": media.media_type,
                "content_type": media.content_type,
                "size_bytes": media.size_bytes,
                "status": media.status,
                "uploader_username": media.uploader_username,
                "created_at": media.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "object_url": signed_asset_url(
                    f"/api/v1/scenic/projects/{project_id}/media/{media.id}/content"
                ) if media.size_bytes else "",
                "thumbnail_url": "",
                "target_count": len(media.detection_targets),
                "targets": [
                    {
                        "id": target.id,
                        "media_id": target.media_id,
                        "thumbnail_url": signed_asset_url(
                            f"/api/v1/scenic/projects/{project_id}/targets/{target.id}/thumbnail"
                        ),
                        "source_info": target.source_info,
                        "timestamp": target.timestamp,
                        "created_at": target.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    for target in media.detection_targets
                ],
            }
            for media in media_rows
        ],
        "pagination": _pagination_payload(page, page_size, total),
    }


def scenic_project_media_content(
    project_id: int,
    media_id: int,
    download: bool = Query(False),
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    if username is not None:
        _ensure_project_access(project_id, username, db)
    media = db.query(database.MediaFile).filter(
        database.MediaFile.id == media_id,
        database.MediaFile.project_id == project_id,
    ).first()
    if not media:
        raise HTTPException(status_code=404, detail="媒体不存在")
    return deliver_object(
        media.object_key,
        filename=media.filename,
        content_type=media.content_type,
        download=download,
    )


def scenic_preview_project_video(
    project_id: int,
    media_id: int,
    start_seconds: float = Query(0.0, ge=0.0),
    duration_seconds: float = Query(8.0, ge=1.0, le=60.0),
    full: bool = Query(False),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    media = db.query(database.MediaFile).filter(
        database.MediaFile.id == media_id,
        database.MediaFile.project_id == project_id,
    ).first()
    if not media:
        raise HTTPException(status_code=404, detail="媒体不存在")
    payload = _video_preview_payload(
        media,
        _scenic_storage_project_id(project_id),
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        full=full,
        stream_manager=camera_stream_manager,
    )
    preview_object_key = payload.pop("preview_object_key", "")
    original_url = signed_asset_url(f"/api/v1/scenic/projects/{project_id}/media/{media_id}/content")
    payload["original_url"] = original_url
    payload["preview_url"] = (
        signed_asset_url(
            f"/api/v1/scenic/projects/{project_id}/media/{media_id}/previews/{Path(preview_object_key).name}"
        )
        if preview_object_key
        else original_url
    )
    return payload


def scenic_project_media_preview_content(
    project_id: int,
    media_id: int,
    filename: str,
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    if username is not None:
        _ensure_project_access(project_id, username, db)
    media_exists = db.query(database.MediaFile.id).filter(
        database.MediaFile.id == media_id,
        database.MediaFile.project_id == project_id,
    ).first()
    if not media_exists or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="视频预览不存在")
    object_key = f"projects/{_scenic_storage_project_id(project_id)}/media/{media_id}/previews/{filename}"
    from app.services.asset_delivery import deliver_video_preview
    return deliver_video_preview(object_key)


def scenic_list_project_jobs(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    status: str = Query("", max_length=32),
    job_type: str = Query("", max_length=64),
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
):
    _ensure_project_access(project_id, username, db)
    query = db.query(database.ProcessingJob).filter(database.ProcessingJob.project_id == project_id)
    if status:
        query = query.filter(database.ProcessingJob.status == status)
    if job_type:
        query = query.filter(database.ProcessingJob.job_type == job_type)
    query = filter_created_at(query, database.ProcessingJob.created_at, date_from, date_to)
    total = query.count()
    jobs = (query.order_by(database.ProcessingJob.created_at.desc(), database.ProcessingJob.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "status": "success",
        "pagination": _pagination_payload(page, page_size, total),
        "jobs": [
            {
                "id": job.id,
                "project_id": job.project_id,
                "media_id": job.media_id,
                "type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "error": job.error,
                "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": job.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for job in jobs
        ],
    }


def scenic_list_project_audit(
    project_id: int,
    limit: int = Query(200, ge=1, le=1000),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=1000),
    q: str = Query("", max_length=128),
    action: str = Query("", max_length=128),
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
):
    _ensure_project_access(project_id, username, db)
    tourist_ids = select(cast(database.TouristSession.id, String)).where(
        database.TouristSession.project_id == project_id
    )
    media_ids = select(cast(database.MediaFile.id, String)).where(database.MediaFile.project_id == project_id)
    job_ids = select(cast(database.ProcessingJob.id, String)).where(
        database.ProcessingJob.project_id == project_id
    )
    project_scope = or_(
        and_(database.AuditLog.resource_type == "project", database.AuditLog.resource_id == str(project_id)),
        and_(database.AuditLog.resource_type == "tourist_session", database.AuditLog.resource_id.in_(tourist_ids)),
        and_(database.AuditLog.resource_type == "media", database.AuditLog.resource_id.in_(media_ids)),
        and_(database.AuditLog.resource_type == "processing_job", database.AuditLog.resource_id.in_(job_ids)),
    )
    query = db.query(database.AuditLog).filter(project_scope)
    if q.strip():
        query = query.filter(or_(*[column.contains(q.strip(), autoescape=True) for column in
                             (database.AuditLog.username, database.AuditLog.action, database.AuditLog.detail)]))
    if action:
        query = query.filter(database.AuditLog.action == action)
    query = filter_created_at(query, database.AuditLog.created_at, date_from, date_to)
    total = query.count()
    size = page_size or limit
    logs = (query.order_by(database.AuditLog.created_at.desc(), database.AuditLog.id.desc())
            .offset((page - 1) * size).limit(size).all())
    return {
        "status": "success",
        "pagination": _pagination_payload(page, size, total),
        "audit": [
            {
                "id": log.id,
                "username": log.username,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "detail": log.detail,
                "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for log in logs
        ],
    }


def scenic_list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    result_limit: int = Query(4, ge=0, le=20),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
    project_id: int | None = Query(None, gt=0),
    q: str = Query("", max_length=128),
    query_type: str = Query("", max_length=32),
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
):
    user = db.query(database.User).filter(database.User.username == username).first()
    task_query = db.query(database.SearchTask).filter(database.SearchTask.username == username)
    if not user or user.role != "admin":
        accessible_project_ids = (
            select(database.Project.id)
            .outerjoin(
                database.ProjectMember,
                and_(
                    database.ProjectMember.project_id == database.Project.id,
                    database.ProjectMember.username == username,
                ),
            )
            .where(
                or_(
                    database.Project.owner_username == username,
                    database.ProjectMember.username == username,
                )
            )
        )
        task_query = task_query.filter(database.SearchTask.project_id.in_(accessible_project_ids))

    if project_id is not None:
        _ensure_project_access(project_id, username, db)
        task_query = task_query.filter(database.SearchTask.project_id == project_id)
    if q.strip():
        task_query = task_query.filter(database.SearchTask.query_content.contains(q.strip(), autoescape=True))
    if query_type:
        task_query = task_query.filter(database.SearchTask.query_type == query_type)
    task_query = filter_created_at(task_query, database.SearchTask.created_at, date_from, date_to)
    total = task_query.count()
    tasks = (
        task_query.order_by(database.SearchTask.created_at.desc(), database.SearchTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    task_ids = [task.id for task in tasks]
    result_counts: dict[int, int] = {}
    preview_rows: dict[int, list] = {task_id: [] for task_id in task_ids}
    if task_ids:
        result_counts = {
            int(task_id): int(count)
            for task_id, count in (
                db.query(database.SearchResult.search_task_id, func.count(database.SearchResult.id))
                .filter(database.SearchResult.search_task_id.in_(task_ids))
                .group_by(database.SearchResult.search_task_id)
                .all()
            )
        }
        if result_limit:
            ranked_results = (
                select(
                    database.SearchResult.id.label("result_id"),
                    func.row_number().over(
                        partition_by=database.SearchResult.search_task_id,
                        order_by=(
                            database.SearchResult.rank.asc(),
                            database.SearchResult.similarity.desc(),
                            database.SearchResult.id.asc(),
                        ),
                    ).label("result_number"),
                )
                .where(database.SearchResult.search_task_id.in_(task_ids))
                .subquery()
            )
            rows = (
                db.query(database.SearchResult)
                .join(ranked_results, ranked_results.c.result_id == database.SearchResult.id)
                .filter(ranked_results.c.result_number <= result_limit)
                .options(
                    joinedload(database.SearchResult.target).joinedload(database.DetectionTarget.media_file)
                )
                .order_by(
                    database.SearchResult.search_task_id.asc(),
                    database.SearchResult.rank.asc(),
                    database.SearchResult.similarity.desc(),
                    database.SearchResult.id.asc(),
                )
                .all()
            )
            for row in rows:
                preview_rows[row.search_task_id].append(row)

    history = []
    for task in tasks:
        try:
            filters = json.loads(task.filters_json or "{}")
        except (TypeError, json.JSONDecodeError):
            filters = {}
        if not isinstance(filters, dict):
            filters = {}
        history.append({
            "id": task.id,
            "project_id": task.project_id,
            "session_id": str(task.project_id),
            "type": task.query_type,
            "content": task.query_content,
            "query_object_url": signed_asset_url(
                f"/api/v1/scenic/search/tasks/{task.id}/query"
            ) if task.query_object_key else "",
            "filters": filters,
            "filter_summary": _query_filter_summary(filters),
            "created_at": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "result_count": result_counts.get(task.id, 0),
            "results": [
                _search_result_payload(row, None, scope="scenic")
                for row in preview_rows.get(task.id, [])
            ],
        })
    return {
        "status": "success",
        "history": history,
        "pagination": _pagination_payload(page, page_size, total),
    }


def scenic_get_history_detail(
    history_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    task = db.query(database.SearchTask).filter(
        database.SearchTask.id == history_id,
        database.SearchTask.username == username,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="搜索记录不存在")
    _ensure_project_access(task.project_id, username, db)
    result_query = db.query(database.SearchResult).filter(database.SearchResult.search_task_id == task.id)
    result_count = result_query.count()
    rows = (
        result_query.options(
            joinedload(database.SearchResult.target).joinedload(database.DetectionTarget.media_file)
        )
        .order_by(database.SearchResult.rank.asc(), database.SearchResult.similarity.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    try:
        filters = json.loads(task.filters_json or "{}")
    except (TypeError, json.JSONDecodeError):
        filters = {}
    if not isinstance(filters, dict):
        filters = {}
    return {
        "status": "success",
        "task": {
            "id": task.id,
            "project_id": task.project_id,
            "type": task.query_type,
            "content": task.query_content,
            "filters": filters,
            "filter_summary": _query_filter_summary(filters),
            "query_object_url": signed_asset_url(
                f"/api/v1/scenic/search/tasks/{task.id}/query"
            ) if task.query_object_key else None,
            "created_at": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "result_count": result_count,
        },
        "pagination": _pagination_payload(page, page_size, result_count),
        "results": [_search_result_payload(row, None, scope="scenic") for row in rows],
    }


def scenic_get_job(
    job_id: str,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    db_job = db.query(database.ProcessingJob).filter(database.ProcessingJob.id == job_id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_project_access(db_job.project_id, username, db)
    try:
        record = job_manager.get(job_id)
    except Exception:
        record = None
    # Enhanced render progress is committed with its output in Scenic DB.
    # A stale Redis mirror must not hide a completed or failed render.
    if record and db_job.job_type != "scenic_vlog_variant":
        return _job_to_schema(record)
    return JobOut(
        id=db_job.id,
        type=db_job.job_type,
        status=db_job.status,
        progress=db_job.progress,
        message=db_job.message,
        error=db_job.error,
        created_at=db_job.created_at,
        updated_at=db_job.updated_at,
    )


def v1_scenic_dashboard(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    today_start = datetime.datetime.now(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sessions = db.query(database.TouristSession).filter(database.TouristSession.project_id == project_id)
    orders = (
        db.query(database.ScenicOrder)
        .join(database.TouristSession, database.TouristSession.id == database.ScenicOrder.session_id)
        .filter(database.TouristSession.project_id == project_id)
    )
    vlog_jobs = (
        db.query(database.ScenicVlogJob)
        .join(database.TouristSession, database.TouristSession.id == database.ScenicVlogJob.session_id)
        .filter(database.TouristSession.project_id == project_id)
    )
    paid_orders = orders.filter(database.ScenicOrder.status == "paid")
    recent_sessions = sessions.order_by(database.TouristSession.created_at.desc()).limit(8).all()
    return {
        "status": "success",
        "project_id": project_id,
        "work_queue_counts": work_queue_counts(db, project_id),
        "today_sessions": sessions.filter(database.TouristSession.created_at >= today_start).count(),
        "active_sessions": sessions.filter(
            database.TouristSession.status.in_(["registered", "matched", "rendering"])
        ).count(),
        "matched_sessions": sessions.filter(
            database.TouristSession.status.in_(["matched", "vlog_ready", "paid"])
        ).count(),
        "camera_points": db.query(database.ScenicCameraPoint).filter(
            database.ScenicCameraPoint.project_id == project_id
        ).count(),
        "active_camera_points": db.query(database.ScenicCameraPoint).filter(
            database.ScenicCameraPoint.project_id == project_id,
            database.ScenicCameraPoint.is_active.is_(True),
        ).count(),
        "camera_status": camera_stream_manager.status(project_id),
        "camera_wall": _camera_wall_payload(project_id, db),
        "pending_vlogs": vlog_jobs.filter(
            database.ScenicVlogJob.status.in_(["queued", "running"])
        ).count(),
        "ready_vlogs": vlog_jobs.filter(database.ScenicVlogJob.status == "succeeded").count(),
        "pending_orders": orders.filter(database.ScenicOrder.status == "pending").count(),
        "paid_orders": paid_orders.count(),
        "paid_revenue_cents": sum(
            row[0] or 0 for row in paid_orders.with_entities(database.ScenicOrder.amount_cents).all()
        ),
        "recent_sessions": [_tourist_payload(session, db) for session in recent_sessions],
    }

router.add_api_route("/api/v1/scenic/projects", scenic_list_projects, methods=["GET"])
router.add_api_route("/api/v1/scenic/projects", scenic_create_project, methods=["POST"])
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/status",
    scenic_project_status,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/preview",
    scenic_project_preview,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/media",
    scenic_list_project_media,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/media/{media_id}/content",
    scenic_project_media_content,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/media/{media_id}/preview-video",
    scenic_preview_project_video,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/media/{media_id}/previews/{filename}",
    scenic_project_media_preview_content,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/jobs",
    scenic_list_project_jobs,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/audit",
    scenic_list_project_audit,
    methods=["GET"],
)
router.add_api_route("/api/v1/scenic/history", scenic_list_history, methods=["GET"])
router.add_api_route(
    "/api/v1/scenic/history/{history_id}",
    scenic_get_history_detail,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/jobs/{job_id}",
    scenic_get_job,
    methods=["GET"],
    response_model=JobOut,
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/dashboard",
    v1_scenic_dashboard,
    methods=["GET"],
)


@router.get("/api/v1/scenic/models")
def list_models(username: str = Depends(get_current_username)):
    from app.services.model_registry import public_models
    return public_models()
