"""Scenic vlog generation and staff preview endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

import scenic_database as database
from app.api.auth_dependencies import (
    get_scenic_asset_username,
    get_scenic_current_username as get_current_username,
)
from app.api.scenic.service_adapters import (
    audit as _audit,
    enqueue_scenic_job_or_503 as _enqueue_job_or_503,
)
from app.api.scenic.system_routes import (
    _ensure_tourist_access,
    _staff_vlog,
    _vlog_payload,
)
from app.services.asset_delivery import deliver_object
from app.services.job_manager import job_manager
from app.scenic_editing_schemas import VlogSubmit


router = APIRouter()


def v1_create_tourist_vlog(
    session_id: int,
    payload: VlogSubmit | None = None,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    if payload is not None:
        from app.api.scenic.editing_routes import submit_edited_vlog
        return submit_edited_vlog(session_id, payload, username, db)
    session = _ensure_tourist_access(session_id, username, db, "write")
    included_count = (
        db.query(database.TouristVisitSegment)
        .filter(database.TouristVisitSegment.session_id == session.id, database.TouristVisitSegment.included.is_(True))
        .count()
    )
    if included_count == 0:
        raise HTTPException(status_code=400, detail="没有可用于成片的片段")
    job = job_manager.create("scenic_vlog", "游览视频生成已排队")
    db.add(database.ProcessingJob(
        id=job.id,
        project_id=session.project_id,
        job_type=job.type,
        status=job.status,
        progress=job.progress,
        message=job.message,
    ))
    vlog = database.ScenicVlogJob(
        session_id=session.id,
        processing_job_id=job.id,
        status="queued",
        segment_count=included_count,
        created_by_username=username,
    )
    from app.services.scenic_snapshot import freeze_legacy_segments
    selected = db.query(database.TouristVisitSegment).filter_by(session_id=session.id, included=True).order_by(database.TouristVisitSegment.sort_order, database.TouristVisitSegment.id).all()
    vlog.snapshot_json = json.dumps(freeze_legacy_segments(selected), ensure_ascii=False)
    session.status = "rendering"
    db.add(vlog)
    db.flush()
    _audit(db, username, "scenic.vlog_requested", "tourist_session", str(session.id), f"segments={included_count}")
    db.commit()

    try:
        _enqueue_job_or_503(
            db,
            job.id,
            "app.workers.tasks.generate_scenic_vlog_for_scenic",
            job.id,
            vlog.id,
            username,
            project_id=session.project_id,
        )
    except HTTPException as exc:
        vlog.status = "failed"
        vlog.error = str(exc.detail)
        session.status = "vlog_failed"
        db.commit()
        raise
    db.refresh(vlog)
    return {"status": "accepted", "job_id": job.id, "vlog": _vlog_payload(vlog)}


def v1_list_tourist_vlogs(
    session_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    session = _ensure_tourist_access(session_id, username, db)
    jobs = (
        db.query(database.ScenicVlogJob)
        .filter(database.ScenicVlogJob.session_id == session.id)
        .order_by(database.ScenicVlogJob.created_at.desc())
        .all()
    )
    return {"status": "success", "vlog_jobs": [_vlog_payload(job) for job in jobs]}


def v1_scenic_staff_vlog_preview(
    vlog_id: int,
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    vlog = _staff_vlog(vlog_id, username, db)
    return deliver_object(
        vlog.preview_object_key,
        filename=vlog.preview_filename,
        content_type="video/mp4",
    )


def v1_scenic_staff_vlog_full(
    vlog_id: int,
    download: bool = Query(False),
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    vlog = _staff_vlog(vlog_id, username, db)
    return deliver_object(
        vlog.full_object_key,
        filename=vlog.full_filename,
        content_type="video/mp4",
        download=download,
    )

router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/vlog",
    v1_create_tourist_vlog,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/vlog",
    v1_list_tourist_vlogs,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/vlogs/{vlog_id}/preview",
    v1_scenic_staff_vlog_preview,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/vlogs/{vlog_id}/full",
    v1_scenic_staff_vlog_full,
    methods=["GET"],
)
