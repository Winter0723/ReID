import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

import database
import scenic_database
from app.api.auth_dependencies import get_current_username, require_roles
from app.core.config import get_settings
from app.core.observability import error_tracker
from app.services.camera_stream import list_camera_devices
from app.services.job_manager import job_manager
from app.services.model_runtime import DEVICE, model_status
from app.services.observability_service import (
    _disk_status_payload,
    _job_observability_payload,
    _prometheus_payload,
    _service_observability_payload,
)

router = APIRouter()

@router.get("/api/v1/healthz")
def v1_healthz():
    settings = get_settings()
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@router.get("/api/v1/readyz")
def v1_readyz():
    payload = _service_observability_payload()
    models = payload.get("models", {})
    embedding = models.get("embedding", {})
    detection = models.get("detection", {})
    try:
        scenic_database.check_database_connection()
        scenic_database_ready = True
    except Exception:
        scenic_database_ready = False
    database_ready = bool(payload.get("database", {}).get("ready"))
    queue = payload.get("queue", {})
    queue_ready = bool(queue.get("ready", queue.get("redis_ready")))
    queue_mode = str(queue.get("queue_mode") or ("redis" if queue.get("redis_ready") else "unavailable"))
    vector_store_ready = bool(payload.get("vector_store", {}).get("ready"))
    disk_ready = not any(item.get("alert") for item in payload.get("disk", []))
    models_ready = bool(models.get("ready"))
    degraded_reasons = list(payload.get("degraded_reasons") or [])
    if not scenic_database_ready:
        degraded_reasons.append("scenic_database")
    status = str(payload.get("status", "warming"))
    if not scenic_database_ready:
        status = "unhealthy"
    return {
        "status": status,
        "clip_loaded": bool(
            embedding.get("backend") in {"clip", "clipreid"} and embedding.get("loaded")
        ),
        "yolo_loaded": bool(detection.get("backend") == "yolo" and detection.get("loaded")),
        "models_ready": models_ready,
        "models": models.get("models", []),
        "model_cache": models.get("model_cache", []),
        "database_ready": database_ready,
        "scenic_database_ready": scenic_database_ready,
        "queue_ready": queue_ready,
        "queue_mode": queue_mode,
        "degraded_reasons": degraded_reasons,
        "vector_store_ready": vector_store_ready,
        "disk_ready": disk_ready,
        "device": str(DEVICE),
    }


@router.get("/api/v1/database/status")
def v1_database_status(_username: str = Depends(get_current_username)):
    database.check_database_connection()
    return {"status": "ok", **database.get_database_info()}


@router.get("/api/v1/cameras")
def v1_list_cameras(refresh: bool = Query(False), username: str = Depends(get_current_username)):
    from app.services.camera_policy import public_camera_devices
    devices = public_camera_devices(list_camera_devices(force=refresh))
    return {
        "status": "success",
        "devices": devices,
        "message": "此列表仅显示管理员授权的设备。远程摄像头请先配置 CAMERA_SOURCES，再选择对应设备 ID；Docker 本机摄像头需要设备映射。",
    }


@router.get("/api/v1/metrics")
def v1_metrics(db: Session = Depends(database.get_db), username: str = Depends(get_current_username)):
    user = db.query(database.User).filter(database.User.username == username).first()
    if user.role == "admin":
        project_ids = [row[0] for row in db.query(database.Project.id).all()]
    else:
        project_ids = [
            row[0]
            for row in db.query(database.ProjectMember.project_id)
            .filter(database.ProjectMember.username == username)
            .all()
        ]
    return {
        "projects": len(project_ids),
        "media_files": db.query(database.MediaFile).filter(database.MediaFile.project_id.in_(project_ids)).count() if project_ids else 0,
        "indexed_targets": db.query(database.DetectionTarget).filter(database.DetectionTarget.project_id.in_(project_ids)).count() if project_ids else 0,
        "jobs": _job_observability_payload(db, project_ids),
        "queue": job_manager.stats(),
        "models": model_status(),
        "disk": _disk_status_payload(),
        "errors": error_tracker.summary() if user.role == "admin" else None,
    }


@router.get("/api/v1/observability")
def v1_observability(db: Session = Depends(database.get_db), _user: database.User = Depends(require_roles("admin"))):
    return _service_observability_payload(db)


@router.get("/metrics")
def prometheus_metrics(request: Request, db: Session = Depends(database.get_db)):
    metrics_token = get_settings().metrics_token
    authorization = request.headers.get("Authorization", "")
    supplied_token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if metrics_token and not hmac.compare_digest(supplied_token, metrics_token):
        raise HTTPException(status_code=401, detail="Invalid metrics token")
    return Response(content=_prometheus_payload(db), media_type="text/plain; version=0.0.4; charset=utf-8")
