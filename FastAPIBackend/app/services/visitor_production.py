"""Durable production state machine, reusing scenic matching, planning and render jobs."""
import json
from types import SimpleNamespace
from filelock import FileLock, Timeout
import scenic_database as database
from app.core.config import get_settings
from app.services.time_utils import as_utc, utc_now
from app.services.storage import storage
from app.workers.scenic_editing import lock_path

ACTIVE_STAGES = ("waiting", "matching", "planning", "rendering")
MESSAGES = {
    "sealing": "结束游览已受理，正在封存本次录像",
    "visiting": "正在游览中", "waiting": "正在整理本次游览素材", "matching": "正在寻找你的精彩画面",
    "planning": "正在编排旅行故事", "rendering": "正在制作横竖版视频", "ready": "你的旅行短片已就绪",
    "review": "本次处理未完成，可以查看已有片段或重新匹配",
    "selecting": "匹配已完成，请挑选本人的游玩片段", "editing": "短片编排已就绪，请确认制作",
}


def prepare_visit_recordings(visit_id):
    """Only the API process owns the capture; resume its durable sealing outbox."""
    from app.services.camera_stream import scenic_camera_stream_manager, solidify_project_camera_recordings
    try:
        with FileLock(lock_path(f"visitor-{visit_id}"), timeout=0):
            with database.SessionLocal() as db:
                row = db.get(database.VisitorVisit, visit_id)
                if not row or row.stage != "sealing":
                    return
                try:
                    solidify_project_camera_recordings(scenic_camera_stream_manager, db, row.session.project_id, row.session.created_by_username)
                    row.stage, row.reason, row.internal_error = "waiting", "", ""
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    row = db.get(database.VisitorVisit, visit_id)
                    review(row, "recording_failed", exc)
                    db.commit()
    except Timeout:
        pass


def review(row, reason, error=""):
    row.stage, row.reason, row.internal_error = "review", reason, str(error)[:4000]


def output_complete(vlog):
    if not vlog or vlog.status != "succeeded" or {v.orientation for v in vlog.variants} != {"landscape", "portrait"}:
        return False
    try:
        return all(v.status == "succeeded" and v.duration_seconds > 0 and
        all(key and storage.path_for(key).is_file() and storage.path_for(key).stat().st_size > 0
            for key in (v.preview_object_key, v.full_object_key)) for v in vlog.variants)
    except (OSError, ValueError):
        return False


def within_visit(target, session, start, end):
    """Unknown capture times fail closed; upload time is never capture time."""
    from app.services.scenic_editing import _recorded_at
    media = target.media_file
    if not media or media.project_id != session.project_id:
        return False
    first, _ = _recorded_at(target, media, media.scenic_metadata, start)
    last, _ = _recorded_at(target, media, media.scenic_metadata, end)
    if not first or not last or not session.checked_out_at:
        return False
    from datetime import datetime
    return as_utc(session.created_at) <= as_utc(datetime.fromisoformat(first)) <= as_utc(datetime.fromisoformat(last)) <= as_utc(session.checked_out_at)


def safe_to_publish(db, session, data):
    """Revalidate frozen ranges after planning; require evidence in both formats."""
    threshold = get_settings().visitor_auto_similarity
    if set(data.get("variants", {})) != {"landscape", "portrait"}:
        return False
    for clips in data["variants"].values():
        selected = [clip for clip in clips if clip.get("included", True)]
        if not selected or sum(c["end_seconds"] - c["start_seconds"] for c in selected) < 3:
            return False
        for clip in selected:
            media = db.get(database.MediaFile, clip.get("media_id"))
            target = SimpleNamespace(media_file=media, timestamp=clip.get("recorded_at"))
            if not clip.get("trajectory") or clip.get("identity_similarity", 0) < threshold:
                return False
            if not within_visit(target, session, clip["start_seconds"], clip["end_seconds"]):
                return False
    return True


def production_step(visit_id):
    # Same cross-process lock convention as the existing render workers.
    try:
        with FileLock(lock_path(f"visitor-{visit_id}"), timeout=0):
            with database.SessionLocal() as db:
                row = db.get(database.VisitorVisit, visit_id)
                if row and row.stage in ACTIVE_STAGES:
                    try:
                        _step(db, row)
                    except Exception as exc:
                        db.rollback()
                        row = db.get(database.VisitorVisit, visit_id)
                        if row:
                            review(row, "production_failed", exc)
                            db.commit()
    except Timeout:
        pass


def sealed_recording_available(media):
    """API and render workers share sealed aliases; remote backup is independent."""
    if not media or not media.object_key:
        return False
    root = get_settings().object_storage_dir.resolve()
    path = (root / media.object_key).resolve()
    if not path.is_relative_to(root):
        return False
    try:
        return path.stat().st_size > 0 and int(path.with_suffix(path.suffix + '.sealed').read_text(encoding='utf-8')) == path.stat().st_size
    except (OSError, ValueError):
        return False


def _step(db, row):
    settings = get_settings()
    if settings.visitor_require_staff_verification and not row.verified_at:
        review(row, "identity_unverified")
        db.commit()
        return
    session = row.session
    actor = session.created_by_username
    age = (utc_now() - as_utc(row.requested_at)).total_seconds()
    if row.stage == "waiting":
        from sqlalchemy import or_
        captured_media = db.query(database.DetectionTarget.media_id).filter(
            database.DetectionTarget.timestamp >= as_utc(session.created_at).isoformat(),
            database.DetectionTarget.timestamp <= as_utc(session.checked_out_at).isoformat())
        jobs = db.query(database.ProcessingJob).filter(
            database.ProcessingJob.project_id == session.project_id,
            database.ProcessingJob.media_id.isnot(None),
            database.ProcessingJob.job_type != "camera_stream",
            or_(database.ProcessingJob.job_type != "camera_recording",
                database.ProcessingJob.media_id.in_(captured_media)),
            database.ProcessingJob.status.in_(("queued", "running"))).all()
        pending = any(job.job_type != 'camera_recording' or not sealed_recording_available(job.media_file) for job in jobs)
        if pending:
            if age > settings.visitor_index_wait_seconds:
                review(row, "index_timeout")
            db.commit()
            return
        row.stage = "matching"
        db.commit()
    if row.stage == "matching":
        from app.api.scenic.tourist_routes import v1_match_tourist_segments
        from app.schemas import TouristMatchRequest
        v1_match_tourist_segments(session.id, TouristMatchRequest(min_similarity=0.72), actor, db)
        db.expire_all()
        if not session.segments:
            review(row, "no_material")
        else:
            row.stage = "selecting"
        db.commit()
    if row.stage == "planning":
        # Old requests resume at visitor selection. Human confirmation is the
        # visitor's own action; normal production no longer requires staff review.
        row.stage = "selecting"
        db.commit()
    if row.stage == "rendering":
        db.expire_all()
        if output_complete(row.vlog):
            row.stage, row.reason = "ready", ""
        elif row.vlog and row.vlog.status in {"failed", "partial"}:
            review(row, "render_failed")
        elif age > 6 * 3600:
            review(row, "render_timeout")
        db.commit()
