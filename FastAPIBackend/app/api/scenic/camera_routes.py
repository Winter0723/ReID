"""Scenic camera discovery, control, snapshot, and simulation endpoints."""

import datetime
import io
import json
import os
from pathlib import Path

import cv2

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import scenic_database as database
from app.api.auth_dependencies import (
    get_scenic_asset_username,
    get_scenic_current_username as get_current_username,
)
from app.api.scenic.service_adapters import (
    audit as _audit,
    create_scenic_media_record as _create_scenic_media_record,
    create_scenic_processing_job as _create_scenic_processing_job,
    enqueue_scenic_job_or_503 as _enqueue_job_or_503,
    ensure_access as _ensure_project_access,
)
from app.api.scenic.system_routes import (
    _camera_device_is_iphone,
    _camera_device_is_virtual,
    _camera_device_label,
    _camera_device_source,
    _camera_point_payload,
    _camera_wall_point_payload,
    _camera_wall_payload,
    _gallery_url_path,
    _ordered_camera_devices,
    _scenic_camera_status_payload,
    _scenic_camera_stream_key,
    _scenic_storage_project_id,
    _stop_all_scenic_project_streams,
    _stop_scenic_stream_keys,
    _unique_camera_point_name,
)
from app.schemas import CameraStartRequest, ScenicCameraPointCreate, ScenicCameraPointUpdate
from app.services.asset_delivery import deliver_object, signed_asset_url
from app.services import camera_bridge
from app.services.camera_stream import list_camera_devices, scenic_camera_stream_manager as camera_stream_manager
from app.services.central_camera_service import CameraSourceBusyError, find_camera_service, get_camera_service
from app.services.media_service import BASE_GALLERY_DIR, delete_storage_objects as _delete_storage_objects
from app.services.schema_service import schema_dict as _schema_dict
from app.services.storage import UploadTooLargeError, storage


router = APIRouter()


def _preview_point(db, project_id, point_id):
    point = db.query(database.ScenicCameraPoint).filter_by(
        id=point_id, project_id=project_id, is_active=True).first()
    if point is None:
        raise HTTPException(status_code=404, detail="所选相机不存在或未启用")
    return point


@router.get("/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/preview-stream")
def scenic_preview_stream_url(project_id: int, point_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    from app.core.config import get_settings
    _ensure_project_access(project_id, username, db)
    _preview_point(db, project_id, point_id)
    return {"url": signed_asset_url(f"/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/preview.mjpeg"),
            "expires_in": get_settings().asset_url_ttl_seconds}


@router.get("/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/preview.mjpeg")
def scenic_preview_stream(project_id: int, point_id: int, username=Depends(get_scenic_asset_username), db: Session = Depends(database.get_db)):
    from app.services.camera_preview import preview_response
    if username is not None:
        _ensure_project_access(project_id, username, db)
    point = _preview_point(db, project_id, point_id)
    source = point.camera_source or point.camera_id or "0"
    db.close()
    return preview_response(source)


def scenic_list_cameras(refresh: bool = Query(False), username: str = Depends(get_current_username)):
    from app.services.camera_policy import public_camera_devices
    devices = public_camera_devices([device for device in list_camera_devices(force=refresh) if not _camera_device_is_virtual(device)])
    devices.sort(key=lambda item: (0 if _camera_device_is_iphone(item) else 1, str(item.get("source", ""))))
    return {
        "status": "success",
        "devices": devices,
        "message": "已扫描 Iriun、USB 和系统摄像头；新增设备后可点击刷新设备重新扫描。",
    }


def v1_list_camera_points(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    points = (
        db.query(database.ScenicCameraPoint)
        .filter(database.ScenicCameraPoint.project_id == project_id)
        .order_by(database.ScenicCameraPoint.role.asc(), database.ScenicCameraPoint.created_at.asc())
        .all()
    )
    return {"status": "success", "camera_points": [_camera_point_payload(point) for point in camera_bridge.scenic_points_for_environment(points)]}


def v1_discover_camera_points(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "manage")
    devices = _ordered_camera_devices()
    from app.services.camera_hotplug import discover_points
    created_count = discover_points(db, project_id, devices)
    _audit(db, username, "scenic.camera_points_discovered", "project", str(project_id), f"created={created_count}")
    db.commit()
    points = (
        db.query(database.ScenicCameraPoint)
        .filter(database.ScenicCameraPoint.project_id == project_id)
        .order_by(database.ScenicCameraPoint.created_at.asc())
        .all()
    )
    return {
        "status": "success",
        "created": created_count,
        "camera_points": [_camera_point_payload(point) for point in camera_bridge.scenic_points_for_environment(points)],
    }


def v1_create_camera_point(
    project_id: int,
    payload: ScenicCameraPointCreate,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "manage")
    camera_source = (payload.camera_source or "0").strip() or "0"
    camera_id = (payload.camera_id or camera_source).strip() or camera_source
    point = database.ScenicCameraPoint(
        project_id=project_id,
        name=payload.name.strip(),
        camera_source=camera_source,
        camera_id=camera_id,
        location=payload.location.strip(),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(point)
    _audit(
        db,
        username,
        "scenic.camera_point_created",
        "project",
        str(project_id),
        json.dumps(_schema_dict(payload), ensure_ascii=False),
    )
    db.commit()
    db.refresh(point)
    return _camera_point_payload(point)


def v1_scenic_camera_wall(
    project_id: int,
    light: bool = Query(False),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    wall = _camera_wall_payload(project_id, db, light=light)
    return {
        "status": "success",
        "project_id": project_id,
        "active_count": len(wall),
        "running_count": sum(1 for item in wall if item.get("running")),
        "camera_wall": wall,
    }


def v1_scenic_camera_preview(
    project_id: int,
    point_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "write")
    point = db.query(database.ScenicCameraPoint).filter_by(
        id=point_id, project_id=project_id, is_active=True,
    ).first()
    if point is None:
        raise HTTPException(status_code=404, detail="所选相机不存在或未启用")
    source = (point.camera_source or point.camera_id or "0").strip() or "0"
    service = get_camera_service(source)
    service.preview()
    return {"status": "success", "camera": _camera_wall_point_payload(point, db, light=True)}


def v1_start_all_scenic_camera_points(
    project_id: int,
    payload: CameraStartRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "write")
    from app.services.camera_hotplug import resume_project
    resume_project(project_id)
    preferred_source = (project.camera_source or payload.camera_source or "0").strip() or "0"
    points = (
        db.query(database.ScenicCameraPoint)
        .filter(database.ScenicCameraPoint.project_id == project_id, database.ScenicCameraPoint.is_active.is_(True))
        .order_by(database.ScenicCameraPoint.created_at.asc())
        .all()
    )
    points = camera_bridge.scenic_points_for_environment(points)
    if not points:
        configured_count = (
            db.query(database.ScenicCameraPoint)
            .filter(database.ScenicCameraPoint.project_id == project_id)
            .count()
        )
        if configured_count:
            raise HTTPException(status_code=400, detail="没有已启用的摄像头点位")
        if camera_bridge.enabled():
            available_devices = _ordered_camera_devices()
            if not available_devices:
                raise HTTPException(status_code=400, detail="未发现可用本机摄像头，请先连接设备并启动采集代理")
            preferred_source = _camera_device_source(available_devices[0])
        point = database.ScenicCameraPoint(
            project_id=project_id,
            name=_unique_camera_point_name(db, project_id, f"Camera {preferred_source}"),
            camera_source=preferred_source,
            camera_id=preferred_source,
            location="",
            role="entry",
            is_active=True,
        )
        db.add(point)
        db.flush()
        points = [point]
    runtime_points = []
    runtime_sources = set()
    for point in points:
        point_source = (point.camera_source or point.camera_id or preferred_source or "0").strip() or "0"
        if point_source in runtime_sources:
            continue
        runtime_points.append(point)
        runtime_sources.add(point_source)
    if not runtime_points:
        runtime_points = [points[0]]
    first_source = (
        runtime_points[0].camera_source
        or runtime_points[0].camera_id
        or preferred_source
        or payload.camera_source
        or project.camera_source
        or "0"
    ).strip() or "0"
    project.camera_source = first_source
    db.commit()

    newly_started_keys: list[int] = []
    for point in runtime_points:
        camera_source = (
            point.camera_source
            or point.camera_id
            or preferred_source
            or payload.camera_source
            or project.camera_source
            or "0"
        ).strip() or "0"
        stream_key = _scenic_camera_stream_key(project_id, point.id)
        was_running = camera_stream_manager.is_running(project_id, stream_key=stream_key)
        try:
            camera_stream_manager.start(
                project_id=project_id,
                username=username,
                camera_source=camera_source,
                sample_interval_seconds=payload.sample_interval_seconds,
                min_confidence=payload.min_confidence,
                stream_key=stream_key,
            )
        except CameraSourceBusyError as exc:
            _stop_scenic_stream_keys(project_id, newly_started_keys)
            raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"}) from exc
        except ValueError as exc:
            _stop_scenic_stream_keys(project_id, newly_started_keys)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            _stop_scenic_stream_keys(project_id, newly_started_keys)
            raise
        if not was_running:
            newly_started_keys.append(stream_key)
    _audit(
        db,
        username,
        "scenic.camera_wall_started",
        "project",
        str(project_id),
        f"points={len(runtime_points)}; sources={','.join(str(point.camera_source or point.camera_id) for point in runtime_points)}",
    )
    db.commit()
    return {
        "status": "success",
        "project_id": project_id,
        "camera_wall": _camera_wall_payload(project_id, db),
        "camera_status": _scenic_camera_status_payload(project_id, project, db),
    }


def v1_stop_all_scenic_camera_points(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "write")
    from app.services.camera_hotplug import pause_project
    pause_project(project_id)
    stopped_streams = _stop_all_scenic_project_streams(project_id, db)
    _audit(db, username, "scenic.camera_wall_stopped", "project", str(project_id), f"streams={stopped_streams}")
    db.commit()
    return {
        "status": "success",
        "message": "景区监控墙已停止",
        "project_id": project_id,
        "camera_wall": _camera_wall_payload(project_id, db),
        "camera_status": _scenic_camera_status_payload(project_id, project, db),
    }


def v1_start_scenic_camera(
    project_id: int,
    payload: CameraStartRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "write")
    from app.services.camera_hotplug import resume_project
    resume_project(project_id)
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
    _audit(db, username, "scenic.camera_started", "project", str(project_id), f"source={camera_source}")
    db.commit()
    return _scenic_camera_status_payload(project_id, project, db)


def v1_stop_scenic_camera(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db, "write")
    from app.services.camera_hotplug import pause_project
    pause_project(project_id)
    stopped_streams = _stop_all_scenic_project_streams(project_id, db)
    _audit(db, username, "scenic.camera_stopped", "project", str(project_id), f"streams={stopped_streams}")
    db.commit()
    status = _scenic_camera_status_payload(project_id, project, db)
    status["message"] = "景区相机已停止"
    return status


def v1_scenic_camera_status(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    project = _ensure_project_access(project_id, username, db)
    return _scenic_camera_status_payload(project_id, project, db)


def v1_scenic_camera_snapshot(
    project_id: int,
    point_id: int | None = Query(None, gt=0),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "write")
    latest_frame_path = os.path.join(BASE_GALLERY_DIR, f"scenic_project_{project_id}", "camera", "latest.jpg")
    snapshot_bytes = None
    if point_id is not None:
        point = db.query(database.ScenicCameraPoint).filter_by(id=point_id, project_id=project_id, is_active=True).first()
        if not point:
            raise HTTPException(404, "入口相机不存在")
        camera_source = (point.camera_source or point.camera_id or "0").strip() or "0"
        service = find_camera_service(camera_source)
        if service is not None:
            frame, _frame_time, _frame_index = service.latest_frame()
            if not service.status().get("running") or frame is None:
                raise HTTPException(400, "所选相机尚未产生画面，请检查相机状态")
            encoded, image = cv2.imencode(".jpg", frame)
            if not encoded:
                raise HTTPException(503, "相机画面编码失败，请重试")
            snapshot_bytes = image.tobytes()
        else:
            wall = _camera_wall_point_payload(point, db, light=True)
            latest_frame_path = _gallery_url_path(wall.get("latest_frame_url", ""))
            if not wall.get("running") or not latest_frame_path or not os.path.isfile(latest_frame_path):
                raise HTTPException(400, "所选相机尚未产生画面，请检查相机状态")
    if snapshot_bytes is None and not os.path.exists(latest_frame_path):
        for point in _camera_wall_payload(project_id, db):
            candidate_path = _gallery_url_path(point.get("latest_frame_url") or "")
            if candidate_path and os.path.exists(candidate_path):
                latest_frame_path = candidate_path
                break
        else:
            raise HTTPException(status_code=400, detail="相机尚未产生画面，请稍后再试或检查相机状态")

    filename = f"snapshot_{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S')}.jpg"
    with io.BytesIO(snapshot_bytes) if snapshot_bytes is not None else open(latest_frame_path, "rb") as source:
        stored = storage.save_upload(
            _scenic_storage_project_id(project_id),
            filename,
            source,
            category="tourists/entry",
        )
    return {
        "status": "success",
        "snapshot_url": signed_asset_url(
            f"/api/v1/scenic/projects/{project_id}/snapshots/{Path(stored.object_key).name}"
        ),
        "snapshot_filename": stored.filename,
        "snapshot_object_key": stored.object_key,
    }


def v1_scenic_camera_snapshot_content(
    project_id: int,
    object_name: str,
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    if username is not None:
        _ensure_project_access(project_id, username, db)
    if Path(object_name).name != object_name:
        raise HTTPException(status_code=404, detail="抓拍图片不存在")
    object_key = f"projects/{_scenic_storage_project_id(project_id)}/tourists/entry/{object_name}"
    return deliver_object(object_key, content_type="image/jpeg")


def v1_update_camera_point(
    point_id: int,
    payload: ScenicCameraPointUpdate,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    point = db.query(database.ScenicCameraPoint).filter(database.ScenicCameraPoint.id == point_id).first()
    if not point:
        raise HTTPException(status_code=404, detail="摄像头点位不存在")
    _ensure_project_access(point.project_id, username, db, "manage")
    updates = _schema_dict(payload, exclude_none=True)
    requires_stream_stop = updates.get("is_active") is False or any(
        key in updates for key in ("camera_source", "camera_id")
    )
    for key, value in updates.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(point, key, value)
    if not point.camera_id:
        point.camera_id = point.camera_source or "0"
    _audit(
        db,
        username,
        "scenic.camera_point_updated",
        "project",
        str(point.project_id),
        json.dumps(updates, ensure_ascii=False),
    )
    db.commit()
    if requires_stream_stop:
        _stop_scenic_stream_keys(
            point.project_id,
            [_scenic_camera_stream_key(point.project_id, point.id)],
        )
    db.refresh(point)
    return _camera_point_payload(point)


def v1_upload_scenic_simulation_video(
    project_id: int,
    point_id: int,
    file: UploadFile = File(...),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "write")
    point = db.query(database.ScenicCameraPoint).filter(
        database.ScenicCameraPoint.id == point_id,
        database.ScenicCameraPoint.project_id == project_id,
    ).first()
    if not point:
        raise HTTPException(status_code=404, detail="摄像头点位不存在")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".mp4", ".avi", ".mov", ".mkv"}:
        raise HTTPException(status_code=400, detail="请选择 MP4、AVI、MOV 或 MKV 格式的录像")

    try:
        stored = storage.save_upload(
            _scenic_storage_project_id(project_id),
            file.filename,
            file.file,
            category=f"simulation/{point_id}",
        )
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))

    try:
        media = _create_scenic_media_record(
            db,
            project_id=project_id,
            username=username,
            filename=file.filename,
            media_type="camera",
            stored=stored,
        )
        job = _create_scenic_processing_job(
            db,
            project_id,
            media.id,
            "media_index",
            "录像已提交，正在等待处理",
        )
        _audit(
            db,
            username,
            "scenic.simulation_video_uploaded",
            "project",
            str(project_id),
            f"point_id={point_id}, media_id={media.id}",
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _delete_storage_objects([stored.object_key])
        raise HTTPException(status_code=409, detail="该录像已上传，请勿重复提交") from exc
    except Exception:
        db.rollback()
        _delete_storage_objects([stored.object_key])
        raise

    _enqueue_job_or_503(
        db,
        job.id,
        "app.workers.tasks.process_scenic_media_index",
        job.id,
        media.id,
        project_id,
        stored.object_key,
        "video",
        username,
        point.camera_id or point.camera_source or "0",
        project_id=project_id,
        media_id=media.id,
    )
    return {"status": "accepted", "media_id": media.id, "job_id": job.id}

router.add_api_route("/api/v1/scenic/cameras", scenic_list_cameras, methods=["GET"])
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-points",
    v1_list_camera_points,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-points/discover",
    v1_discover_camera_points,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-points",
    v1_create_camera_point,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-wall",
    v1_scenic_camera_wall,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/preview",
    v1_scenic_camera_preview,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-points/start-all",
    v1_start_all_scenic_camera_points,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-points/stop-all",
    v1_stop_all_scenic_camera_points,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera/start",
    v1_start_scenic_camera,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera/stop",
    v1_stop_scenic_camera,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera/status",
    v1_scenic_camera_status,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera/snapshot",
    v1_scenic_camera_snapshot,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/snapshots/{object_name}",
    v1_scenic_camera_snapshot_content,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/camera-points/{point_id}",
    v1_update_camera_point,
    methods=["PATCH"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/simulate-video",
    v1_upload_scenic_simulation_video,
    methods=["POST"],
)
