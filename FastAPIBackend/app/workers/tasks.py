from contextlib import ExitStack
from app.services.project_models import project_context
import datetime
import json
import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import database
import scenic_database
from app.core.config import get_settings
from app.services.audit_service import add_audit_log
from app.services.job_manager import job_manager
from app.services.ffmpeg import ffmpeg_executable
from app.services.camera_stream import camera_stream_manager, scenic_camera_stream_manager
from app.services.model_runtime import clip_image_feature, detect_people
from app.services.persistence_service import encode_image_path_as_source as _encode_image_path_as_source
from app.services.storage import storage
from app.services.storage import S3ObjectStorage
from filelock import FileLock
from app.services.task_service import sync_job_to_db
from app.services.vector_store import VectorPoint, scenic_vector_store, vector_store

BASE_GALLERY_DIR = str(get_settings().gallery_dir)
logger = logging.getLogger(__name__)


def publish_camera_recording(job_id: str, media_id: int, domain: str):
    """Publish immutable camera segments without blocking the API or ingestion."""
    db_module = scenic_database if domain == "scenic" else database
    root = get_settings().object_storage_dir.resolve()
    with FileLock(str(root / f".camera-publish-{domain}-{media_id}.lock"), timeout=60):
        with db_module.SessionLocal() as db:
            media = db.get(db_module.MediaFile, media_id)
            job = db.get(db_module.ProcessingJob, job_id)
            if not media or not job or job.status == "succeeded":
                return
            try:
                if not job_manager.get(job_id):
                    job_manager.create("camera_recording", job.message, job_id=job_id)
                job.status, job.message, job.progress = "running", "正在上传封存录像", 0.1
                job_manager.update(job_id, status="running", message=job.message, progress=.1, error="")
                db.commit()
                path = (root / media.object_key).resolve()
                if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
                    raise FileNotFoundError("封存录像不存在")
                if isinstance(storage, S3ObjectStorage):
                    storage.publish_path(media.object_key, path, media.content_type or "video/x-msvideo")
                from app.services.scenic_editing import source_sha256
                media.sha256 = source_sha256(path)
                media.size_bytes = path.stat().st_size
                has_targets = db.query(db_module.DetectionTarget.id).filter_by(media_id=media.id).first()
                media.status = "indexed" if has_targets else "stopped"
                job.status, job.progress, job.message, job.error = "succeeded", 1, "录像上传完成", ""
                db.commit()
                job_manager.update(job_id, status="succeeded", progress=1, message=job.message, error="")
            except Exception as exc:
                db.rollback()
                job = db.get(db_module.ProcessingJob, job_id)
                job.status, job.message, job.error = "failed", "录像上传失败，可重试", str(exc)[:2000]
                db.commit()
                job_manager.update(job_id, status="failed", message=job.message, error=job.error)
                raise


def _audit(db, username: str, action: str, resource_type: str, resource_id: str = None, detail: str = ""):
    _audit_for(database, db, username, action, resource_type, resource_id, detail)


def _audit_for(db_module, db, username: str, action: str, resource_type: str, resource_id: str = None, detail: str = ""):
    add_audit_log(db, db_module.AuditLog, username, action, resource_type, resource_id, detail)


def _apply_job_record_to_db(db_module, db, record, project_id: int = None, media_id: int = None):
    sync_job_to_db(
        db,
        db_module.ProcessingJob,
        record,
        project_id,
        media_id,
        commit=False,
    )


def _update_job_state_for(
    db_module,
    db,
    job_id: str,
    *,
    job_type: str,
    project_id: int = None,
    media_id: int = None,
    status: str = None,
    progress: float = None,
    message: str = None,
    error: str = None,
    commit: bool = True,
):
    record = job_manager.update(
        job_id,
        status=status,
        progress=progress,
        message=message,
        error=error,
    )
    if record:
        _apply_job_record_to_db(db_module, db, record, project_id, media_id)
    else:
        db_job = db.query(db_module.ProcessingJob).filter(db_module.ProcessingJob.id == job_id).first()
        if not db_job:
            if project_id is None:
                raise RuntimeError(f"Cannot persist orphan job state: {job_id}")
            db_job = db_module.ProcessingJob(
                id=job_id,
                project_id=project_id,
                media_id=media_id,
                job_type=job_type,
                status=status or "queued",
                progress=max(0.0, min(1.0, progress or 0.0)),
                message=message or "",
                error=error or None,
            )
            db.add(db_job)
        else:
            if status is not None:
                db_job.status = status
            if progress is not None:
                db_job.progress = max(0.0, min(1.0, progress))
            if message is not None:
                db_job.message = message
            if error is not None:
                db_job.error = error or None
    if commit:
        db.commit()
    return record


def _storage_project_id_for(db_module, project_id: int):
    if db_module is scenic_database:
        return f"scenic/{project_id}"
    return project_id


def _delete_storage_keys(object_keys) -> None:
    for object_key in set(filter(None, object_keys)):
        try:
            storage.delete(object_key)
        except Exception:
            logger.warning("Failed to remove worker artifact %s", object_key, exc_info=True)


def _delete_vector_ids(vector_store_instance, vector_ids) -> None:
    ids = list(dict.fromkeys(str(vector_id) for vector_id in vector_ids if vector_id))
    if not ids:
        return
    try:
        vector_store_instance.delete_by_ids(ids)
    except Exception:
        logger.warning("Failed to remove %s staged vectors", len(ids), exc_info=True)


def _upsert_vector_batch(vector_store_instance, points: list[VectorPoint], created_vector_ids: list[str]) -> None:
    if not points:
        return
    # Record ids before the external write. Some backends can partially accept
    # a batch before raising, so failure cleanup must attempt every id.
    created_vector_ids.extend(point.id for point in points)
    vector_store_instance.upsert(points)


def process_media_index(job_id: str, media_id: int, project_id: int, object_key: str, media_type: str, username: str, camera_id: str = None):
    return _process_media_index(database, vector_store, job_id, media_id, project_id, object_key, media_type, username, camera_id)


def process_scenic_media_index(job_id: str, media_id: int, project_id: int, object_key: str, media_type: str, username: str, camera_id: str = None):
    return _process_media_index(scenic_database, scenic_vector_store, job_id, media_id, project_id, object_key, media_type, username, camera_id)


def _process_media_index(db_module, vector_store_instance, job_id: str, media_id: int, project_id: int, object_key: str, media_type: str, username: str, camera_id: str = None):
    db = db_module.SessionLocal()
    model_context = ExitStack()
    created_crop_keys: list[str] = []
    created_vector_ids: list[str] = []
    try:
        model_context.enter_context(project_context(project_id, "scenic" if db_module is scenic_database else "core"))
        media = db.query(db_module.MediaFile).filter(db_module.MediaFile.id == media_id).first()
        if not media or media.project_id != project_id:
            raise RuntimeError("媒体记录不存在")

        stale_targets = db.query(db_module.DetectionTarget).filter(db_module.DetectionTarget.media_id == media_id).all()
        stale_crop_keys = [target.crop_object_key for target in stale_targets if target.crop_object_key]
        stale_vector_ids = [target.vector_id for target in stale_targets if target.vector_id]
        media.status = "processing"
        _update_job_state_for(
            db_module,
            db,
            job_id,
            job_type="media_index",
            project_id=project_id,
            media_id=media_id,
            status="running",
            progress=0.05,
            message="开始处理媒体",
            error="",
            commit=False,
        )
        db.commit()

        source_path = storage.path_for(object_key)
        with tempfile.TemporaryDirectory(prefix=f"myreid-media-{media_id}-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            crop_paths = []
            if media_type == "video":
                cap = cv2.VideoCapture(str(source_path))
                if not cap.isOpened():
                    cap.release()
                    raise RuntimeError("无法打开上传的视频文件")
                try:
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
                    frame_interval = max(1, int(fps))
                    frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    count = 0
                    safe_media_name = Path(media.filename).name
                    while cap.isOpened():
                        ret, frame = cap.read()
                        if not ret:
                            break
                        if count % frame_interval == 0:
                            seconds = count / fps
                            time_str = str(datetime.timedelta(seconds=int(seconds)))
                            time_safe = time_str.replace(":", "-")
                            detections = detect_people(frame)
                            for index, detection in enumerate(detections):
                                person_crop = detection.crop_from(frame)
                                if person_crop is None or person_crop.shape[0] < 60 or person_crop.shape[1] < 30:
                                    continue
                                crop_name = f"[VID]_{safe_media_name}_time_{time_safe}_p{index}.jpg"
                                crop_path = tmp_root / crop_name
                                if not cv2.imwrite(str(crop_path), person_crop):
                                    raise RuntimeError("无法保存视频检测裁剪图")
                                crop_paths.append((crop_path, crop_name, time_str))
                        count += 1
                        if frame_total and count % max(1, frame_interval * 10) == 0:
                            progress = min(0.45, 0.05 + 0.40 * (count / frame_total))
                            _update_job_state_for(
                                db_module,
                                db,
                                job_id,
                                job_type="media_index",
                                project_id=project_id,
                                media_id=media_id,
                                progress=progress,
                                message=f"视频抽帧检测 {count}/{frame_total}",
                            )
                finally:
                    cap.release()
            else:
                crop_name = Path(media.filename).name
                crop_path = tmp_root / crop_name
                with Image.open(source_path) as source_image:
                    source_image.verify()
                with Image.open(source_path) as source_image:
                    source_image.convert("RGB").save(crop_path)
                crop_paths.append((crop_path, crop_name, None))

            if not crop_paths:
                raise RuntimeError("未检测到可用于检索的行人图像")

            prepared_targets = []
            total = len(crop_paths)
            for index, (crop_path, crop_name, time_str) in enumerate(crop_paths, start=1):
                with Image.open(crop_path) as source_image:
                    feature = clip_image_feature(source_image.convert("RGB"))
                crop_object = storage.save_path(_storage_project_id_for(db_module, project_id), crop_path, crop_name, category=f"media/{media_id}/crops")
                created_crop_keys.append(crop_object.object_key)
                vector_id = str(uuid.uuid4())
                source_info = _encode_image_path_as_source(crop_name)
                prepared_targets.append((
                    crop_object,
                    vector_id,
                    source_info,
                    time_str,
                    feature.cpu(),
                ))
                if index % 5 == 0 or index == total:
                    _update_job_state_for(
                        db_module,
                        db,
                        job_id,
                        job_type="media_index",
                        project_id=project_id,
                        media_id=media_id,
                        progress=0.45 + 0.50 * (index / total),
                        message=f"图像特征提取 {index}/{total}",
                    )

            pending_points: list[VectorPoint] = []
            indexed_count = 0
            for prepared_index in range(len(prepared_targets)):
                crop_object, vector_id, source_info, time_str, feature = prepared_targets[prepared_index]
                prepared_targets[prepared_index] = None
                target = db_module.DetectionTarget(
                    project_id=project_id,
                    media_id=media_id,
                    crop_path=crop_object.object_key,
                    crop_object_key=crop_object.object_key,
                    source_info=source_info,
                    timestamp=time_str,
                    vector_id=vector_id,
                    camera_id=camera_id,
                )
                db.add(target)
                db.flush()
                pending_points.append(VectorPoint(
                    id=vector_id,
                    vector=feature,
                    path=crop_object.object_key,
                    metadata={
                        "target_id": target.id,
                        "project_id": project_id,
                        "media_id": media_id,
                        "camera_id": camera_id or target.camera_id,
                        "timestamp": time_str,
                        "source_info": source_info,
                    },
                ))
                indexed_count += 1
                if len(pending_points) >= 256:
                    _upsert_vector_batch(vector_store_instance, pending_points, created_vector_ids)
                    pending_points.clear()

        _upsert_vector_batch(vector_store_instance, pending_points, created_vector_ids)
        for stale_target in stale_targets:
            db.delete(stale_target)
        media.status = "indexed"
        _audit_for(db_module, db, username, "media.indexed", "media", str(media_id), f"indexed {indexed_count} targets")
        _update_job_state_for(
            db_module,
            db,
            job_id,
            job_type="media_index",
            project_id=project_id,
            media_id=media_id,
            status="succeeded",
            progress=1.0,
            message="媒体处理完成",
            error="",
            commit=False,
        )
        db.commit()
        _delete_vector_ids(vector_store_instance, stale_vector_ids)
        _delete_storage_keys(stale_crop_keys)
    except Exception as exc:
        db.rollback()
        _delete_vector_ids(vector_store_instance, created_vector_ids)
        _delete_storage_keys(created_crop_keys)
        try:
            media = db.query(db_module.MediaFile).filter(db_module.MediaFile.id == media_id).first()
            if media:
                media.status = "failed"
            _update_job_state_for(
                db_module,
                db,
                job_id,
                job_type="media_index",
                project_id=project_id,
                media_id=media_id,
                status="failed",
                progress=1.0,
                message="媒体处理失败",
                error=str(exc),
                commit=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist media worker failure for job %s", job_id)
        raise
    finally:
        model_context.close()
        db.close()


def rebuild_project_index(job_id: str, project_id: int, username: str):
    db = database.SessionLocal()
    model_context = ExitStack()
    try:
        model_context.enter_context(project_context(project_id))
        _update_job_state_for(
            database,
            db,
            job_id,
            job_type="index_rebuild",
            project_id=project_id,
            status="running",
            progress=0.05,
            message="开始重建索引",
            error="",
        )
        targets = db.query(database.DetectionTarget).filter(database.DetectionTarget.project_id == project_id).all()
        total = len(targets)
        if total == 0:
            vector_store.delete_by_project(project_id)
            _update_job_state_for(
                database,
                db,
                job_id,
                job_type="index_rebuild",
                project_id=project_id,
                status="succeeded",
                progress=1.0,
                message="项目暂无可重建目标",
                error="",
            )
            return

        points = []
        for index, target in enumerate(targets, start=1):
            crop_ref = target.crop_object_key or target.crop_path
            crop_path = storage.path_for(crop_ref) if crop_ref.startswith("projects/") else Path(crop_ref)
            with Image.open(crop_path) as source_image:
                feature = clip_image_feature(source_image.convert("RGB"))
            points.append(VectorPoint(
                id=target.vector_id,
                vector=feature.cpu(),
                path=crop_ref,
                metadata={
                    "target_id": target.id,
                    "project_id": target.project_id,
                    "media_id": target.media_id,
                    "camera_id": target.camera_id,
                    "timestamp": target.timestamp,
                    "source_info": target.source_info,
                },
            ))
            if index % 10 == 0 or index == total:
                _update_job_state_for(
                    database,
                    db,
                    job_id,
                    job_type="index_rebuild",
                    project_id=project_id,
                    progress=0.05 + 0.90 * (index / total),
                    message=f"索引重建 {index}/{total}",
                )

        vector_store.rebuild_project(project_id, points)
        _audit(db, username, "index.rebuilt", "project", str(project_id), f"rebuilt {len(points)} targets")
        _update_job_state_for(
            database,
            db,
            job_id,
            job_type="index_rebuild",
            project_id=project_id,
            status="succeeded",
            progress=1.0,
            message="索引重建完成",
            error="",
            commit=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        _update_job_state_for(
            database,
            db,
            job_id,
            job_type="index_rebuild",
            project_id=project_id,
            status="failed",
            progress=1.0,
            message="索引重建失败",
            error=str(exc),
        )
        raise
    finally:
        model_context.close()
        db.close()


def generate_scenic_vlog(job_id: str, vlog_job_id: int, username: str):
    _generate_scenic_vlog(database, job_id, vlog_job_id, username)


def generate_scenic_vlog_for_scenic(job_id: str, vlog_job_id: int, username: str):
    _generate_scenic_vlog(scenic_database, job_id, vlog_job_id, username)


def _generate_scenic_vlog(db_module, job_id: str, vlog_job_id: int, username: str):
    db = db_module.SessionLocal()
    created_object_keys: list[str] = []
    replaced_object_keys: list[str] = []
    try:
        vlog = db.query(db_module.ScenicVlogJob).filter(db_module.ScenicVlogJob.id == vlog_job_id).first()
        if not vlog:
            raise RuntimeError("成片任务不存在")
        session = vlog.session
        if vlog.status == "succeeded" and vlog.preview_object_key and vlog.full_object_key:
            _update_job_state_for(
                db_module,
                db,
                job_id,
                job_type="scenic_vlog",
                project_id=session.project_id,
                status="succeeded",
                progress=1.0,
                message="游览视频已生成",
                error="",
            )
            return
        replaced_object_keys = [vlog.preview_object_key, vlog.full_object_key]

        _update_job_state_for(
            db_module,
            db,
            job_id,
            job_type="scenic_vlog",
            project_id=session.project_id,
            status="running",
            progress=0.05,
            message="开始生成游览视频",
            error="",
        )
        segments = (
            db.query(db_module.TouristVisitSegment)
            .filter(
                db_module.TouristVisitSegment.session_id == session.id,
                db_module.TouristVisitSegment.included.is_(True),
            )
            .order_by(db_module.TouristVisitSegment.sort_order.asc(), db_module.TouristVisitSegment.id.asc())
            .all()
        )
        if getattr(vlog, "snapshot_json", ""):
            frozen = json.loads(vlog.snapshot_json)
            if "legacy_segments" in frozen:
                from app.services.scenic_snapshot import thaw_legacy_segments
                segments = thaw_legacy_segments(frozen)
        if not segments:
            raise RuntimeError("没有可用于成片的片段")

        vlog.status = "running"
        session.status = "rendering"
        db.commit()

        with tempfile.TemporaryDirectory(prefix=f"myreid-vlog-{vlog.id}-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            preview_path = tmp_root / f"tourist_{session.id}_preview.mp4"
            full_path = tmp_root / f"tourist_{session.id}_full.mp4"
            duration = _render_scenic_video(segments, preview_path, preview=True)
            duration = max(duration, _render_scenic_video(segments, full_path, preview=False))

            _update_job_state_for(
                db_module,
                db,
                job_id,
                job_type="scenic_vlog",
                project_id=session.project_id,
                progress=0.80,
                message="正在保存游览视频",
            )

            preview_stored = storage.save_path(
                _storage_project_id_for(db_module, session.project_id),
                preview_path,
                preview_path.name,
                category=f"tourists/{session.id}/vlogs",
            )
            created_object_keys.append(preview_stored.object_key)
            full_stored = storage.save_path(
                _storage_project_id_for(db_module, session.project_id),
                full_path,
                full_path.name,
                category=f"tourists/{session.id}/vlogs",
            )
            created_object_keys.append(full_stored.object_key)

        vlog.status = "succeeded"
        vlog.preview_object_key = preview_stored.object_key
        vlog.full_object_key = full_stored.object_key
        vlog.preview_filename = preview_stored.filename
        vlog.full_filename = full_stored.filename
        vlog.segment_count = len(segments)
        vlog.duration_seconds = duration
        vlog.error = ""
        session.status = "vlog_ready"
        _audit_for(db_module, db, username, "scenic.vlog_generated", "tourist_session", str(session.id), f"segments={len(segments)}")
        _update_job_state_for(
            db_module,
            db,
            job_id,
            job_type="scenic_vlog",
            project_id=session.project_id,
            status="succeeded",
            progress=1.0,
            message="游览视频生成完成",
            error="",
            commit=False,
        )
        db.commit()
        new_object_keys = {preview_stored.object_key, full_stored.object_key}
        _delete_storage_keys(key for key in replaced_object_keys if key not in new_object_keys)
    except Exception as exc:
        db.rollback()
        _delete_storage_keys(created_object_keys)
        vlog = db.query(db_module.ScenicVlogJob).filter(db_module.ScenicVlogJob.id == vlog_job_id).first()
        project_id = None
        if vlog:
            vlog.status = "failed"
            vlog.error = str(exc)
            if vlog.session:
                project_id = vlog.session.project_id
                vlog.session.status = "vlog_failed"
        _update_job_state_for(
            db_module,
            db,
            job_id,
            job_type="scenic_vlog",
            project_id=project_id,
            status="failed",
            progress=1.0,
            message="游览视频生成失败",
            error=str(exc),
            commit=False,
        )
        db.commit()
        raise
    finally:
        db.close()


def _render_scenic_video(segments, output_path: Path, *, preview: bool) -> float:
    video_plan = _build_video_render_plan(segments, preview=preview)
    if video_plan:
        duration = sum(item[3] for item in video_plan)
        if _try_render_ffmpeg_video(video_plan, output_path, preview=preview):
            return duration
        if _try_render_opencv_video(video_plan, output_path, preview=preview):
            return duration
        raise RuntimeError("视频片段渲染失败：请安装 ffmpeg/imageio-ffmpeg，或启用 OpenCV H.264 写入器")
    if _segments_have_video_sources(segments):
        raise RuntimeError("未找到可用的源视频片段，无法生成视频片段短片")
    return _render_slideshow_video(segments, output_path, preview=preview)


def _segments_have_video_sources(segments) -> bool:
    for segment in segments:
        media = getattr(segment, "media_file", None)
        media_type = str(getattr(media, "media_type", "") or "")
        content_type = str(getattr(media, "content_type", "") or "").lower()
        if media_type in {"video", "camera"} or content_type.startswith("video/"):
            return True
    return False


def _segment_source_path(segment):
    media = getattr(segment, "media_file", None)
    object_key = str(getattr(segment, "original_media_object_key", "") or "").strip()
    if not object_key and media is not None:
        object_key = str(getattr(media, "object_key", "") or "").strip()
    if object_key:
        source_path = storage.path_for(object_key)
        if source_path.exists():
            return source_path
    media_id = int(getattr(segment, "media_id", 0) or getattr(media, "id", 0) or 0)
    if media_id:
        for manager in (scenic_camera_stream_manager, camera_stream_manager):
            active_path = manager.recording_path_for_media(media_id)
            if active_path and active_path.exists():
                return active_path
    return None


def _build_video_render_plan(segments, *, preview: bool):
    full_plan = []
    for segment in segments:
        media = getattr(segment, "media_file", None)
        if not media:
            return []
        media_type = str(getattr(media, "media_type", "") or "")
        content_type = str(getattr(media, "content_type", "") or "").lower()
        if media_type not in {"video", "camera"} and not content_type.startswith("video/"):
            return []
        source_path = _segment_source_path(segment)
        if source_path is None:
            return []
        start = max(0.0, float(getattr(segment, "start_seconds", 0.0) or 0.0))
        duration = _segment_duration(segment)
        if start > 24 * 3600:
            from app.services.media_service import media_video_seek_seconds

            seek = media_video_seek_seconds(media, getattr(segment, "timestamp", None))
            start = max(0.0, float(seek or 0.0) - 2.0)
            duration = max(3.0, min(8.0, duration if duration < 60.0 else 6.0))
        full_plan.append((
            segment,
            source_path,
            start,
            duration,
        ))
    if not preview:
        return full_plan
    return _build_preview_render_plan(full_plan)


def _build_preview_render_plan(full_plan):
    full_duration = sum(item[3] for item in full_plan)
    if full_duration <= 0:
        return []
    preview_duration = min(full_duration, max(5.0, min(10.0, full_duration * 0.20)))
    if full_duration <= preview_duration + 0.25:
        return full_plan

    window_count = min(5, max(3, int(round(preview_duration / 2.0))))
    window_duration = preview_duration / window_count
    last_start = max(0.0, full_duration - window_duration)
    starts = [last_start * (index / max(1, window_count - 1)) for index in range(window_count)]
    preview_plan = []
    for global_start in starts:
        preview_plan.extend(_timeline_window(full_plan, global_start, window_duration))
    return preview_plan


def _timeline_window(full_plan, global_start: float, duration: float):
    window = []
    cursor = 0.0
    remaining = duration
    for segment, source_path, source_start, segment_duration in full_plan:
        if remaining <= 0.05:
            break
        next_cursor = cursor + segment_duration
        if global_start >= next_cursor:
            cursor = next_cursor
            continue
        local_offset = max(0.0, global_start - cursor)
        take = min(remaining, max(0.0, segment_duration - local_offset))
        if take > 0.05:
            window.append((segment, source_path, source_start + local_offset, take))
            remaining -= take
        cursor = next_cursor
        global_start = cursor
    return window


def _try_render_ffmpeg_video(video_plan, output_path: Path, *, preview: bool) -> bool:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return False

    width, height = (640, 360) if preview else (1280, 720)
    encoder_arg_sets = _ffmpeg_video_encoder_arg_sets(ffmpeg)
    clip_paths = []
    try:
        with tempfile.TemporaryDirectory(prefix="myreid-vlog-ffmpeg-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            for index, (segment, source_path, source_start, duration) in enumerate(video_plan, start=1):
                clip_path = tmp_root / f"clip_{index:03d}.mp4"
                label = "PREVIEW" if preview else "MyReID Scenic Vlog"
                vf = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                    f"drawtext=text='{label}':x=w-tw-24:y=h-th-24:fontsize=24:fontcolor=white:"
                    "box=1:boxcolor=black@0.35"
                )
                base_cmd = [
                    ffmpeg,
                    "-y",
                    "-ss",
                    f"{max(0.0, float(source_start or 0)):.3f}",
                    "-t",
                    f"{max(0.1, float(duration or 0)):.3f}",
                    "-i",
                    str(source_path),
                    "-vf",
                    vf,
                    "-an",
                    "-r",
                    "24",
                ]
                last_error = None
                for encoder_args in encoder_arg_sets:
                    cmd = base_cmd + encoder_args + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(clip_path)]
                    try:
                        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=180)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        clip_path.unlink(missing_ok=True)
                if last_error is not None:
                    raise last_error
                clip_paths.append(clip_path)

            list_path = tmp_root / "clips.txt"
            list_path.write_text(
                "\n".join(f"file '{path.as_posix()}'" for path in clip_paths),
                encoding="utf-8",
            )
            last_error = None
            for encoder_args in encoder_arg_sets:
                concat_cmd = [
                    ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    *encoder_args,
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-an",
                    str(output_path),
                ]
                try:
                    subprocess.run(
                        concat_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                        timeout=180,
                    )
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    output_path.unlink(missing_ok=True)
            if last_error is not None:
                raise last_error
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception:
        output_path.unlink(missing_ok=True)
        return False


_FFMPEG_ENCODER_SUPPORT: dict[tuple[str, str], bool] = {}


def _ffmpeg_supports_encoder(ffmpeg: str, encoder: str) -> bool:
    cache_key = (ffmpeg, encoder)
    if cache_key in _FFMPEG_ENCODER_SUPPORT:
        return _FFMPEG_ENCODER_SUPPORT[cache_key]
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        supported = completed.returncode == 0 and encoder in output
    except Exception:
        supported = False
    _FFMPEG_ENCODER_SUPPORT[cache_key] = supported
    return supported


def _ffmpeg_video_encoder_arg_sets(ffmpeg: str) -> list[list[str]]:
    encoders: list[list[str]] = []
    if _ffmpeg_supports_encoder(ffmpeg, "h264_nvenc"):
        encoders.append(["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"])
    encoders.append(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
    return encoders


def _try_render_opencv_video(video_plan, output_path: Path, *, preview: bool) -> bool:
    width, height = (640, 360) if preview else (1280, 720)
    fps = 24
    writer = _open_opencv_browser_video_writer(output_path, fps, (width, height))
    if writer is None:
        return False

    wrote_frames = 0
    try:
        for index, (segment, source_path, source_start, duration) in enumerate(video_plan, start=1):
            wrote_frames += _write_opencv_clip(
                writer,
                segment,
                source_path,
                source_start,
                duration,
                width,
                height,
                fps,
                preview=preview,
                index=index,
            )
    finally:
        writer.release()
    if wrote_frames == 0 or not output_path.exists() or output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        return False
    return True


def _open_opencv_browser_video_writer(output_path: Path, fps: int, size: tuple[int, int]):
    for codec in ("avc1", "H264", "X264"):
        writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*codec), fps, size)
        if writer.isOpened():
            return writer
        writer.release()
    return None


def _write_opencv_clip(writer, segment, source_path: Path, source_start: float, duration: float, width: int, height: int, fps: int, *, preview: bool, index: int) -> int:
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        return 0
    frame_total = max(1, int(max(0.1, duration) * fps))
    wrote = 0
    last_frame = None
    try:
        for frame_index in range(frame_total):
            target_seconds = max(0.0, source_start + frame_index / fps)
            cap.set(cv2.CAP_PROP_POS_MSEC, target_seconds * 1000.0)
            ok, frame = cap.read()
            if not ok:
                if last_frame is None:
                    break
                frame = last_frame.copy()
            else:
                last_frame = frame
            writer.write(_fit_video_frame(frame, width, height, preview=preview, index=index, segment=segment))
            wrote += 1
    finally:
        cap.release()
    return wrote


def _fit_video_frame(image, width: int, height: int, *, preview: bool, index: int, segment):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (18, 25, 38)
    src_height, src_width = image.shape[:2]
    scale = min(width / max(1, src_width), height / max(1, src_height))
    target_width = max(1, int(src_width * scale))
    target_height = max(1, int(src_height * scale))
    resized = cv2.resize(image, (target_width, target_height))
    x = (width - target_width) // 2
    y = (height - target_height) // 2
    frame[y:y + target_height, x:x + target_width] = resized
    label = "PREVIEW" if preview else "MyReID Scenic Vlog"
    cv2.rectangle(frame, (0, height - 52), (width, height), (0, 0, 0), -1)
    cv2.putText(frame, f"#{index} {segment.camera_id or 'camera'}", (24, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, label, (width - 230, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def _render_slideshow_video(segments, output_path: Path, *, preview: bool) -> float:
    segments = list(segments)
    if not segments:
        raise ValueError("没有可用于生成成片的素材")
    if _try_render_ffmpeg_slideshow(segments, output_path, preview=preview):
        return sum(max(2.0, min(6.0, _segment_duration(segment))) for segment in segments)

    width, height = (640, 360) if preview else (1280, 720)
    fps = 24
    writer = _open_opencv_browser_video_writer(output_path, fps, (width, height))
    if writer is None:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("无法创建浏览器兼容的 MP4 成片文件，请安装 ffmpeg/imageio-ffmpeg")

    total_duration = 0.0
    try:
        for index, segment in enumerate(segments, start=1):
            duration = max(2.0, min(6.0, _segment_duration(segment)))
            frame = _segment_frame(segment, width, height, preview=preview, index=index)
            for _ in range(max(1, int(duration * fps))):
                writer.write(frame)
            total_duration += duration
    finally:
        writer.release()
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("成片文件为空")
    return total_duration


def _try_render_ffmpeg_slideshow(segments, output_path: Path, *, preview: bool) -> bool:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return False
    width, height = (640, 360) if preview else (1280, 720)
    try:
        with tempfile.TemporaryDirectory(prefix="myreid-slides-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            entries = []
            total_duration = 0.0
            for index, segment in enumerate(segments, start=1):
                frame = _segment_frame(segment, width, height, preview=preview, index=index)
                encoded, image = cv2.imencode(".png", frame)
                if not encoded:
                    raise OSError("无法编码成片画面")
                name = f"slide_{index:04d}.png"
                (tmp_root / name).write_bytes(image.tobytes())
                duration = max(2.0, min(6.0, _segment_duration(segment)))
                entries.extend([f"file '{name}'", f"duration {duration:.6f}"])
                total_duration += duration
            # The concat demuxer needs a final frame to honor the last duration.
            entries.append(f"file '{name}'")
            (tmp_root / "slides.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
            subprocess.run(
                [ffmpeg, "-nostdin", "-y", "-f", "concat", "-safe", "1",
                 "-i", "slides.txt", "-t", f"{total_duration:.6f}",
                 "-vf", "fps=24,tpad=stop_mode=clone:stop_duration=6",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
                 str(output_path.resolve())],
                cwd=tmp_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True, timeout=180,
            )
        if output_path.exists() and output_path.stat().st_size > 0:
            return True
    except (OSError, subprocess.SubprocessError):
        logger.warning("FFmpeg 幻灯片编码失败，正在尝试 OpenCV 编码")
    output_path.unlink(missing_ok=True)
    return False


def _segment_duration(segment) -> float:
    start = float(segment.start_seconds or 0.0)
    end = float(segment.end_seconds or 0.0)
    return max(2.0, end - start)


def _segment_frame(segment, width: int, height: int, *, preview: bool, index: int):
    image = None
    if segment.thumbnail_object_key:
        path = storage.path_for(segment.thumbnail_object_key)
        if path.exists():
            image = cv2.imread(str(path))
    if image is None:
        image = np.zeros((height, width, 3), dtype=np.uint8)
        image[:] = (28, 35, 48)

    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (18, 25, 38)
    src_height, src_width = image.shape[:2]
    scale = min(width / max(1, src_width), height / max(1, src_height))
    target_width = max(1, int(src_width * scale))
    target_height = max(1, int(src_height * scale))
    resized = cv2.resize(image, (target_width, target_height))
    x = (width - target_width) // 2
    y = (height - target_height) // 2
    frame[y:y + target_height, x:x + target_width] = resized

    cv2.rectangle(frame, (0, height - 86), (width, height), (0, 0, 0), -1)
    cv2.putText(frame, f"#{index} {segment.camera_name or segment.camera_id or 'Camera'}", (28, height - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, f"similarity {float(segment.similarity or 0):.3f}", (28, height - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 210, 255), 1, cv2.LINE_AA)
    if preview:
        cv2.putText(frame, "PREVIEW", (width - 158, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
    else:
        cv2.putText(frame, "MyReID Scenic Vlog", (28, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
    return frame

def run_bound_model_job(task_path, scope, project_id, expected_space, args):
    """Queue envelope binds API selection to the worker's local model files."""
    import importlib
    from app.services.project_models import database_for
    try:
        with project_context(project_id, scope, expected_space=expected_space):
            module, function = task_path.rsplit(".", 1)
            return getattr(importlib.import_module(module), function)(*args)
    except Exception as exc:
        module = database_for(scope)
        with module.SessionLocal() as db:
            row = db.get(module.ProcessingJob, args[0])
            if row:
                row.status, row.error, row.message = "failed", str(exc), "项目模型任务失败"
                if row.media_id:
                    media = db.get(module.MediaFile, row.media_id)
                    if media:
                        media.status = "failed"
                db.commit()
        job_manager.update(args[0], status="failed", error=str(exc), message="项目模型任务失败")
        raise
