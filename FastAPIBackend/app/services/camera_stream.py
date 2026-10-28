from contextlib import ExitStack
from app.services.project_models import project_context, snapshot_for
import datetime
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from app.services.camera_policy import validate_camera_source, capture_source, safe_camera_name

import cv2
from PIL import Image

import database
import scenic_database
from app.core.config import get_settings
from app.services.central_camera_service import camera_services, get_camera_service
from app.services.job_manager import job_manager
from app.services.model_runtime import Detection, clip_image_feature, detect_people
from app.services.storage import S3ObjectStorage, storage
from app.services.vector_store import VectorPoint, scenic_vector_store, vector_store


BASE_GALLERY_DIR = str(get_settings().gallery_dir)
logger = logging.getLogger("myreid.camera")
_CAMERA_DEVICE_CACHE_LOCK = threading.Lock()
_CAMERA_DEVICE_CACHE = {"expires_at": 0.0, "max_index": 0, "devices": []}
_LAST_FRAME_INFO_BY_SOURCE: dict[str, dict] = {}
_LAST_FRAME_INFO_LOCK = threading.Lock()
_RECORDING_INDEX_LOCK = threading.RLock()


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def reconcile_orphaned_camera_streams(db_module=database) -> dict[str, int]:
    """Close camera rows left active by an unclean process shutdown."""

    with db_module.SessionLocal() as db:
        media_count = (
            db.query(db_module.MediaFile)
            .filter(
                db_module.MediaFile.media_type == "camera",
                db_module.MediaFile.status == "streaming",
            )
            .update(
                {db_module.MediaFile.status: "failed"},
                synchronize_session=False,
            )
        )
        job_count = (
            db.query(db_module.ProcessingJob)
            .filter(
                db_module.ProcessingJob.job_type == "camera_stream",
                db_module.ProcessingJob.status.in_(["queued", "running"]),
            )
            .update(
                {
                    db_module.ProcessingJob.status: "failed",
                    db_module.ProcessingJob.progress: 1.0,
                    db_module.ProcessingJob.message: "服务异常重启，相机采集任务已终止",
                    db_module.ProcessingJob.error: "服务异常重启，相机采集任务已终止",
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return {"media": int(media_count or 0), "jobs": int(job_count or 0)}


class _NullVideoWriter:
    def write(self, frame) -> None:
        return None

    def release(self) -> None:
        return None


class CentralCameraCapture:
    """OpenCV-compatible reader backed by the process-wide camera service."""

    def __init__(self, source: str) -> None:
        self.source = (source or "0").strip() or "0"
        self._service = get_camera_service(self.source)
        self._opened = False
        self.frame_info = {}
        self._service.acquire()
        self._opened = True

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        if not self._opened:
            return False, None
        frame, _frame_time, _frame_index, frame_info = self._service.wait_for_frame(timeout=1.5)
        if frame is None:
            return False, None
        self.frame_time = _frame_time
        self.frame_info = dict(frame_info or {})
        with _LAST_FRAME_INFO_LOCK:
            _LAST_FRAME_INFO_BY_SOURCE[self.source] = self.frame_info
        return True, frame

    def release(self) -> None:
        if self._opened:
            self._opened = False
            self._service.release()

    def get(self, prop_id: int) -> float:
        status = self._service.status()
        if prop_id == cv2.CAP_PROP_FPS:
            return float(status.get("capture_fps") or get_settings().camera_preview_fps)
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(status.get("width") or 0)
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(status.get("height") or 0)
        return 0.0

    def set(self, prop_id: int, value: float) -> bool:
        return True


def _replace_preview_file(temp_path: Path, frame_path: Path) -> bool:
    for attempt in range(8):
        try:
            temp_path.replace(frame_path)
            return True
        except OSError:
            time.sleep(0.025 * (attempt + 1))
    return False


def _safe_filename(filename: str) -> str:
    return Path(filename or "camera_recording.avi").name


def _central_recording_alias_object_key(manager, state, info: dict) -> str:
    filename = _safe_filename(info.get("filename") or state.recording_filename or f"camera_{state.media_id}.avi")
    return f"projects/{manager._storage_project_id(state.project_id)}/media/{state.media_id}/recordings/{filename}"


def _link_or_copy_recording(source_path: Path, alias_path: Path) -> bool:
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path.exists():
        try:
            if os.path.samefile(source_path, alias_path):
                return True
        except OSError:
            pass
        try:
            if alias_path.stat().st_size >= source_path.stat().st_size:
                return True
        except OSError:
            pass
    try:
        if alias_path.exists():
            alias_path.unlink()
        os.link(source_path, alias_path)
        return True
    except OSError:
        pass
    try:
        shutil.copy2(source_path, alias_path)
        return True
    except OSError as exc:
        logger.warning(
            "camera recording alias failed: %s",
            exc,
            extra={"event": "camera.recording_alias_failed"},
        )
        return False


def _sync_media_to_recording_info(manager, db, state, info: dict, *, persist: bool = False) -> bool:
    source_path = info.get("path")
    if not isinstance(source_path, Path) or not source_path.exists():
        return False

    alias_object_key = _central_recording_alias_object_key(manager, state, info)
    alias_path = get_settings().object_storage_dir / alias_object_key
    if not _link_or_copy_recording(source_path, alias_path):
        return False
    media = db.query(manager.db_module.MediaFile).filter(manager.db_module.MediaFile.id == state.media_id).first()
    if not media:
        return False

    size_bytes = alias_path.stat().st_size if alias_path.exists() else int(info.get("size_bytes") or 0)
    if persist and isinstance(storage, S3ObjectStorage):
        storage.publish_path(alias_object_key, alias_path, info.get("content_type") or "video/x-msvideo")
    media.object_key = alias_object_key
    media.filename = _safe_filename(info.get("filename") or state.recording_filename or media.filename)
    media.content_type = info.get("content_type") or "video/x-msvideo"
    media.size_bytes = int(size_bytes)
    media.sha256 = hashlib.sha256(f"{alias_object_key}:{info.get('object_key', '')}".encode("utf-8")).hexdigest()
    if info.get("started_at"):
        media.created_at = info["started_at"]
    if media.status not in {"indexed", "stopped", "failed"}:
        media.status = "streaming"
    state.recording_path = source_path
    state.recording_url = storage.url_for(alias_object_key)
    state.recording_filename = media.filename
    state.recording_started_at = info.get("started_at")
    return True


def _sync_media_to_central_recording(manager, db, state) -> bool:
    return _sync_media_to_recording_info(manager, db, state, get_camera_service(state.source).recording_info())


def _append_video_seek_source_info(source_info: str, frame_info: dict) -> str:
    seek = frame_info.get("video_seek_seconds")
    if seek is None:
        return source_info
    try:
        seek_value = max(0.0, float(seek))
    except (TypeError, ValueError):
        return source_info
    frame_index = int(frame_info.get("recording_frame_index") or 0)
    fps = float(frame_info.get("recording_fps") or 0.0)
    marker = f"video_seek_seconds={seek_value:.3f}; recording_frame_index={frame_index}; recording_fps={fps:.3f}"
    return f"{source_info}\n{marker}" if source_info else marker


def _create_next_stream_media(manager, db, state, username: str) -> None:
    started_at = datetime.datetime.now(datetime.UTC)
    recording_filename = f"实时入库_{manager._safe_source(state.source)}_{started_at.strftime('%Y%m%d_%H%M%S')}.avi"
    object_key = (
        f"projects/{manager._storage_project_id(state.project_id)}/camera/"
        f"{uuid.uuid4()}_pending_{recording_filename}"
    )
    media = manager.db_module.MediaFile(
        project_id=state.project_id,
        uploader_username=username,
        filename=recording_filename,
        object_key=object_key,
        content_type="video/x-msvideo",
        size_bytes=0,
        sha256=hashlib.sha256(f"{state.project_id}:{state.source}:{uuid.uuid4()}".encode("utf-8")).hexdigest(),
        media_type="camera",
        status="streaming",
    )
    db.add(media)
    db.flush()
    state.media_id = media.id
    state.recording_filename = recording_filename
    state.recording_url = ""
    state.recording_path = None
    state.recording_started_at = _utc_now()
    state.segment_targets = 0
    state.sealed_media_id = None
    job = db.get(manager.db_module.ProcessingJob, state.job_id)
    if job:
        job.media_id = media.id


def queue_sealed_camera_recording(manager, db, media):
    """A committed recording is the upload outbox; inference never waits on S3."""
    if not isinstance(storage, S3ObjectStorage):
        return None
    path = (get_settings().object_storage_dir / media.object_key).resolve()
    if path.is_file():
        path.with_suffix(path.suffix + ".sealed").write_text(str(path.stat().st_size), encoding="utf-8")
    existing = db.query(manager.db_module.ProcessingJob).filter_by(media_id=media.id, job_type="camera_recording").first()
    if existing:
        return existing.id
    # Redis and remote storage must never run inside the ingestion/rollover lock.
    job_id = str(uuid.uuid4())
    db.add(manager.db_module.ProcessingJob(id=job_id, project_id=media.project_id, media_id=media.id,
        job_type="camera_recording", status="queued", progress=0, message="录像已封存，等待上传"))
    media.status = "sealed"
    db.flush()  # Session autoflush is disabled; retries must see this outbox row.
    return job_id


def dispatch_camera_recording(manager, db, job_id):
    from app.services.task_service import enqueue_job_or_503
    job = db.get(manager.db_module.ProcessingJob, job_id)
    if not job:
        return
    try:
        enqueue_job_or_503(db, manager.db_module.ProcessingJob, job_id,
            "app.workers.tasks.publish_camera_recording", job_id, job.media_id,
            "scenic" if manager.db_module is scenic_database else "core", project_id=job.project_id, media_id=job.media_id)
    except Exception:
        logger.exception("Could not enqueue sealed camera recording", extra={"event": "camera.publish_enqueue_failed"})


def solidify_project_camera_recordings(manager, db, project_id: int, username: str) -> list[int]:
    # A rollover must not race a frame's ORM writes or expose an uncommitted
    # media ID to the ingestion thread. Capture/preview continues independently.
    with manager._inference_lock:
        return _solidify_project_camera_recordings(manager, db, project_id, username)


def _solidify_project_camera_recordings(manager, db, project_id: int, username: str, stopping_state=None) -> list[int]:
    """Roll every ingestion reference sharing a physical source, once per source."""
    with manager._lock:
        selected = [state for state in manager._streams.values()
            if int(state.project_id) == int(project_id) and state.running and not state.stop_event.is_set()]
    sources = {state.source for state in selected}
    if stopping_state is not None:
        sources = {stopping_state.source}
    if not sources:
        return []
    registered = [camera_stream_manager, scenic_camera_stream_manager]
    managers = registered if any(manager is candidate for candidate in registered) else [manager]
    contexts, owned_dbs = [], []
    try:
        for candidate in managers:
            session = db if candidate.db_module is manager.db_module else candidate.db_module.SessionLocal()
            if session is not db:
                owned_dbs.append(session)
            with candidate._lock:
                states = [state for state in candidate._streams.values() if state.source in sources
                    and state.running and getattr(state, 'sealed_media_id', None) != state.media_id]
            if candidate is manager and stopping_state is not None and not any(state is stopping_state for state in states):
                states.append(stopping_state)
            contexts.extend((candidate, session, state) for state in states)
        sealed_media_ids = []
        for source in sources:
            info = get_camera_service(source).seal_current_recording(timeout=5.0)
            if not info.get("path") or not info.get("object_key"):
                continue
            touched = []
            for candidate, session, state in contexts:
                if state.source != source:
                    continue
                if _sync_media_to_recording_info(candidate, session, state, info, persist=False):
                    media = session.get(candidate.db_module.MediaFile, state.media_id)
                    if candidate is manager and state.project_id == project_id:
                        sealed_media_ids.append(state.media_id)
                    if media:
                        has_targets = session.query(candidate.db_module.DetectionTarget.id).filter_by(media_id=media.id).first()
                        media.status = "indexed" if has_targets else "stopped"
                        queue_sealed_camera_recording(candidate, session, media)
                        if state is not stopping_state and not state.stop_event.is_set():
                            _create_next_stream_media(candidate, session, state, media.uploader_username)
                        else:
                            state.sealed_media_id = media.id
                    if not any(session is other for other in touched):
                        touched.append(session)
            for session in touched:
                session.commit()
        return sealed_media_ids
    finally:
        for session in owned_dbs:
            session.close()


class CameraStreamState:
    def __init__(
        self,
        *,
        project_id: int,
        stream_key: Optional[int] = None,
        media_id: int,
        job_id: str,
        source: str,
        sample_interval_seconds: float,
        min_confidence: float,
    ):
        self.project_id = project_id
        self.stream_key = int(stream_key if stream_key is not None else project_id)
        self.media_id = media_id
        self.job_id = job_id
        self.source = source
        self.sample_interval_seconds = sample_interval_seconds
        self.min_confidence = min_confidence
        self.running = True
        self.started_at = _utc_now()
        self.stopped_at: Optional[datetime.datetime] = None
        self.captured_frames = 0
        self.indexed_targets = 0
        self.latest_frame_url = ""
        self.latest_frame_version = 0
        self.recording_url = ""
        self.recording_filename = ""
        self.recording_path: Optional[Path] = None
        self.recording_started_at = self.started_at
        self.segment_targets = 0
        self.sealed_media_id = None
        self.message = "实时入库进行中"
        self.error = ""
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def to_payload(self) -> dict:
        return {
            "running": self.running,
            "project_id": self.project_id,
            "stream_key": self.stream_key,
            "media_id": self.media_id,
            "job_id": self.job_id,
            "camera_source": self.source,
            "sample_interval_seconds": self.sample_interval_seconds,
            "min_confidence": self.min_confidence,
            "captured_frames": self.captured_frames,
            "indexed_targets": self.indexed_targets,
            "latest_frame_url": self.latest_frame_url,
            "latest_frame_version": self.latest_frame_version,
            "recording_url": self.recording_url,
            "recording_filename": self.recording_filename,
            "recording_ready": bool(self.recording_url),
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "stopped_at": self.stopped_at.strftime("%Y-%m-%d %H:%M:%S") if self.stopped_at else "",
        }


class CameraStreamManager:
    def __init__(
        self,
        *,
        db_module=database,
        vector_store_instance=vector_store,
        storage_namespace: str = "",
        gallery_project_prefix: str = "project",
    ) -> None:
        self.db_module = db_module
        self.vector_store = vector_store_instance
        self.storage_namespace = storage_namespace.strip("/")
        self.gallery_project_prefix = gallery_project_prefix
        self._streams: dict[int, CameraStreamState] = {}
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._inference_lock = _RECORDING_INDEX_LOCK

    def start(
        self,
        *,
        project_id: int,
        username: str,
        camera_source: str = "0",
        sample_interval_seconds: float = 1.0,
        min_confidence: float = 0.35,
        stream_key: Optional[int] = None,
    ) -> dict:
        # Device probing and durable row creation must be one control-plane
        # operation, otherwise concurrent starts can leave a hidden stream.
        with self._start_lock:
            payload = self._start_serialized(
                project_id=project_id,
                username=username,
                camera_source=camera_source,
                sample_interval_seconds=sample_interval_seconds,
                min_confidence=min_confidence,
                stream_key=stream_key,
            )
        with self._lock:
            state = self._streams.get(self._stream_key(project_id, stream_key))
        if not state:
            return payload

        db = self.db_module.SessionLocal()
        try:
            if _sync_media_to_central_recording(self, db, state):
                db.commit()
                return state.to_payload()
        except Exception:
            db.rollback()
        finally:
            db.close()
        return payload

    def _start_serialized(
        self,
        *,
        project_id: int,
        username: str,
        camera_source: str = "0",
        sample_interval_seconds: float = 1.0,
        min_confidence: float = 0.35,
        stream_key: Optional[int] = None,
    ) -> dict:
        snapshot = snapshot_for(project_id, "scenic" if self.db_module is scenic_database else "core")
        camera_source = validate_camera_source((camera_source or "0").strip() or "0")
        resolved_stream_key = self._stream_key(project_id, stream_key)
        with self._lock:
            existing = self._streams.get(resolved_stream_key)
            if existing and existing.running:
                return existing.to_payload()

        probe_error = self._probe_capture_source(camera_source)
        if probe_error:
            raise ValueError(probe_error)

        db = self.db_module.SessionLocal()
        try:
            started_at = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            recording_filename = f"实时入库_{self._safe_source(camera_source)}_{started_at}.avi"
            media = self.db_module.MediaFile(
                project_id=project_id,
                uploader_username=username,
                filename=recording_filename,
                object_key=f"projects/{self._storage_project_id(project_id)}/camera/{uuid.uuid4()}_pending_{recording_filename}",
                content_type="video/x-msvideo",
                size_bytes=0,
                sha256=hashlib.sha256(f"{project_id}:{camera_source}:{uuid.uuid4()}".encode("utf-8")).hexdigest(),
                media_type="camera",
                status="streaming",
            )
            db.add(media)
            db.flush()

            job = job_manager.create("camera_stream", "实时入库进行中")
            db.add(self.db_module.ProcessingJob(
                id=job.id,
                project_id=project_id,
                media_id=media.id,
                job_type=job.type,
                status=job.status,
                progress=job.progress,
                message=job.message,
            ))
            db.add(self.db_module.AuditLog(
                username=username,
                action="camera.started",
                resource_type="project",
                resource_id=str(project_id),
                detail=f"source={camera_source}",
            ))
            db.commit()

            state = CameraStreamState(
                project_id=project_id,
                stream_key=resolved_stream_key,
                media_id=media.id,
                job_id=job.id,
                source=camera_source,
                sample_interval_seconds=sample_interval_seconds,
                min_confidence=min_confidence,
            )
            state.embedding_space_id = snapshot["embedding_space_id"]
            state.recording_filename = recording_filename
            thread = threading.Thread(target=self._run_stream, args=(state,), daemon=True)
            state.thread = thread
            with self._lock:
                self._streams[resolved_stream_key] = state
            try:
                thread.start()
            except Exception as exc:
                state.running = False
                state.error = str(exc)
                with self._lock:
                    if self._streams.get(resolved_stream_key) is state:
                        self._streams.pop(resolved_stream_key, None)
                media.status = "failed"
                self._update_job(
                    db,
                    state,
                    status="failed",
                    progress=1.0,
                    message="实时入库启动失败",
                    error=str(exc),
                )
                db.commit()
                raise
            return state.to_payload()
        finally:
            db.close()

    def stop(self, project_id: int, wait: bool = True, stream_key: Optional[int] = None) -> dict:
        resolved_stream_key = self._stream_key(project_id, stream_key)
        with self._lock:
            state = self._streams.get(resolved_stream_key)
            if not state:
                return self._empty_status(project_id, resolved_stream_key)
            state.message = "正在暂停实时入库"
            state.stop_event.set()
            thread = state.thread
        if wait and thread:
            thread.join(timeout=5)
        return self.status(project_id, resolved_stream_key)

    def stop_all(self, wait: bool = True, timeout: float = 5.0) -> int:
        """Signal every stream before joining so shutdown stays bounded."""

        with self._lock:
            states = [state for state in self._streams.values() if state.running]
            for state in states:
                state.stop_event.set()
        if wait:
            deadline = time.monotonic() + max(0.0, timeout)
            for state in states:
                thread = state.thread
                if thread and thread is not threading.current_thread():
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return len(states)

    def status(self, project_id: int, stream_key: Optional[int] = None) -> dict:
        resolved_stream_key = self._stream_key(project_id, stream_key)
        with self._lock:
            state = self._streams.get(resolved_stream_key)
            if not state:
                return self._empty_status(project_id, resolved_stream_key)
            return state.to_payload()

    def is_running(self, project_id: int, stream_key: Optional[int] = None) -> bool:
        resolved_stream_key = self._stream_key(project_id, stream_key)
        with self._lock:
            state = self._streams.get(resolved_stream_key)
            return bool(state and state.running)

    def running_media_id(self, project_id: int, stream_key: Optional[int] = None) -> Optional[int]:
        resolved_stream_key = self._stream_key(project_id, stream_key)
        with self._lock:
            state = self._streams.get(resolved_stream_key)
            if state and state.running:
                return state.media_id
        return None

    def recording_path_for_media(self, media_id: int) -> Optional[Path]:
        with self._lock:
            for state in self._streams.values():
                if state.media_id != media_id:
                    continue
                path = get_camera_service(state.source).recording_path_for_media(media_id)
                if path and path.exists():
                    return path
                if state.recording_path and state.recording_path.exists():
                    return state.recording_path
        return None

    def _run_stream(self, state: CameraStreamState) -> None:
        db = self.db_module.SessionLocal()
        cap = None
        recording_path: Optional[Path] = None
        recording_writer = None
        recording_unavailable = False
        model_context = ExitStack()
        try:
            model_context.enter_context(project_context(state.project_id, "scenic" if self.db_module is scenic_database else "core", expected_space=getattr(state, "embedding_space_id", None)))
            self._update_job(db, state, status="running", progress=0.0, message="正在打开摄像头")
            cap = self._open_capture(state.source)
            if not cap.isOpened():
                raise RuntimeError(f"无法打开摄像头源: {state.source}")
            recording_path = self._new_recording_path(state)
            state.recording_path = recording_path

            last_sample_at = 0.0
            last_preview_at = 0.0
            latest_detections: list[Detection] = []
            while not state.stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    state.stop_event.wait(0.1)
                    continue

                state.captured_frames += 1
                if recording_writer is None and not recording_unavailable:
                    recording_writer = self._open_recording_writer(recording_path, frame, cap)
                    recording_unavailable = recording_writer is None
                if recording_writer is not None:
                    recording_writer.write(frame)

                now = time.monotonic()
                if now - last_preview_at >= 0.25:
                    try:
                        self._save_latest_frame(state, frame, latest_detections)
                    except Exception:
                        pass
                    finally:
                        last_preview_at = now
                if now - last_sample_at < state.sample_interval_seconds:
                    state.stop_event.wait(0.005)
                    continue
                last_sample_at = now

                capture_time = getattr(cap, "frame_time", None) or _utc_now()
                detections: list[Detection] = []
                points: list[VectorPoint] = []
                frame_committed = False
                try:
                    with self._inference_lock:
                        frame_info = dict(getattr(cap, "frame_info", {}) or {})
                        current_info = get_camera_service(state.source).recording_info() if frame_info else {}
                        if frame_info.get("recording_object_key") and frame_info["recording_object_key"] != current_info.get("object_key"):
                            continue  # This frame belongs to the recording sealed while we waited.
                        state.frame_info = frame_info
                        detections = detect_people(frame, min_confidence=state.min_confidence)
                        points = self._index_detections(db, state, frame, detections, capture_time)
                        latest_detections = detections

                        if points:
                            self.vector_store.upsert(points)
                            next_indexed_total = state.indexed_targets + len(points)
                            message = f"实时入库中，已入库 {next_indexed_total} 个目标"
                        else:
                            next_indexed_total = state.indexed_targets
                            message = "实时入库中，当前帧未检测到可入库目标"
                        self._update_job(db, state, status="running", progress=0.5, message=message)
                        db.commit()
                        frame_committed = True
                        state.indexed_targets = next_indexed_total
                        state.segment_targets += len(points)
                        state.error = ""
                        state.message = message
                        try:
                            self._save_latest_frame(state, frame, detections)
                            last_preview_at = time.monotonic()
                        except Exception:
                            # Preview JPEGs are transient UI state and must not
                            # invalidate a committed database/vector batch.
                            pass
                except Exception as frame_exc:
                    db.rollback()
                    if points and not frame_committed:
                        self._delete_staged_points(points)
                    state.error = str(frame_exc)
                    state.message = f"实时入库中，检测暂不可用: {frame_exc}"
                    try:
                        self._save_latest_frame(state, frame, detections)
                        self._update_job(
                            db,
                            state,
                            status="running",
                            progress=0.5,
                            message=state.message,
                            error=state.error,
                        )
                        db.commit()
                    except Exception:
                        db.rollback()
                    state.stop_event.wait(0.05)

            if recording_writer is not None:
                recording_writer.release()
                recording_writer = None
            # Staff stopping a stream can overlap a visitor checkout. Both
            # sealing paths must finish their ORM writes before either rolls
            # this state to another media row.
            with self._inference_lock:
                self._save_recording(db, state, recording_path)
                media = db.query(self.db_module.MediaFile).filter(self.db_module.MediaFile.id == state.media_id).first()
                if media and media.status != "sealed":
                    media.status = "indexed" if state.indexed_targets > 0 else "stopped"
                self._update_job(db, state, status="succeeded", progress=1.0, message="实时入库已暂停")
                db.commit()
            state.message = "实时入库已暂停"
        except Exception as exc:
            db.rollback()
            state.error = str(exc)
            state.message = "实时入库异常停止"
            media = db.query(self.db_module.MediaFile).filter(self.db_module.MediaFile.id == state.media_id).first()
            if media:
                media.status = "failed"
            self._update_job(db, state, status="failed", progress=1.0, message="实时入库异常停止", error=str(exc))
            db.commit()
        finally:
            if recording_writer is not None:
                recording_writer.release()
            if recording_path is not None:
                recording_path.unlink(missing_ok=True)
            if cap is not None:
                cap.release()
            state.running = False
            state.recording_path = None
            state.stopped_at = _utc_now()
            with self._lock:
                self._streams[state.stream_key] = state
            model_context.close()
            db.close()

    def _index_detections(self, db, state: CameraStreamState, frame, detections: list[Detection], capture_time: datetime.datetime):
        points = []
        created_object_keys: list[str] = []
        timestamp = capture_time.isoformat()
        time_safe = capture_time.strftime("%Y%m%d_%H%M%S")
        with _LAST_FRAME_INFO_LOCK:
            frame_info = dict(getattr(state, "frame_info", None) or _LAST_FRAME_INFO_BY_SOURCE.get(state.source) or {})
        _sync_media_to_central_recording(self, db, state)
        try:
            for index, detection in enumerate(detections):
                person_crop = detection.crop_from(frame)
                if person_crop is None or person_crop.shape[0] < 60 or person_crop.shape[1] < 30:
                    continue

                crop_name = f"[CAM]_{self._safe_source(state.source)}_{time_safe}_p{index}.jpg"
                crop_path = self._write_temp_crop(person_crop)
                try:
                    with Image.open(crop_path) as image:
                        feature = clip_image_feature(image.convert("RGB"))
                    stored = storage.save_path(
                        self._storage_project_id(state.project_id),
                        crop_path,
                        crop_name,
                        category=f"media/{state.media_id}/crops",
                    )
                    created_object_keys.append(stored.object_key)
                finally:
                    crop_path.unlink(missing_ok=True)

                vector_id = str(uuid.uuid4())
                source_info = _append_video_seek_source_info(
                    f"📷 Camera {state.source}\n⏱️ {timestamp} (目标 {index})",
                    frame_info,
                )
                target = self.db_module.DetectionTarget(
                    project_id=state.project_id,
                    media_id=state.media_id,
                    crop_path=stored.object_key,
                    crop_object_key=stored.object_key,
                    source_info=source_info,
                    camera_id=state.source,
                    timestamp=timestamp,
                    vector_id=vector_id,
                )
                db.add(target)
                db.flush()
                metadata = {
                    "target_id": target.id,
                    "project_id": state.project_id,
                    "media_id": state.media_id,
                    "camera_id": state.source,
                    "timestamp": timestamp,
                    "source_info": source_info,
                }
                if frame_info.get("video_seek_seconds") is not None:
                    metadata["video_seek_seconds"] = float(frame_info.get("video_seek_seconds") or 0.0)
                    if frame_info.get("recording_frame_index") is not None:
                        metadata["recording_frame_index"] = int(frame_info.get("recording_frame_index") or 0)
                    if frame_info.get("recording_object_key"):
                        metadata["recording_object_key"] = frame_info["recording_object_key"]
                points.append(VectorPoint(
                    id=vector_id,
                    vector=feature.cpu(),
                    path=stored.object_key,
                    metadata=metadata,
                ))
            return points
        except Exception:
            for object_key in created_object_keys:
                try:
                    storage.delete(object_key)
                except Exception:
                    pass
            raise

    def _delete_staged_points(self, points: list[VectorPoint]) -> None:
        try:
            self.vector_store.delete_by_ids(point.id for point in points)
        except Exception:
            pass
        for point in points:
            try:
                storage.delete(point.path)
            except Exception:
                pass

    def _save_latest_frame(self, state: CameraStreamState, frame, detections: list[Detection]) -> None:
        annotated = frame.copy()
        for detection in detections:
            x1, y1, x2, y2 = detection.clamped_xyxy(annotated.shape[1], annotated.shape[0])
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (47, 111, 237), 2)
            cv2.putText(
                annotated,
                f"person {detection.confidence:.2f}",
                (x1, max(18, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (47, 111, 237),
                2,
                cv2.LINE_AA,
            )
        camera_path = "camera" if state.stream_key == state.project_id else f"camera/{state.stream_key}"
        frame_dir = Path(BASE_GALLERY_DIR) / self._gallery_project_name(state.project_id) / camera_path
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / "latest.jpg"
        temp_path = frame_dir / f"latest.{uuid.uuid4().hex}.tmp.jpg"
        if annotated.shape[1] > 960:
            target_width = 960
            target_height = int(annotated.shape[0] * (target_width / annotated.shape[1]))
            annotated = cv2.resize(annotated, (target_width, target_height), interpolation=cv2.INTER_AREA)
        try:
            if not cv2.imwrite(str(temp_path), annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 82]):
                return
            if not _replace_preview_file(temp_path, frame_path):
                return
            state.latest_frame_url = f"/gallery/{self._gallery_project_name(state.project_id)}/{camera_path}/latest.jpg"
            state.latest_frame_version += 1
        finally:
            temp_path.unlink(missing_ok=True)

    def _save_recording(self, db, state: CameraStreamState, recording_path: Optional[Path]) -> None:
        if isinstance(storage, S3ObjectStorage):
            if state.sealed_media_id == state.media_id:
                return
            # Stop one consumer without corrupting the still-running references
            # in other projects sharing the same physical camera.
            if _solidify_project_camera_recordings(self, db, state.project_id, "", stopping_state=state):
                return
        if _sync_media_to_central_recording(self, db, state):
            return
        self._save_local_recording(db, state, recording_path)

    def _save_local_recording(self, db, state: CameraStreamState, recording_path: Optional[Path]) -> None:
        if recording_path is None or not recording_path.exists() or recording_path.stat().st_size <= 0:
            return
        stored = storage.save_path(
            self._storage_project_id(state.project_id),
            recording_path,
            state.recording_filename or f"camera_{state.media_id}.mp4",
            category=f"media/{state.media_id}/recordings",
        )
        media = db.query(self.db_module.MediaFile).filter(self.db_module.MediaFile.id == state.media_id).first()
        if media:
            if media.object_key and media.object_key != stored.object_key:
                storage.delete(media.object_key)
            media.object_key = stored.object_key
            media.filename = stored.filename
            media.content_type = stored.content_type
            media.size_bytes = stored.size_bytes
            media.sha256 = stored.sha256
        state.recording_url = storage.url_for(stored.object_key)
        state.recording_filename = stored.filename

    def _update_job(self, db, state: CameraStreamState, *, status: str, progress: float, message: str, error: str = None) -> None:
        record = job_manager.update(state.job_id, status=status, progress=progress, message=message, error=error or "")
        db_job = db.query(self.db_module.ProcessingJob).filter(self.db_module.ProcessingJob.id == state.job_id).first()
        if db_job:
            db_job.status = record.status if record else status
            db_job.progress = record.progress if record else progress
            db_job.message = record.message if record else message
            db_job.error = record.error if record else error

    @staticmethod
    def _opencv_source(source: str):
        try:
            return int(source)
        except (TypeError, ValueError):
            return source

    @classmethod
    def _open_capture(cls, source: str):
        return CentralCameraCapture(source)

    @classmethod
    def _open_physical_capture(cls, source: str):
        opencv_source = cls._opencv_source(capture_source(source))
        if isinstance(opencv_source, int) and platform.system() == "Darwin":
            return cv2.VideoCapture(opencv_source, cv2.CAP_AVFOUNDATION)
        if isinstance(opencv_source, int) and platform.system() == "Windows":
            cap = cv2.VideoCapture(opencv_source, cv2.CAP_DSHOW)
            if cap.isOpened():
                return cap
            cap.release()
            return cv2.VideoCapture(opencv_source, cv2.CAP_MSMF)
        return cv2.VideoCapture(opencv_source)

    @classmethod
    def _probe_capture_source(cls, source: str) -> str:
        service = get_camera_service(source)
        service.acquire()
        try:
            # RTSP needs handshake + a keyframe; a local-device 2s deadline races the first frame.
            endpoint = get_settings().camera_sources.get(str(service.source), str(service.source))
            timeout = 10.0 if endpoint.lower().startswith(("rtsp://", "rtsps://")) else 2.0
            frame, _frame_time, _frame_index, _frame_info = service.wait_for_frame(timeout=timeout)
            if frame is not None:
                return ""
            status = service.status()
            return str(status.get("error") or status.get("message") or f"无法打开摄像头源: {source}")
        finally:
            service.release()

    @staticmethod
    def _safe_source(source: str) -> str:
        return safe_camera_name(source)

    def _storage_project_id(self, project_id: int) -> str:
        if not self.storage_namespace:
            return str(project_id)
        return f"{self.storage_namespace}/{project_id}"

    def _gallery_project_name(self, project_id: int) -> str:
        return f"{self.gallery_project_prefix}_{project_id}"

    @staticmethod
    def _stream_key(project_id: int, stream_key: Optional[int] = None) -> int:
        return int(stream_key if stream_key is not None else project_id)

    @staticmethod
    def _write_temp_crop(person_crop) -> Path:
        fd, temp_name = tempfile.mkstemp(prefix="myreid-camera-", suffix=".jpg")
        os.close(fd)
        temp_path = Path(temp_name)
        if not cv2.imwrite(str(temp_path), person_crop):
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("实时入库目标裁剪保存失败")
        return temp_path

    @staticmethod
    def _new_recording_path(state: CameraStreamState) -> Path:
        fd, temp_name = tempfile.mkstemp(
            prefix=f"myreid-camera-{state.project_id}-{state.stream_key}-{state.media_id}-",
            suffix=".avi",
        )
        os.close(fd)
        Path(temp_name).unlink(missing_ok=True)
        return Path(temp_name)

    @staticmethod
    def _open_recording_writer(recording_path: Path, frame, cap):
        return _NullVideoWriter()

    @staticmethod
    def _empty_status(project_id: int, stream_key: Optional[int] = None) -> dict:
        resolved_stream_key = int(stream_key if stream_key is not None else project_id)
        return {
            "running": False,
            "project_id": project_id,
            "stream_key": resolved_stream_key,
            "media_id": None,
            "job_id": "",
            "camera_source": "",
            "sample_interval_seconds": 1.0,
            "min_confidence": 0.35,
            "captured_frames": 0,
            "indexed_targets": 0,
            "latest_frame_url": "",
            "latest_frame_version": 0,
            "recording_url": "",
            "recording_filename": "",
            "message": "实时入库未启用",
            "error": "",
            "started_at": "",
            "stopped_at": "",
        }


def list_camera_devices(max_index: int = 8, cache_seconds: float = 8.0, force: bool = False) -> list[dict]:
    """Probe local camera indices.

    On macOS, an iPhone connected by cable appears here when the system exposes
    it as an AVFoundation/Continuity Camera device.
    """
    from app.services import camera_bridge
    if camera_bridge.enabled():
        return camera_bridge.devices()
    now = time.monotonic()
    with _CAMERA_DEVICE_CACHE_LOCK:
        if (
            not force
            and _CAMERA_DEVICE_CACHE["max_index"] >= max_index
            and _CAMERA_DEVICE_CACHE["expires_at"] > now
        ):
            return [dict(device) for device in _CAMERA_DEVICE_CACHE["devices"]]

    names = _camera_names()
    probe_max_index = max_index
    if platform.system() == "Windows" and names:
        # DirectShow indices normally follow the device list. Keep a two-index
        # cushion for drivers that expose a gap, without probing 0..15 and
        # flooding stderr with expected "index out of range" messages.
        probe_max_index = min(max_index, len(names) + 2)
    if platform.system() == "Darwin":
        subprocess_devices = _probe_camera_devices_in_subprocess(probe_max_index)
        if subprocess_devices is not None:
            devices = _label_camera_devices(subprocess_devices, names)
            with _CAMERA_DEVICE_CACHE_LOCK:
                _CAMERA_DEVICE_CACHE.update({"expires_at": now + cache_seconds, "max_index": max_index, "devices": devices})
            return [dict(device) for device in devices]

    devices = _label_camera_devices(_probe_camera_devices_in_process(probe_max_index), names)
    with _CAMERA_DEVICE_CACHE_LOCK:
        _CAMERA_DEVICE_CACHE.update({"expires_at": now + cache_seconds, "max_index": max_index, "devices": devices})
    return [dict(device) for device in devices]


def _probe_physical_camera_devices(max_index: int, open_capture) -> list[dict]:
    devices = []
    consecutive_misses = 0
    for index in range(max(0, max_index)):
        try:
            cap = open_capture(str(index))
        except ValueError:
            # Enumeration is broader than the administrator's device allowlist.
            continue
        try:
            if not cap.isOpened():
                consecutive_misses += 1
                if devices and consecutive_misses >= 2:
                    break
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                consecutive_misses += 1
                if devices and consecutive_misses >= 2:
                    break
                continue
            height, width = frame.shape[:2]
            devices.append({
                "source": str(index),
                "index": index,
                "width": int(width),
                "height": int(height),
            })
            consecutive_misses = 0
        finally:
            cap.release()
    return devices


def _merge_camera_devices(*device_lists: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for devices in device_lists:
        for device in devices or []:
            source = str(device.get("source", device.get("index", ""))).strip()
            if source:
                merged[source] = {**merged.get(source, {}), **device, "source": source}
    return sorted(
        merged.values(),
        key=lambda device: (
            0 if str(device.get("source", "")).isdigit() else 1,
            int(device["source"])
            if str(device.get("source", "")).isdigit()
            else str(device.get("source", "")),
        ),
    )


def _running_camera_devices() -> list[dict]:
    return [device for service in camera_services() for device in service.devices()]


def _probe_camera_devices_in_process(max_index: int) -> list[dict]:
    physical_devices = _probe_physical_camera_devices(max_index, CameraStreamManager._open_physical_capture)
    return _merge_camera_devices(physical_devices, _running_camera_devices())


def _probe_physical_camera_devices_in_subprocess(max_index: int) -> Optional[list[dict]]:
    script = r"""
import json
import platform
import sys

import cv2

max_index = int(sys.argv[1])
devices = []
for index in range(max(0, max_index)):
    backend = cv2.CAP_AVFOUNDATION if platform.system() == "Darwin" else 0
    cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            continue
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        height, width = frame.shape[:2]
        devices.append({
            "source": str(index),
            "index": index,
            "width": int(width),
            "height": int(height),
        })
    finally:
        cap.release()
print(json.dumps(devices))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(max_index)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    try:
        devices = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return devices if isinstance(devices, list) else None


def _probe_camera_devices_in_subprocess(max_index: int) -> Optional[list[dict]]:
    physical_devices = _probe_physical_camera_devices_in_subprocess(max_index)
    if physical_devices is None:
        physical_devices = _probe_physical_camera_devices(max_index, CameraStreamManager._open_physical_capture)
    return _merge_camera_devices(physical_devices, _running_camera_devices())


def _label_camera_devices(devices: list[dict], names: list[str]) -> list[dict]:
    labeled_devices = []
    for detected_order, device in enumerate(devices):
        index = int(device.get("index", detected_order))
        label = names[detected_order] if detected_order < len(names) else ""
        if not label:
            label = f"摄像头 {index}"
        device_type = "iphone" if "iphone" in label.lower() else "camera"
        labeled_devices.append({
            "source": str(device.get("source", index)),
            "label": label,
            "index": index,
            "type": device_type,
            "width": int(device.get("width", 0)),
            "height": int(device.get("height", 0)),
        })
    return labeled_devices


def _windows_camera_names() -> list[str]:
    if platform.system() != "Windows":
        return []
    try:
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        completed = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []
    names = []
    for line in completed.stderr.splitlines():
        match = re.search(r'"([^"]+)"\s+\(video\)', line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _macos_camera_names() -> list[str]:
    if platform.system() != "Darwin":
        return []
    try:
        completed = subprocess.run(
            ["system_profiler", "SPCameraDataType"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            check=False,
        )
    except Exception:
        return []
    names = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line.endswith(":") or line in {"Camera:"}:
            continue
        name = line[:-1].strip()
        if name and name not in names:
            names.append(name)
    return names


def _camera_names() -> list[str]:
    if platform.system() == "Windows":
        return _windows_camera_names()
    return _macos_camera_names()


camera_stream_manager = CameraStreamManager()
scenic_camera_stream_manager = CameraStreamManager(
    db_module=scenic_database,
    vector_store_instance=scenic_vector_store,
    storage_namespace="scenic",
    gallery_project_prefix="scenic_project",
)


_MAINTENANCE_STOP = threading.Event()
_MAINTENANCE_THREADS = []
_MAINTENANCE_POOLS = []


def recording_segment_due(state, now=None):
    settings = get_settings()
    started = state.recording_started_at or state.started_at
    from app.services.time_utils import as_utc
    elapsed = ((now or _utc_now()) - as_utc(started)).total_seconds()
    if elapsed >= settings.camera_recording_segment_seconds:
        return True
    if settings.camera_recording_segment_targets and state.segment_targets >= settings.camera_recording_segment_targets:
        return True
    path = state.recording_path
    return bool(path and path.is_file() and path.stat().st_size >= settings.camera_recording_segment_max_bytes)


def seal_due_camera_recordings():
    for manager in (camera_stream_manager, scenic_camera_stream_manager):
        with manager._lock:
            projects = {state.project_id for state in manager._streams.values()
                if state.running and not state.stop_event.is_set() and recording_segment_due(state)}
        for project_id in projects:
            try:
                with manager.db_module.SessionLocal() as db:
                    project = db.get(manager.db_module.Project, project_id)
                    if project:
                        solidify_project_camera_recordings(manager, db, project_id, project.owner_username)
            except Exception:
                logger.exception("Camera segment sealing failed; retrying")

    # Preview-only/persistent captures have no ingestion state to rotate them.
    from app.services.time_utils import as_utc
    from app.services import camera_bridge
    active_sources = set()
    for manager in (camera_stream_manager, scenic_camera_stream_manager):
        with manager._lock:
            active_sources.update(camera_bridge.resolve(state.source) for state in manager._streams.values() if state.running)
    for service in camera_services():
        if service.source in active_sources:
            continue
        info = service.recording_info()
        started = info.get("started_at")
        if service.status().get("running") and started and (
            _utc_now() - as_utc(started)
        ).total_seconds() >= get_settings().camera_recording_segment_seconds:
            service.seal_current_recording()


def prepare_pending_visit_recordings(pool):
    # Sealing requests are committed before the HTTP response; this API-owned
    # loop recovers them on restart without pretending another process owns USB.
    from app.services.visitor_production import prepare_visit_recordings
    with scenic_database.SessionLocal() as db:
        ids = [item[0] for item in db.query(scenic_database.VisitorVisit.id).filter_by(stage="sealing").all()]
    for visit_id in ids:
        pool.submit(visit_id, prepare_visit_recordings, visit_id)


def publish_pending_camera_recordings(pool):
    # Reuse the existing publisher, on independent bounded IO threads rather
    # than behind long render jobs. DB rows remain the restart-safe outbox.
    from app.workers.tasks import publish_camera_recording
    for manager in (camera_stream_manager, scenic_camera_stream_manager):
        with manager.db_module.SessionLocal() as db:
            jobs = db.query(manager.db_module.ProcessingJob).filter(
                manager.db_module.ProcessingJob.job_type == "camera_recording",
                manager.db_module.ProcessingJob.status.in_(("queued", "failed", "running"))).order_by(
                    manager.db_module.ProcessingJob.created_at).limit(100).all()
            for job in jobs:
                try:
                    queued = job_manager.queue.fetch_job(job.id)
                    rq_status = queued.get_status(refresh=True) if queued else None
                    if rq_status in {"queued", "started", "deferred", "scheduled"}:
                        continue
                    from app.services.time_utils import as_utc
                    age = (_utc_now() - as_utc(job.updated_at)).total_seconds()
                    if job.status != "queued" and age < 30:
                        continue
                    if rq_status in {None, "failed", "stopped", "canceled"}:
                        pool.submit((manager.storage_namespace, job.id), publish_camera_recording,
                            job.id, job.media_id, "scenic" if manager.db_module is scenic_database else "core")
                except Exception:
                    logger.exception("Camera upload outbox dispatch failed")


def start_camera_recording_maintenance():
    if any(thread.is_alive() for thread in _MAINTENANCE_THREADS):
        return
    from app.services.job_manager import KeyedTaskPool
    visit_pool = KeyedTaskPool(2, "visitor-sealing")
    publish_pool = KeyedTaskPool(2, "camera-backup")
    _MAINTENANCE_POOLS[:] = [visit_pool, publish_pool]
    _MAINTENANCE_STOP.clear()
    from app.services.camera_hotplug import ScenicCameraHotplug
    hotplug = ScenicCameraHotplug()
    from app.services.camera_lifecycle import CoreCameraAutostart, ScenicCameraAutostart, cleanup_camera_recordings
    autostart = ScenicCameraAutostart()
    core_autostart = CoreCameraAutostart()
    def run(tick, interval=.5):
        while not _MAINTENANCE_STOP.is_set():
            try:
                tick()
            except Exception:
                logger.exception("Camera recording maintenance failed; retrying")
            _MAINTENANCE_STOP.wait(interval)
    _MAINTENANCE_THREADS[:] = [threading.Thread(target=run, args=(tick,), name=name, daemon=True)
        for name, tick in (
            ("camera-gateway-hotplug", hotplug.tick),
            ("camera-segments", seal_due_camera_recordings),
            ("visitor-sealing-outbox", lambda: prepare_pending_visit_recordings(visit_pool)),
            ("camera-backup-outbox", lambda: publish_pending_camera_recordings(publish_pool)))]
    _MAINTENANCE_THREADS.extend([
        threading.Thread(target=run, args=(autostart.tick, 3), name="scenic-camera-autostart", daemon=True),
        threading.Thread(target=run, args=(core_autostart.tick, 3), name="core-camera-autostart", daemon=True),
        threading.Thread(target=run, args=(cleanup_camera_recordings, 60), name="camera-retention", daemon=True),
    ])
    for thread in _MAINTENANCE_THREADS:
        thread.start()


def stop_camera_recording_maintenance():
    _MAINTENANCE_STOP.set()
    deadline = time.monotonic() + 6
    for thread in _MAINTENANCE_THREADS:
        thread.join(timeout=max(0, deadline - time.monotonic()))
    for pool in _MAINTENANCE_POOLS:
        pool.shutdown()
