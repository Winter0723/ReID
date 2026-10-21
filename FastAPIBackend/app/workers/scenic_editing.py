"""Independent, idempotent render jobs consuming only immutable snapshots."""

import hashlib
import json
import logging
import tempfile
from pathlib import Path

from filelock import FileLock, Timeout

import scenic_database as database
from app.core.config import get_settings
from app.services.job_manager import job_manager
from app.services.storage import storage


def lock_path(name):
    root = get_settings().object_storage_dir / ".scenic_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{name}.lock")


def _aggregate_parent(db, vlog_id):
    vlog = db.query(database.ScenicVlogJob).filter_by(id=vlog_id).with_for_update().first()
    if vlog is None or not vlog.variants:
        return
    variants = list(vlog.variants)
    statuses = [variant.status for variant in variants]
    if all(status == "succeeded" for status in statuses):
        vlog.status = "succeeded"
    elif any(status in {"queued", "running"} for status in statuses):
        vlog.status = "queued" if all(status == "queued" for status in statuses) else "running"
    elif "succeeded" in statuses:
        vlog.status = "partial"
    else:
        vlog.status = "failed"
    vlog.error = "；".join(v.error for v in variants if v.error)
    landscape = next((v for v in variants if v.orientation == "landscape" and v.status == "succeeded"), None)
    if landscape:
        vlog.preview_object_key = landscape.preview_object_key
        vlog.full_object_key = landscape.full_object_key
        vlog.preview_filename = Path(landscape.preview_object_key).name
        vlog.full_filename = Path(landscape.full_object_key).name
        vlog.duration_seconds = landscape.duration_seconds
    latest = db.query(database.ScenicVlogJob.id).filter_by(session_id=vlog.session_id).order_by(database.ScenicVlogJob.id.desc()).first()
    if latest and latest[0] == vlog.id and vlog.session.status != "paid":
        vlog.session.status = {"succeeded": "vlog_ready", "failed": "vlog_failed", "partial": "vlog_partial"}.get(vlog.status, "rendering")


def refresh_parent(db, vlog_id):
    with FileLock(lock_path(f"parent-{vlog_id}"), timeout=30):
        db.expire_all()
        _aggregate_parent(db, vlog_id)
        db.commit()


def update_progress(variant_id, job_id, progress, message, *, status="running", error=""):
    with database.SessionLocal() as db:
        variant = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, processing_job_id=job_id).first()
        if variant is None:
            raise RuntimeError("成片任务已删除或替换")
        variant.status, variant.progress, variant.message, variant.error = status, progress, message, error
        row = db.get(database.ProcessingJob, job_id)
        if row:
            row.status, row.progress, row.message, row.error = status, progress, message, error
        db.commit()
    try:
        job_manager.update(job_id, status=status, progress=progress, message=message, error=error)
    except Exception:
        logging.getLogger(__name__).warning("Could not mirror render progress %s", job_id, exc_info=True)


def _fail_waiting_variant(job_id, variant_id, exc):
    """Make failures before rendering visible and retryable without stale writes."""
    message, error = "无法取得渲染资源，可重试此画幅", str(exc)
    try:
        with database.SessionLocal() as db:
            variant = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, processing_job_id=job_id).first()
            if variant is None:
                return
            vlog_id = variant.vlog_job_id
            changed = db.query(database.ScenicVlogVariant).filter_by(
                id=variant_id, processing_job_id=job_id, status="queued",
            ).update({"status": "failed", "progress": 1, "message": message, "error": error}, synchronize_session=False)
            if not changed:
                return
            processing = db.get(database.ProcessingJob, job_id)
            if processing:
                processing.status, processing.progress, processing.message, processing.error = "failed", 1, message, error
            db.commit()
    except Exception:
        logging.getLogger(__name__).warning("Could not persist render admission failure %s", job_id, exc_info=True)
        return
    try:
        job_manager.update(job_id, status="failed", progress=1, message=message, error=error)
    except Exception:
        logging.getLogger(__name__).warning("Could not mirror render admission failure %s", job_id, exc_info=True)
    try:
        with database.SessionLocal() as db:
            refresh_parent(db, vlog_id)
    except Exception:
        # The same directory/permissions failure can prevent the parent lock.
        # Keep the child retryable and preserve the original admission error.
        logging.getLogger(__name__).warning("Could not aggregate render admission failure %s", job_id, exc_info=True)


def execution_lock_path(variant_id: int, job_id: str):
    # Hash both identifiers so job IDs cannot create unsafe or oversized paths.
    identity = json.dumps([variant_id, job_id], separators=(",", ":")).encode("utf-8")
    return lock_path(f"variant-{hashlib.sha256(identity).hexdigest()}")


def render_scenic_variant(job_id: str, variant_id: int, username: str):
    # Duplicate deliveries of the same job are no-ops, including while waiting
    # for the global slot. A retry must not inherit its failed job's lease:
    # failure is visible before the old execution reaches its finally block.
    try:
        execution_lock = FileLock(execution_lock_path(variant_id, job_id), timeout=0)
        execution_lock.acquire()
    except Timeout:
        return
    except Exception as exc:
        _fail_waiting_variant(job_id, variant_id, exc)
        raise
    try:
        _render_scenic_variant(job_id, variant_id, username)
    finally:
        execution_lock.release()


def _render_scenic_variant(job_id: str, variant_id: int, username: str):
    created = []
    vlog_id = None
    published = False
    # A cross-process lock limits encoding load in RQ and local development alike.
    render_lock = None
    try:
        render_lock = FileLock(lock_path("render"), timeout=6 * 3600)
        render_lock.acquire()
    except Exception as exc:
        _fail_waiting_variant(job_id, variant_id, exc)
        raise
    try:
        try:
            with database.SessionLocal() as db:
                variant = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, processing_job_id=job_id).first()
                if not variant or variant.status not in {"queued", "running"}:
                    # Retries get a new job ID. Redelivery of a failed/canceled
                    # job must not restart it or overwrite its recorded outcome.
                    return
                vlog = db.get(database.ScenicVlogJob, variant.vlog_job_id)
                if not vlog:
                    return
                vlog_id, project_id, session_id = vlog.id, vlog.session.project_id, vlog.session_id
                orientation = variant.orientation
                snapshot = json.loads(vlog.snapshot_json)
            update_progress(variant_id, job_id, 0.02, "检查冻结素材")
            with database.SessionLocal() as db:
                refresh_parent(db, vlog_id)
            from app.services.scenic_render import render_variant
            with tempfile.TemporaryDirectory(prefix=f"myreid-edit-{variant_id}-") as temp:
                result = render_variant(snapshot, orientation, Path(temp),
                    lambda fraction, message: update_progress(variant_id, job_id, min(0.93, 0.03 + fraction * 0.90), message))
                keys = {}
                for name in ("full", "preview", "draft_preview", "cover"):
                    path = Path(result[f"{name}_path"])
                    stored = storage.save_path(f"scenic/{project_id}", path, f"vlog_{vlog_id}_{orientation}_{path.name}",
                        category=f"tourists/{session_id}/vlogs/{vlog_id}/{orientation}")
                    keys[f"{name}_object_key"] = stored.object_key
                    created.append(stored.object_key)
                with FileLock(lock_path(f"parent-{vlog_id}"), timeout=30), database.SessionLocal() as db:
                    variant = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, processing_job_id=job_id).first()
                    if not variant:
                        raise RuntimeError("成片任务已删除")
                    for field, key in keys.items():
                        setattr(variant, field, key)
                    variant.duration_seconds = result["duration_seconds"]
                    variant.status, variant.progress, variant.message, variant.error = "succeeded", 1.0, "成片完成", ""
                    processing = db.get(database.ProcessingJob, job_id)
                    if processing:
                        processing.status, processing.progress, processing.message, processing.error = "succeeded", 1.0, "成片完成", ""
                    from app.api.scenic.service_adapters import audit
                    audit(db, username, "scenic.variant_rendered", "vlog", str(vlog_id), orientation)
                    db.flush()
                    _aggregate_parent(db, vlog_id)
                    db.commit()
                    published = True
            created.clear()
        except Exception as exc:
            if published:
                logging.getLogger(__name__).warning("Render published; temporary cleanup failed for %s", job_id, exc_info=True)
            else:
                for key in created:
                    try:
                        storage.delete(key)
                    except Exception:
                        logging.getLogger(__name__).warning("Could not clean failed render object %s", key, exc_info=True)
                try:
                    update_progress(variant_id, job_id, 1, "成片失败，可重试此画幅", status="failed", error=str(exc))
                    if vlog_id:
                        with database.SessionLocal() as db:
                            refresh_parent(db, vlog_id)
                except Exception:
                    # A secondary database/parent-lock failure must not replace
                    # the codec/storage error that caused this render to fail.
                    logging.getLogger(__name__).warning("Could not persist or aggregate render failure %s", job_id, exc_info=True)
                raise
        # Redis is a progress mirror: a mirror failure must not retract published assets.
        try:
            job_manager.update(job_id, status="succeeded", progress=1.0, message="成片完成", error="")
        except Exception:
            logging.getLogger(__name__).warning("Could not mirror completed render %s", job_id, exc_info=True)
    finally:
        render_lock.release()
