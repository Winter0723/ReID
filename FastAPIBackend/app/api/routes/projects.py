import json
import os
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import database

from app.api.auth_dependencies import get_core_asset_username, get_current_username
from app.services.audit_service import add_audit_log
from app.services.media_service import (
    BASE_GALLERY_DIR,
    MAX_BATCH_UPLOAD_FILES,
    delete_media_assets as _delete_media_assets,
    delete_storage_objects as _delete_storage_objects,
    media_download_url as _media_download_url,
    media_type_for_filename as _media_type_for_filename,
    merge_central_camera_status as _merge_central_camera_status,
    safe_delete_vectors_by_media as _safe_delete_vectors_by_media,
    safe_delete_vectors_by_project as _safe_delete_vectors_by_project,
    target_video_seek_seconds as _target_video_seek_seconds,
    video_preview_payload as _video_preview_payload,
)
from app.core.config import get_settings
from app.schemas import (
    CameraStartRequest,
    ProjectCreate,
    ProjectMemberOut,
    ProjectMemberUpsert,
    ProjectOut,
    ProjectUpdate,
)
from app.services.asset_delivery import deliver_object, signed_asset_url
from app.services.camera_stream import camera_stream_manager
from app.services.job_manager import LocalJobFallbackDisabled
from app.services.permission_service import (
    PROJECT_ROLES,
    ensure_project_access,
    ensure_project_membership,
)
from app.services.persistence_service import create_media_record, find_duplicate_media
from app.services.schema_service import schema_dict as _schema_dict
from app.services.search_service import pagination_payload as _pagination_payload
from app.services.storage import UploadTooLargeError, storage
from app.services.task_service import (
    create_processing_job,
    enqueue_job as _enqueue_job,
    enqueue_job_or_503,
    mark_job_enqueue_failed,
)

router = APIRouter()


def _audit(db, username, action, resource_type, resource_id=None, detail=""):
    return add_audit_log(db, database.AuditLog, username, action, resource_type, resource_id, detail)


def _ensure_project_access(project_id, username, db, permission="read"):
    return ensure_project_access(
        db,
        project_model=database.Project,
        project_member_model=database.ProjectMember,
        user_model=database.User,
        project_id=project_id,
        username=username,
        permission=permission,
    )


def _ensure_project_membership(db, project_id, username, role="owner"):
    return ensure_project_membership(db, database.ProjectMember, project_id, username, role)


def _create_media_record(db, **kwargs):
    return create_media_record(db, database.MediaFile, **kwargs)


def _find_duplicate_media(db, project_id, sha256):
    return find_duplicate_media(db, database.MediaFile, project_id, sha256)


def _create_processing_job(db, project_id, media_id, job_type, message):
    return create_processing_job(db, database.ProcessingJob, project_id, media_id, job_type, message)


def _enqueue_job_or_503(db, job_id, task_path, *args, project_id=None, media_id=None):
    return enqueue_job_or_503(
        db,
        database.ProcessingJob,
        job_id,
        task_path,
        *args,
        project_id=project_id,
        media_id=media_id,
    )


def _mark_job_enqueue_failed(db, job_id, project_id, media_id, message):
    return mark_job_enqueue_failed(db, database.ProcessingJob, job_id, project_id, media_id, message)


@router.get("/api/v1/projects/{project_id}/media/{media_id}/content")
def v1_get_media_content(
    project_id: int,
    media_id: int,
    download: bool = Query(False),
    username: str | None = Depends(get_core_asset_username),
    db: Session = Depends(database.get_db),
):
    if username is not None:
        _ensure_project_access(project_id, username, db)
    media = (
        db.query(database.MediaFile)
        .filter(database.MediaFile.id == media_id, database.MediaFile.project_id == project_id)
        .first()
    )
    if not media:
        raise HTTPException(status_code=404, detail="媒体不存在")
    return deliver_object(
        media.object_key,
        filename=media.filename,
        content_type=media.content_type,
        download=download,
    )


@router.get("/api/v1/projects/{project_id}/targets/{target_id}/thumbnail")
def v1_get_target_thumbnail(
    project_id: int,
    target_id: int,
    username: str | None = Depends(get_core_asset_username),
    db: Session = Depends(database.get_db),
):
    if username is not None:
        _ensure_project_access(project_id, username, db)
    target = (
        db.query(database.DetectionTarget)
        .filter(
            database.DetectionTarget.id == target_id,
            database.DetectionTarget.project_id == project_id,
        )
        .first()
    )
    if not target or not target.crop_object_key:
        raise HTTPException(status_code=404, detail="目标缩略图不存在")
    return deliver_object(target.crop_object_key, content_type="image/jpeg")

@router.get("/api/v1/projects", response_model=List[ProjectOut])
def v1_list_projects(username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    user = db.query(database.User).filter(database.User.username == username).first()
    if user.role == "admin":
        return db.query(database.Project).order_by(database.Project.created_at.desc()).all()
    return (
        db.query(database.Project)
        .join(database.ProjectMember, database.ProjectMember.project_id == database.Project.id)
        .filter(database.ProjectMember.username == username)
        .order_by(database.Project.created_at.desc())
        .all()
    )


@router.post("/api/v1/projects", response_model=ProjectOut)
def v1_create_project(payload: ProjectCreate, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    user = db.query(database.User).filter(database.User.username == username).first()
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="viewer 无权创建项目")
    camera_source = (payload.camera_source or "0").strip() if payload.project_type == "camera" else ""
    project = database.Project(
        name=payload.name,
        description=payload.description,
        project_type=payload.project_type,
        camera_source=camera_source,
        owner_username=username,
    )
    db.add(project)
    db.flush()
    _ensure_project_membership(db, project.id, username, "owner")
    _audit(db, username, "project.created", "project", str(project.id), payload.name)
    db.commit()
    db.refresh(project)
    return project


@router.get("/api/v1/projects/{project_id}", response_model=ProjectOut)
def v1_get_project(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    return _ensure_project_access(project_id, username, db)


@router.patch("/api/v1/projects/{project_id}", response_model=ProjectOut)
def v1_update_project(project_id: int, payload: ProjectUpdate, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    project = _ensure_project_access(project_id, username, db, "manage")
    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    _audit(db, username, "project.updated", "project", str(project_id), json.dumps(_schema_dict(payload, exclude_none=True), ensure_ascii=False))
    db.commit()
    db.refresh(project)
    return project


@router.delete("/api/v1/projects/{project_id}")
def v1_delete_project(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    project = _ensure_project_access(project_id, username, db, "manage")
    camera_stream_manager.stop(project_id)
    media_rows = db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id).all()
    _delete_media_assets(db, media_rows)
    if media_rows:
        _safe_delete_vectors_by_project(project_id)
    db.delete(project)
    _audit(db, username, "project.deleted", "project", str(project_id), project.name)
    db.commit()
    project_gallery_dir = os.path.join(BASE_GALLERY_DIR, f"project_{project_id}")
    if os.path.isdir(project_gallery_dir):
        shutil.rmtree(project_gallery_dir)
    return {"status": "success"}


@router.get("/api/v1/projects/{project_id}/members", response_model=List[ProjectMemberOut])
def v1_list_project_members(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    _ensure_project_access(project_id, username, db)
    return (
        db.query(database.ProjectMember)
        .filter(database.ProjectMember.project_id == project_id)
        .order_by(database.ProjectMember.created_at.asc())
        .all()
    )


@router.put("/api/v1/projects/{project_id}/members/{member_username}", response_model=ProjectMemberOut)
def v1_upsert_project_member(
    project_id: int,
    member_username: str,
    payload: ProjectMemberUpsert,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "manage")
    target_username = payload.username or member_username
    if target_username != member_username:
        raise HTTPException(status_code=400, detail="路径用户名与请求体不一致")
    if payload.role not in PROJECT_ROLES:
        raise HTTPException(status_code=400, detail="无效项目角色")
    user = db.query(database.User).filter(database.User.username == target_username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if project.owner_username == target_username and payload.role != "owner":
        raise HTTPException(status_code=400, detail="项目所有者必须保留 owner 角色")
    member = _ensure_project_membership(db, project_id, target_username, payload.role)
    _audit(db, username, "project.member_upserted", "project", str(project_id), json.dumps(_schema_dict(payload), ensure_ascii=False))
    db.commit()
    db.refresh(member)
    return member


@router.delete("/api/v1/projects/{project_id}/members/{member_username}")
def v1_delete_project_member(
    project_id: int,
    member_username: str,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "manage")
    if project.owner_username == member_username:
        raise HTTPException(status_code=400, detail="不能移除项目所有者")
    member = (
        db.query(database.ProjectMember)
        .filter(
            database.ProjectMember.project_id == project_id,
            database.ProjectMember.username == member_username,
        )
        .first()
    )
    if member:
        db.delete(member)
        _audit(db, username, "project.member_deleted", "project", str(project_id), member_username)
        db.commit()
    return {"status": "success"}


@router.post("/api/v1/projects/{project_id}/media")
def v1_upload_media(
    project_id: int,
    file: UploadFile = File(...),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "write")
    media_type = _media_type_for_filename(file.filename)

    try:
        stored = storage.save_upload(project_id, file.filename, file.file, category="originals")
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    object_committed = False
    try:
        duplicate = _find_duplicate_media(db, project_id, stored.sha256)
        if duplicate:
            _audit(
                db,
                username,
                "media.duplicate_skipped",
                "media",
                str(duplicate.id),
                json.dumps({"filename": file.filename, "sha256": stored.sha256}, ensure_ascii=False),
            )
            db.commit()
            return {
                "status": "duplicate",
                "media_id": duplicate.id,
                "existing_media_id": duplicate.id,
                "job_id": None,
            }

        media = _create_media_record(
            db,
            project_id=project_id,
            username=username,
            filename=file.filename,
            media_type=media_type,
            stored=stored,
        )
        job = _create_processing_job(db, project_id, media.id, "media_index", "等待后台处理")
        _audit(
            db,
            username,
            "media.uploaded",
            "media",
            str(media.id),
            json.dumps({
                "filename": file.filename,
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
                "sha256": stored.sha256,
            }, ensure_ascii=False),
        )
        db.commit()
        object_committed = True
    except IntegrityError as exc:
        db.rollback()
        duplicate = _find_duplicate_media(db, project_id, stored.sha256)
        if not duplicate:
            raise
        _audit(
            db,
            username,
            "media.duplicate_skipped",
            "media",
            str(duplicate.id),
            json.dumps({"filename": file.filename, "sha256": stored.sha256}, ensure_ascii=False),
        )
        db.commit()
        return {
            "status": "duplicate",
            "media_id": duplicate.id,
            "existing_media_id": duplicate.id,
            "job_id": None,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        if not object_committed:
            _delete_storage_objects([stored.object_key])

    _enqueue_job_or_503(
        db,
        job.id,
        "app.workers.tasks.process_media_index",
        job.id,
        media.id,
        project_id,
        stored.object_key,
        media_type,
        username,
        project_id=project_id,
        media_id=media.id,
    )
    return {"status": "accepted", "media_id": media.id, "job_id": job.id}


@router.post("/api/v1/projects/{project_id}/media/batch")
def v1_upload_media_batch(
    project_id: int,
    files: List[UploadFile] = File(...),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "write")
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")
    if len(files) > MAX_BATCH_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"单次最多上传 {MAX_BATCH_UPLOAD_FILES} 个文件")
    file_specs = [(file, _media_type_for_filename(file.filename)) for file in files]
    accepted = []
    queued_tasks = []
    pending_object_keys: set[str] = set()
    max_batch_bytes = get_settings().max_upload_mb * 1024 * 1024
    batch_size_bytes = 0
    try:
        for file, media_type in file_specs:
            stored = storage.save_upload(project_id, file.filename, file.file, category="originals")
            pending_object_keys.add(stored.object_key)
            batch_size_bytes += stored.size_bytes
            if batch_size_bytes > max_batch_bytes:
                raise UploadTooLargeError(
                    f"批量文件总大小超过最大限制 {get_settings().max_upload_mb} MB"
                )
            duplicate = _find_duplicate_media(db, project_id, stored.sha256)
            if duplicate:
                storage.delete(stored.object_key)
                pending_object_keys.discard(stored.object_key)
                _audit(
                    db,
                    username,
                    "media.duplicate_skipped",
                    "media",
                    str(duplicate.id),
                    json.dumps({"filename": file.filename, "sha256": stored.sha256}, ensure_ascii=False),
                )
                accepted.append({
                    "media_id": duplicate.id,
                    "existing_media_id": duplicate.id,
                    "job_id": None,
                    "filename": file.filename,
                    "status": "duplicate",
                })
                continue

            media = _create_media_record(
                db,
                project_id=project_id,
                username=username,
                filename=file.filename,
                media_type=media_type,
                stored=stored,
            )
            job = _create_processing_job(db, project_id, media.id, "media_index", "等待后台处理")
            _audit(
                db,
                username,
                "media.uploaded",
                "media",
                str(media.id),
                json.dumps({
                    "filename": file.filename,
                    "content_type": stored.content_type,
                    "size_bytes": stored.size_bytes,
                    "sha256": stored.sha256,
                }, ensure_ascii=False),
            )
            accepted.append({
                "media_id": media.id,
                "job_id": job.id,
                "filename": file.filename,
                "status": "accepted",
            })
            queued_tasks.append((job.id, media.id, stored.object_key, media_type))
        db.commit()
        pending_object_keys.clear()
    except UploadTooLargeError as exc:
        db.rollback()
        _delete_storage_objects(pending_object_keys)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        _delete_storage_objects(pending_object_keys)
        raise HTTPException(status_code=409, detail="批量上传遇到并发重复文件，请重试") from exc
    except Exception:
        db.rollback()
        _delete_storage_objects(pending_object_keys)
        raise
    enqueue_errors = []
    for job_id, media_id, object_key, media_type in queued_tasks:
        try:
            _enqueue_job(
                job_id,
                "app.workers.tasks.process_media_index",
                job_id,
                media_id,
                project_id,
                object_key,
                media_type,
                username,
            )
        except LocalJobFallbackDisabled as exc:
            enqueue_errors.append((job_id, media_id, str(exc)))
    if enqueue_errors:
        for job_id, media_id, message in enqueue_errors:
            _mark_job_enqueue_failed(db, job_id, project_id, media_id, message)
        db.commit()
        raise HTTPException(status_code=503, detail=enqueue_errors[0][2])
    return {"status": "accepted", "items": accepted}


@router.get("/api/v1/projects/{project_id}/status")
def v1_project_status(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    _ensure_project_access(project_id, username, db)
    jobs = db.query(database.ProcessingJob).filter(database.ProcessingJob.project_id == project_id).all()
    active_jobs = [job for job in jobs if job.status in {"queued", "running"} and job.job_type != "camera_stream"]
    active_camera_jobs = [job for job in jobs if job.status in {"queued", "running"} and job.job_type == "camera_stream"]
    indexed_targets = db.query(database.DetectionTarget).filter(database.DetectionTarget.project_id == project_id).count()
    media_query = db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id)
    media_total = media_query.count()
    indexed_media = media_query.filter(database.MediaFile.status == "indexed").count()
    processing_media = media_query.filter(database.MediaFile.status.in_(["uploaded", "processing", "streaming"])).count()
    failed_media = media_query.filter(database.MediaFile.status == "failed").count()
    latest_job = (
        db.query(database.ProcessingJob)
        .filter(database.ProcessingJob.project_id == project_id)
        .order_by(database.ProcessingJob.created_at.desc())
        .first()
    )
    if active_jobs:
        total = len(jobs)
        current = sum(1 for job in jobs if job.status == "succeeded")
        status_text = "processing"
    elif active_camera_jobs and indexed_targets == 0:
        total = 1
        current = 0
        status_text = "processing"
    elif processing_media:
        total = media_total
        current = indexed_media
        status_text = "processing"
    elif indexed_targets > 0:
        total = indexed_targets
        current = indexed_targets
        status_text = "ready"
    elif failed_media > 0:
        total = media_total
        current = indexed_media
        status_text = "failed"
    else:
        total = 0
        current = 0
        status_text = "idle"
    latest_job_payload = None
    if latest_job:
        latest_job_payload = {
            "id": latest_job.id,
            "project_id": latest_job.project_id,
            "media_id": latest_job.media_id,
            "job_type": latest_job.job_type,
            "status": latest_job.status,
            "progress": latest_job.progress,
            "message": latest_job.message,
            "error": latest_job.error,
            "created_at": latest_job.created_at.isoformat() if latest_job.created_at else "",
            "updated_at": latest_job.updated_at.isoformat() if latest_job.updated_at else "",
        }
    return {
        "status": status_text,
        "current": current,
        "total": total,
        "indexed_targets": indexed_targets,
        "media_total": media_total,
        "indexed_media": indexed_media,
        "processing_media": processing_media,
        "failed_media": failed_media,
        "latest_job": latest_job_payload,
    }


def _camera_status_payload(project_id: int, project, db: Session, *, light: bool = False) -> dict:
    payload = camera_stream_manager.status(project_id)
    latest_frame_url = f"/gallery/project_{project_id}/camera/latest.jpg"
    latest_frame_path = os.path.join(BASE_GALLERY_DIR, f"project_{project_id}", "camera", "latest.jpg")
    if not payload.get("latest_frame_url") and os.path.exists(latest_frame_path):
        payload["latest_frame_url"] = latest_frame_url
    if not payload.get("latest_frame_version") and os.path.exists(latest_frame_path):
        payload["latest_frame_version"] = int(os.path.getmtime(latest_frame_path))
    if not light:
        target_count = (
            db.query(database.DetectionTarget)
            .filter(
                database.DetectionTarget.project_id == project_id,
                database.DetectionTarget.camera_id.isnot(None),
            )
            .count()
        )
        latest_recording = (
            db.query(database.MediaFile)
            .filter(
                database.MediaFile.project_id == project_id,
                database.MediaFile.media_type == "camera",
                database.MediaFile.size_bytes > 0,
                database.MediaFile.content_type.like("video/%"),
            )
            .order_by(database.MediaFile.created_at.desc())
            .first()
        )
        if latest_recording and not payload.get("recording_url"):
            payload["recording_url"] = _media_download_url(latest_recording)
            payload["recording_filename"] = latest_recording.filename
            payload["recording_media_id"] = latest_recording.id
    if not light:
        payload["indexed_targets"] = max(int(payload.get("indexed_targets") or 0), target_count)
    payload["project_type"] = project.project_type
    payload["camera_source"] = payload.get("camera_source") or project.camera_source or "0"
    payload = _merge_central_camera_status(payload, camera_source=payload["camera_source"])
    if payload.get("recording_url") and payload.get("media_id"):
        payload["recording_url"] = signed_asset_url(
            f"/api/v1/projects/{project_id}/media/{payload['media_id']}/content"
        )
    return payload


@router.post("/api/v1/projects/{project_id}/camera/start")
def v1_start_project_camera(
    project_id: int,
    payload: CameraStartRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "write")
    if project.project_type != "camera":
        raise HTTPException(status_code=400, detail="当前项目不是实时相机项目")
    camera_source = (payload.camera_source or project.camera_source or "0").strip() or "0"
    project.camera_source = camera_source
    db.commit()
    try:
        camera_stream_manager.start(
            project_id=project_id,
            username=username,
            camera_source=camera_source,
            sample_interval_seconds=payload.sample_interval_seconds,
            min_confidence=payload.min_confidence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _camera_status_payload(project_id, project, db)


@router.post("/api/v1/projects/{project_id}/camera/stop")
def v1_stop_project_camera(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "write")
    if project.project_type != "camera":
        raise HTTPException(status_code=400, detail="当前项目不是实时相机项目")
    camera_stream_manager.stop(project_id)
    _audit(db, username, "camera.stopped", "project", str(project_id), "")
    db.commit()
    return _camera_status_payload(project_id, project, db)


@router.get("/api/v1/projects/{project_id}/camera/status")
def v1_project_camera_status(
    project_id: int,
    light: bool = Query(False),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db)
    if project.project_type != "camera":
        raise HTTPException(status_code=400, detail="当前项目不是实时相机项目")
    return _camera_status_payload(project_id, project, db, light=light)


@router.get("/api/v1/projects/{project_id}/camera/preview-stream")
def core_preview_stream_url(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    project = _ensure_project_access(project_id, username, db)
    if project.project_type != "camera":
        raise HTTPException(status_code=400, detail="当前项目不是实时相机项目")
    return {"url": signed_asset_url(f"/api/v1/projects/{project_id}/camera/preview.mjpeg"),
            "expires_in": get_settings().asset_url_ttl_seconds}


@router.get("/api/v1/projects/{project_id}/camera/preview.mjpeg")
def core_preview_stream(project_id: int, username=Depends(get_core_asset_username), db: Session = Depends(database.get_db)):
    from app.services.camera_preview import preview_response
    project = (_ensure_project_access(project_id, username, db) if username is not None
               else db.get(database.Project, project_id))
    if project is None or project.project_type != "camera":
        raise HTTPException(status_code=404, detail="相机项目不存在")
    source = project.camera_source or "0"
    db.close()  # Do not retain a database connection for the stream lifetime.
    return preview_response(source)


@router.get("/api/v1/projects/{project_id}/preview")
def v1_project_preview(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
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
            signed_asset_url(f"/api/v1/projects/{project_id}/targets/{target.id}/thumbnail")
            for target in targets
        ]
    }


@router.get("/api/v1/projects/{project_id}/media")
def v1_list_project_media(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    media_query = db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id)
    total = media_query.count()
    media_rows = (
        media_query.order_by(database.MediaFile.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    media_ids = [media.id for media in media_rows]
    media_by_id = {media.id: media for media in media_rows}
    targets_by_media = {media_id: [] for media_id in media_ids}
    if media_ids:
        target_rows = (
            db.query(database.DetectionTarget)
            .filter(database.DetectionTarget.media_id.in_(media_ids))
            .order_by(database.DetectionTarget.created_at.asc())
            .all()
        )
        for target in target_rows:
            media = media_by_id.get(target.media_id)
            targets_by_media.setdefault(target.media_id, []).append(
                {
                    "id": target.id,
                    "media_id": target.media_id,
                    "thumbnail_url": signed_asset_url(
                        f"/api/v1/projects/{project_id}/targets/{target.id}/thumbnail"
                    ),
                    "source_info": target.source_info,
                    "timestamp": target.timestamp,
                    "video_seek_seconds": _target_video_seek_seconds(target, media),
                    "created_at": target.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

    media_items = []
    for media in media_rows:
        targets = targets_by_media.get(media.id, [])
        original_url = _media_download_url(media)
        thumbnail_url = original_url if media.media_type == "image" else (targets[0]["thumbnail_url"] if targets else "")
        media_items.append(
            {
                "id": media.id,
                "project_id": media.project_id,
                "filename": media.filename,
                "media_type": media.media_type,
                "content_type": media.content_type,
                "size_bytes": media.size_bytes,
                "status": media.status,
                "duplicate_of_media_id": media.duplicate_of_media_id,
                "uploader_username": media.uploader_username,
                "created_at": media.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "object_url": original_url,
                "thumbnail_url": thumbnail_url,
                "target_count": len(targets),
                "targets": targets,
            }
        )
    return {
        "status": "success",
        "media": media_items,
        "pagination": _pagination_payload(page, page_size, total),
    }


@router.delete("/api/v1/projects/{project_id}/media")
def v1_clear_project_media(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    _ensure_project_access(project_id, username, db, "write")
    camera_stream_manager.stop(project_id)
    media_rows = db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id).all()
    deleted_count = len(media_rows)
    _delete_media_assets(db, media_rows)
    if media_rows:
        _safe_delete_vectors_by_project(project_id)
    db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id).delete(synchronize_session=False)
    project_gallery_dir = os.path.join(BASE_GALLERY_DIR, f"project_{project_id}")
    if os.path.isdir(project_gallery_dir):
        shutil.rmtree(project_gallery_dir)
    _audit(db, username, "media.cleared", "project", str(project_id), f"deleted {deleted_count} media")
    db.commit()
    return {"status": "success", "deleted": deleted_count}


@router.get("/api/v1/projects/{project_id}/jobs")
def v1_list_project_jobs(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    _ensure_project_access(project_id, username, db)
    jobs = (
        db.query(database.ProcessingJob)
        .filter(database.ProcessingJob.project_id == project_id)
        .order_by(database.ProcessingJob.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "status": "success",
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


@router.get("/api/v1/projects/{project_id}/audit")
def v1_list_project_audit(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    _ensure_project_access(project_id, username, db)
    project_resource_id = str(project_id)
    media_ids = [
        str(row[0])
        for row in db.query(database.MediaFile.id)
        .filter(database.MediaFile.project_id == project_id)
        .all()
    ]
    search_task_ids = [
        str(row[0])
        for row in db.query(database.SearchTask.id)
        .filter(database.SearchTask.project_id == project_id)
        .all()
    ]
    tourist_session_ids = [
        str(row[0])
        for row in db.query(database.TouristSession.id)
        .filter(database.TouristSession.project_id == project_id)
        .all()
    ]
    resource_ids = [project_resource_id] + media_ids + search_task_ids + tourist_session_ids
    logs = (
        db.query(database.AuditLog)
        .filter(
            database.AuditLog.resource_type.in_(["project", "media", "search_task", "tourist_session"]),
            database.AuditLog.resource_id.in_(resource_ids),
        )
        .order_by(database.AuditLog.created_at.desc())
        .limit(200)
        .all()
    )
    return {
        "status": "success",
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


@router.get("/api/v1/media/{media_id}")
def v1_get_media(media_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    media = db.query(database.MediaFile).filter(database.MediaFile.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="媒体不存在")
    _ensure_project_access(media.project_id, username, db, "read")
    return {
        "id": media.id,
        "project_id": media.project_id,
        "filename": media.filename,
        "object_key": media.object_key,
        "content_type": media.content_type,
        "size_bytes": media.size_bytes,
        "sha256": media.sha256,
        "duplicate_of_media_id": media.duplicate_of_media_id,
        "media_type": media.media_type,
        "status": media.status,
        "created_at": media.created_at,
    }


@router.get("/api/v1/projects/{project_id}/media/{media_id}/preview-video")
def v1_preview_project_video(
    project_id: int,
    media_id: int,
    start_seconds: float = Query(0.0, ge=0.0),
    duration_seconds: float = Query(8.0, ge=1.0, le=60.0),
    full: bool = Query(False),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    media = (
        db.query(database.MediaFile)
        .filter(database.MediaFile.id == media_id, database.MediaFile.project_id == project_id)
        .first()
    )
    if not media:
        raise HTTPException(status_code=404, detail="媒体不存在")
    payload = _video_preview_payload(
        media,
        project_id,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        full=full,
    )
    preview_object_key = payload.pop("preview_object_key", "")
    original_url = signed_asset_url(
        f"/api/v1/projects/{project_id}/media/{media_id}/content"
    )
    payload["original_url"] = original_url
    payload["preview_url"] = (
        signed_asset_url(
            f"/api/v1/projects/{project_id}/media/{media_id}/previews/{Path(preview_object_key).name}"
        )
        if preview_object_key
        else original_url
    )
    return payload


@router.get("/api/v1/projects/{project_id}/media/{media_id}/previews/{filename}")
def v1_get_media_preview_content(
    project_id: int,
    media_id: int,
    filename: str,
    username: str | None = Depends(get_core_asset_username),
    db: Session = Depends(database.get_db),
):
    if username is not None:
        _ensure_project_access(project_id, username, db)
    media_exists = (
        db.query(database.MediaFile.id)
        .filter(database.MediaFile.id == media_id, database.MediaFile.project_id == project_id)
        .first()
    )
    if not media_exists or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="视频预览不存在")
    object_key = f"projects/{project_id}/media/{media_id}/previews/{filename}"
    return deliver_object(object_key, filename=filename, content_type="video/mp4")


@router.delete("/api/v1/media/{media_id}")
def v1_delete_media(media_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    media = db.query(database.MediaFile).filter(database.MediaFile.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="媒体不存在")
    _ensure_project_access(media.project_id, username, db, "write")
    media_rows = [media]
    if media.duplicate_of_media_id is None:
        media_rows.extend(
            db.query(database.MediaFile)
            .filter(database.MediaFile.duplicate_of_media_id == media.id)
            .all()
        )
    for row in media_rows:
        if row.media_type == "camera" and camera_stream_manager.running_media_id(row.project_id) == row.id:
            camera_stream_manager.stop(row.project_id)
        _safe_delete_vectors_by_media(row.id)
    _delete_media_assets(db, media_rows)
    deleted_ids = [row.id for row in media_rows]
    for row in media_rows:
        db.delete(row)
    _audit(db, username, "media.deleted", "media", str(media_id), json.dumps({"filename": media.filename, "deleted_ids": deleted_ids}, ensure_ascii=False))
    db.commit()
    return {"status": "success", "deleted": deleted_ids}
