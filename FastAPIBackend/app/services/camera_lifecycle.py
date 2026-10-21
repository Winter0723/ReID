"""API-owned camera startup and conservative, reference-aware recording expiry."""
import datetime
import json
import logging
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock

from sqlalchemy import or_

import scenic_database as database
from app.core.config import get_settings
from app.services import camera_bridge
from app.services.storage import storage
from app.services.time_utils import as_utc, utc_now

logger = logging.getLogger("myreid.camera")
_RETENTION_CURSOR = 0


def protect_recording_references(function):
    """Serialize matching and expiry across API/render worker processes."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        root = get_settings().object_storage_dir / '.scenic_jobs'
        root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(root / 'recording-retention.lock')):
            return function(*args, **kwargs)
    return wrapped


class ScenicCameraAutostart:
    """Retry unavailable sources, but respect a manual stop after successful start."""

    def __init__(self):
        self.started = set()

    def tick(self):
        if not get_settings().scenic_camera_auto_start:
            return
        from app.services.camera_stream import scenic_camera_stream_manager as manager, _MAINTENANCE_STOP
        with database.SessionLocal() as db:
            for project in db.query(database.Project).all():
                points = db.query(database.ScenicCameraPoint).filter_by(project_id=project.id).all()
                if not points:
                    if camera_bridge.enabled():
                        continue  # Gateway discovery owns creation of local points.
                    # Use the same default as the management wall, and persist a
                    # point so opening the UI cannot create a second stream key.
                    source = project.camera_source or "0"
                    if camera_bridge.enabled():
                        devices = [d for d in camera_bridge.devices() if d.get("online")]
                        if not devices:
                            continue
                        source = devices[0]["source"]
                    point = database.ScenicCameraPoint(project_id=project.id, name="Camera " + source,
                        camera_source=source, camera_id=source, role="entry", is_active=True)
                    db.add(point)
                    db.commit()
                    points = [point]
                points = camera_bridge.scenic_points_for_environment([p for p in points if p.is_active])
                sources = set()
                for point in points:
                    if _MAINTENANCE_STOP.is_set():
                        return
                    source = point.camera_source or point.camera_id or "0"
                    if camera_bridge.enabled() and (source.isdigit() or urlsplit(source).hostname == 'mediamtx'):
                        continue  # Reconciled by ScenicCameraHotplug.
                    canonical = camera_bridge.resolve(source)
                    if canonical in sources:
                        continue
                    sources.add(canonical)
                    key = project.id * 1_000_000 + point.id
                    identity = (key, canonical)
                    if identity in self.started:
                        continue
                    try:
                        manager.start(project_id=project.id, username=project.owner_username,
                                      camera_source=source, stream_key=key)
                        if _MAINTENANCE_STOP.is_set():
                            manager.stop(project.id, stream_key=key)
                            return
                        self.started.add(identity)
                    except Exception:
                        logger.exception("Scenic camera auto-start failed; retrying", extra={
                            "event": "camera.autostart_failed", "project_id": project.id, "point_id": point.id})


class CoreCameraAutostart:
    """Restore camera projects on API startup, once per project/source."""

    def __init__(self):
        self.started = set()

    def tick(self):
        if not get_settings().core_camera_auto_start:
            return
        import database as core_database
        from app.services.camera_stream import camera_stream_manager as manager, _MAINTENANCE_STOP
        with core_database.SessionLocal() as db:
            for project in db.query(core_database.Project).filter_by(project_type="camera").all():
                if _MAINTENANCE_STOP.is_set():
                    return
                source = project.camera_source or "0"
                identity = (project.id, camera_bridge.resolve(source))
                if identity in self.started:
                    continue
                try:
                    manager.start(project_id=project.id, username=project.owner_username, camera_source=source)
                    if _MAINTENANCE_STOP.is_set():
                        manager.stop(project.id)
                        return
                    self.started.add(identity)
                except Exception:
                    logger.exception("Core camera auto-start failed; retrying",
                                     extra={"project_id": project.id})


def _json_references(value, media_id, object_key):
    if isinstance(value, dict):
        if str(value.get("media_id", "")) == str(media_id) or value.get("object_key") == object_key:
            return True
        return any(_json_references(item, media_id, object_key) for item in value.values())
    if isinstance(value, list):
        return any(_json_references(item, media_id, object_key) for item in value)
    return value == object_key


def recording_is_protected(db, media, end_at):
    """Derive references from durable records; no fallible manual refcount."""
    targets = db.query(database.DetectionTarget.id).filter_by(media_id=media.id)
    if db.query(database.SearchResult.id).filter(database.SearchResult.target_id.in_(targets)).first():
        return True
    if db.query(database.TouristVisitSegment.id).filter(or_(
        database.TouristVisitSegment.media_id == media.id,
        database.TouristVisitSegment.target_id.in_(targets),
        database.TouristVisitSegment.original_media_object_key == media.object_key,
    )).first():
        return True
    if db.query(database.ProcessingJob.id).filter(
        database.ProcessingJob.media_id == media.id,
        database.ProcessingJob.status.in_(("queued", "running", "failed")),
        database.ProcessingJob.job_type != "camera_stream",
    ).first():
        return True
    # A search may be building its results in a separate worker transaction.
    # Preserve its source project while any non-recording work is pending.
    if db.query(database.ProcessingJob.id).filter(
        database.ProcessingJob.project_id == media.project_id,
        database.ProcessingJob.status.in_(("queued", "running")),
        ~database.ProcessingJob.job_type.in_(("camera_stream", "camera_recording")),
    ).first():
        return True
    sessions = db.query(database.TouristSession).filter_by(project_id=media.project_id).all()
    for session in sessions:
        for draft in session.edit_drafts:
            if _json_references(json.loads(draft.data_json or '{}'), media.id, media.object_key):
                return True
        for vlog in session.vlog_jobs:
            if _json_references(json.loads(vlog.snapshot_json or '{}'), media.id, media.object_key):
                return True
        # Keep the whole visit interval until a successful match has produced
        # durable segment references. Includes long visits and failed retries.
        visit = db.query(database.VisitorVisit).filter_by(session_id=session.id).first()
        pending = (not session.checked_out_at or not session.segments or
                   (visit is not None and visit.stage in {"visiting", "sealing", "waiting", "matching", "review"}))
        if pending and as_utc(session.created_at) <= end_at and (
            not session.checked_out_at or as_utc(session.checked_out_at) >= as_utc(media.created_at)
        ):
            return True
    return False


def _local_path(key):
    root = get_settings().object_storage_dir.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Recording outside object storage")
    return path


@protect_recording_references
def cleanup_camera_recordings(now=None):
    """Unlink old immutable segments, never truncate an inode shared by tourists.

    Only scenic camera aliases are expired. Core project media and uploaded
    simulation videos retain their existing lifetime. Broken references fail closed.
    """
    from app.services.camera_stream import scenic_camera_stream_manager as manager
    from app.services.central_camera_service import camera_services
    now = now or utc_now()
    cutoff = now - datetime.timedelta(hours=get_settings().camera_recording_retention_hours)
    removed = 0
    global _RETENTION_CURSOR
    # Only closed immutable media are eligible. Never hold the inference lock
    # across database scans, filesystem IO, or remote object deletion.
    with database.SessionLocal() as db:
        with manager._lock:
            active = {s.media_id for s in manager._streams.values() if s.running}
        rows = db.query(database.MediaFile).filter(
            database.MediaFile.media_type == "camera",
            database.MediaFile.status.in_(("indexed", "stopped", "failed", "expiring")),
            database.MediaFile.created_at < cutoff,
            database.MediaFile.id > _RETENTION_CURSOR,
        ).order_by(database.MediaFile.id).limit(200).all()
        _RETENTION_CURSOR = rows[-1].id if len(rows) == 200 else 0
        for media in rows:
            expected = f"projects/scenic/{media.project_id}/media/{media.id}/recordings/"
            if media.id in active or not media.object_key.startswith(expected):
                continue
            try:
                path = _local_path(media.object_key)
                # mtime is the last recording write, not the segment start.
                # Missing local aliases may exist remotely: preserve them.
                if not path.is_file() and media.status != "expiring":
                    continue
                end_at = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC) if path.is_file() else now
                # Every retry rechecks references, including interrupted deletes.
                if recording_is_protected(db, media, end_at):
                    continue
                if path.is_file() and end_at >= cutoff:
                    continue
                media.status = "expiring"
                db.commit()
                targets = list(media.detection_targets)
                manager.vector_store.delete_by_ids([target.vector_id for target in targets])
                storage.delete(media.object_key)
                path.unlink(missing_ok=True)  # S3 delete only removes its cache.
                path.with_suffix(path.suffix + '.sealed').unlink(missing_ok=True)
                for target in targets:
                    if target.crop_object_key:
                        storage.delete(target.crop_object_key)
                    crop = Path(target.crop_path).resolve()
                    if crop.is_relative_to(get_settings().gallery_dir.resolve()):
                        crop.unlink(missing_ok=True)
                    db.delete(target)
                media.status = "expired"
                media.size_bytes = 0
                db.commit()
                removed += 1
            except Exception:
                db.rollback()
                logger.exception("Camera retention skipped a recording; retrying", extra={"media_id": media.id})
    # Raw capture paths are redundant once aliases exist. Unlinking leaves all
    # protected hard links intact. Active writers (including disconnected ones)
    # are excluded explicitly; filenames are never reused.
    active_paths = {str(service.recording_info().get('path')) for service in camera_services()}
    root = get_settings().object_storage_dir / 'camera_monitor'
    for path in root.glob('*/*.avi'):
        try:
            if str(path) not in active_paths and path.stat().st_mtime < cutoff.timestamp():
                _local_path(path.relative_to(get_settings().object_storage_dir).as_posix()).unlink()
        except OSError:
            logger.exception("Could not expire raw camera recording")
    if removed:
        logger.info("Expired %s unreferenced camera recordings", removed)
    return removed
