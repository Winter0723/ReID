"""Versioned editing drafts, immutable submissions, and per-format staff assets."""

import copy
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError
from botocore.exceptions import ClientError, BotoCoreError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import scenic_database as database
from app.api.auth_dependencies import get_scenic_asset_username, get_scenic_current_username
from app.api.scenic.service_adapters import audit, ensure_access, enqueue_scenic_job_or_503
from app.api.scenic.system_routes import _ensure_tourist_access, _vlog_payload
from app.core.config import get_settings
from app.scenic_editing_schemas import ClipEdit, DraftUpdate, EditConfig, MediaEditingMetadata, VlogSubmit
from app.services.asset_delivery import deliver_object, signed_asset_url
from app.services.storage import storage
from app.services.job_manager import job_manager
from app.services.time_utils import as_utc

router = APIRouter()
PREFIX = "/api/v1/scenic"


def require_editing():
    if not get_settings().scenic_editing_enabled:
        raise HTTPException(409, "精剪功能未启用；历史成片仍可使用")


def draft_payload(row):
    data = json.loads(row.data_json)
    urls = {}
    for source in data.get("sources", []):
        base = f"{PREFIX}/tourists/{row.session_id}/drafts/{row.id}/sources/{source['id']}"
        urls[source["id"]] = {"original_media_url": signed_asset_url(f"{base}/content"),
                              "thumbnail_url": signed_asset_url(f"{base}/thumbnail")}
        source.update(urls[source["id"]])
    for clips in data.get("variants", {}).values():
        for clip in clips:
            clip.update(urls.get(clip["id"], {}))
    return {"id": row.id, "session_id": row.session_id, "revision": row.revision,
            "data": data, "updated_at": row.updated_at}


def get_draft(db, session_id, draft_id):
    draft = db.query(database.ScenicEditDraft).filter_by(id=draft_id, session_id=session_id).first()
    if not draft:
        raise HTTPException(404, "剪辑草稿不存在")
    return draft


def canonical_edit(data: dict, saved: dict) -> dict:
    """Clients can edit timing/composition, never object keys or identity metadata."""
    from app.services.scenic_templates import TEMPLATES, music_version, timeline_duration
    try:
        config = EditConfig.model_validate(data).model_dump(exclude={"previous_draft_id"})
    except ValidationError as exc:
        raise HTTPException(422, "剪辑参数无效：" + str(exc)) from exc
    if config["template_id"] != saved["template_id"]:
        raise HTTPException(409, "切换模板请重新生成草稿，以应用新的镜头节奏")
    if config["music_id"] == "auto":
        config["music_id"] = TEMPLATES[config["template_id"]]["music_id"]
    if config["music_id"] not in {"", "none"}:
        try:
            music_version(config["music_id"])
        except ValueError as exc:
            raise HTTPException(422, "配乐不存在") from exc
    sources = {str(item["id"]): item for item in saved.get("sources", [])}
    if not isinstance(data.get("variants"), dict) or set(data["variants"]) != {"landscape", "portrait"}:
        raise HTTPException(422, "需要横版和竖版两份时间线")
    result = copy.deepcopy(saved)
    result.update(config)
    result["template_version"] = TEMPLATES[config["template_id"]]["version"]
    result["variants"] = {}
    for orientation in ("landscape", "portrait"):
        raw_clips = data["variants"][orientation]
        if not isinstance(raw_clips, list) or not 1 <= len(raw_clips) <= 100:
            raise HTTPException(422, "每份时间线需要 1–100 个镜头")
        clips, seen = [], set()
        for raw in raw_clips:
            try:
                edit = ClipEdit.model_validate(raw).model_dump()
            except ValidationError as exc:
                raise HTTPException(422, "镜头参数无效：" + str(exc)) from exc
            source = sources.get(edit["id"])
            if source is None or edit["id"] in seen:
                raise HTTPException(422, "镜头不存在或重复，请重新生成草稿")
            seen.add(edit["id"])
            duration = float(source.get("source_duration_seconds") or source.get("duration_seconds") or source["end_seconds"])
            if source["kind"] == "video" and edit["end_seconds"] > duration + 0.04:
                raise HTTPException(422, "剪辑时间超出原始视频长度")
            if edit["end_seconds"] - edit["start_seconds"] < 0.3 - 1e-6:
                raise HTTPException(422, "单镜头至少保留 0.3 秒，请调整入出点")
            if edit["end_seconds"] - edit["start_seconds"] > 60:
                raise HTTPException(422, "单镜头最长 60 秒")
            if source["kind"] == "image" and (edit["start_seconds"] != 0 or edit["end_seconds"] > 12):
                raise HTTPException(422, "照片展示时长应在 0–12 秒以内")
            if edit["locked"] and not edit["included"]:
                raise HTTPException(422, "锁定镜头不能排除；请先解除锁定")
            if edit["crop_mode"] == "track" and not source.get("trajectory"):
                raise HTTPException(422, "此镜头没有可靠轨迹，请使用完整画面或手动焦点")
            if edit["crop_mode"] == "track" and (edit["start_seconds"] < source.get("tracking_start_seconds", source["start_seconds"]) - 1e-6 or edit["end_seconds"] > source.get("tracking_end_seconds", source["end_seconds"]) + 1e-6):
                raise HTTPException(422, "裁切超出跟踪分析区间，请使用完整画面或手动焦点")
            clip = copy.deepcopy(source)
            clip.update(edit)
            clips.append(clip)
        included = [clip for clip in clips if clip["included"]]
        if not included:
            raise HTTPException(422, "每种画幅至少保留一个镜头")
        if timeline_duration(included, TEMPLATES[config["template_id"]]) > config[f"{orientation}_seconds"] + 0.1:
            raise HTTPException(422, "镜头总时长超过目标，请裁切镜头或增加目标时长")
        result["variants"][orientation] = clips
    cover = data.get("cover_clip_id")
    if cover and str(cover) not in sources:
        raise HTTPException(422, "封面镜头不存在")
    result["cover_clip_id"] = cover or result.get("cover_clip_id", "")
    return result


@router.get(PREFIX + "/editing/catalog")
def editing_catalog(username: str = Depends(get_scenic_current_username)):
    from app.services.scenic_templates import template_catalog, music_catalog
    settings = get_settings()
    try:
        music = music_catalog()
    except (RuntimeError, OSError) as exc:
        raise HTTPException(503, str(exc)) from exc
    for track in music:
        track["preview_url"] = signed_asset_url(f"{PREFIX}/editing/music/{track['id']}/preview")
    return {"enabled": settings.scenic_editing_enabled, "tracking_enabled": settings.scenic_tracking_enabled,
            "templates": template_catalog(), "music": music}


@router.get(PREFIX + "/editing/music/{music_id}/preview")
def music_preview(music_id: str, username: str | None = Depends(get_scenic_asset_username)):
    require_editing()
    from app.services.scenic_templates import music_path
    try:
        path = music_path(music_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return FileResponse(path, media_type="audio/mpeg" if path.suffix == ".mp3" else "audio/wav",
                        headers={"Cache-Control": "private, no-store"})


def prepare_camera_sources(session, db, media_ids=None, refs_only=False):
    """Recover sealed local camera aliases before planning; never publish live bytes."""
    from app.services.camera_stream import scenic_camera_stream_manager, solidify_project_camera_recordings
    from app.services.storage import S3ObjectStorage
    if not isinstance(storage, S3ObjectStorage):
        return
    manager = scenic_camera_stream_manager
    media_ids = media_ids if media_ids is not None else {segment.media_id for segment in session.segments if segment.included and segment.media_id}
    with manager._lock:
        live_selection = any(state.running and state.media_id in media_ids for state in manager._streams.values())
    if live_selection and not refs_only:
        solidify_project_camera_recordings(manager, db, session.project_id, session.created_by_username)
        db.expire_all()
    for media_id in media_ids:
        media = db.get(database.MediaFile, media_id)
        if not media or media.project_id != session.project_id or media.media_type != "camera":
            continue
        expected = f"projects/scenic/{session.project_id}/media/{media.id}/recordings/"
        with manager._lock:
            live = any(state.running and state.media_id == media.id for state in manager._streams.values())
        # The old rollover race could retain the old start time but overwrite
        # its key with the NEXT recording. Recover only a unique sealed alias
        # in this media's directory whose timestamp proves the original start.
        if not live and media.object_key.startswith(expected) and media.created_at:
            stamp = media.created_at.strftime("%Y%m%d_%H%M%S_%f")
            if f"_{stamp}_" not in Path(media.object_key).name:
                root = get_settings().object_storage_dir.resolve()
                directory = (root / expected).resolve()
                if directory.is_relative_to(root):
                    candidates = [path for path in directory.glob(f"*_{stamp}_*.avi") if path.is_file() and path.stat().st_size > 0]
                    if len(candidates) == 1:
                        recovered = candidates[0]
                        old_key = media.object_key
                        media.object_key = recovered.relative_to(root).as_posix()
                        media.filename, media.size_bytes = recovered.name, recovered.stat().st_size
                        db.query(database.TouristVisitSegment).filter_by(project_id=session.project_id, media_id=media.id).filter(
                            database.TouristVisitSegment.original_media_object_key == old_key).update(
                                {"original_media_object_key": media.object_key}, synchronize_session="fetch")
                        audit(db, session.created_by_username, "scenic.camera_source_recovered", "media", str(media.id), "restored sealed recording reference")
        if refs_only:
            continue
        root = get_settings().object_storage_dir.resolve()
        local = (root / media.object_key).resolve()
        if not live and media.status in {"indexed", "stopped", "sealed"} and media.object_key.startswith(expected) and local.is_relative_to(root) and local.is_file():
            local.with_suffix(local.suffix + ".sealed").write_text(str(local.stat().st_size), encoding="utf-8")
        try:
            storage.path_for(media.object_key)
            continue
        except FileNotFoundError:
            pass
        expected = f"projects/scenic/{session.project_id}/media/{media.id}/recordings/"
        if not media.object_key.startswith(expected):
            continue
        with manager._lock:
            live = any(state.running and state.media_id == media.id for state in manager._streams.values())
        if live:
            raise HTTPException(409, "录像仍在封存，请稍后重试")
        root = get_settings().object_storage_dir.resolve()
        local = (root / media.object_key).resolve()
        if not local.is_relative_to(root) or not local.is_file() or local.stat().st_size == 0:
            continue
        storage.publish_path(media.object_key, local, media.content_type or "video/x-msvideo")
        media.size_bytes = local.stat().st_size
        media.status = "indexed"
    db.commit()


@router.post(PREFIX + "/tourists/{session_id}/drafts/auto")
def auto_draft(session_id: int, payload: EditConfig, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    require_editing()
    session = _ensure_tourist_access(session_id, username, db, "write")
    previous = json.loads(get_draft(db, session_id, payload.previous_draft_id).data_json) if payload.previous_draft_id else None
    from app.services.scenic_editing import build_draft
    try:
        prepare_camera_sources(session, db)
        data = build_draft(session, db, payload.model_dump(), previous)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(503, "素材服务暂不可用，请稍后重试") from exc
    for source in data.get("sources", []):
        source["project_id"] = session.project_id
    row = database.ScenicEditDraft(session_id=session_id, data_json=json.dumps(data, ensure_ascii=False), revision=1)
    db.add(row)
    audit(db, username, "scenic.draft_created", "tourist_session", str(session_id), data.get("template_id", ""))
    db.commit()
    return draft_payload(row)


@router.get(PREFIX + "/tourists/{session_id}/drafts")
def list_drafts(session_id: int, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    _ensure_tourist_access(session_id, username, db)
    rows = db.query(database.ScenicEditDraft).filter_by(session_id=session_id).order_by(database.ScenicEditDraft.id.desc()).limit(30).all()
    return {"drafts": [draft_payload(row) for row in rows]}


@router.get(PREFIX + "/tourists/{session_id}/drafts/{draft_id}/sources/{source_id}/{asset}")
def draft_source(session_id: int, draft_id: int, source_id: str, asset: str,
                 username: str | None = Depends(get_scenic_asset_username), db: Session = Depends(database.get_db)):
    draft = get_draft(db, session_id, draft_id)
    session = db.get(database.TouristSession, session_id)
    if username is not None:
        _ensure_tourist_access(session_id, username, db)
    source = next((item for item in json.loads(draft.data_json).get("sources", []) if item["id"] == source_id), None)
    if not source or asset not in {"thumbnail", "content"} or not session:
        raise HTTPException(404, "草稿素材不存在")
    media = db.query(database.MediaFile).filter_by(id=source["media_id"], project_id=session.project_id).first()
    if not media or media.object_key != source["object_key"]:
        raise HTTPException(404, "原始素材已移除")
    key = source.get("thumbnail_object_key") if asset == "thumbnail" else source["object_key"]
    if not key and source["kind"] == "image":
        key = source["object_key"]
    return deliver_object(key or "", content_type="image/jpeg" if asset == "thumbnail" else media.content_type)


@router.put(PREFIX + "/tourists/{session_id}/drafts/{draft_id}")
def save_draft(session_id: int, draft_id: int, payload: DraftUpdate, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    require_editing()
    _ensure_tourist_access(session_id, username, db, "write")
    draft = get_draft(db, session_id, draft_id)
    data = canonical_edit(payload.data, json.loads(draft.data_json))
    changed = db.query(database.ScenicEditDraft).filter_by(id=draft_id, session_id=session_id, revision=payload.revision).update(
        {"data_json": json.dumps(data, ensure_ascii=False), "revision": payload.revision + 1}, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(409, "草稿已被其他操作更新，请重新打开最新版本")
    audit(db, username, "scenic.draft_saved", "tourist_session", str(session_id), f"draft={draft_id}, revision={payload.revision + 1}")
    db.commit()
    db.refresh(draft)
    return draft_payload(draft)


def new_processing_job(db, project_id, message):
    job = job_manager.create("scenic_vlog_variant", message)
    db.add(database.ProcessingJob(id=job.id, project_id=project_id, job_type=job.type, status="queued", progress=0, message=message))
    return job.id


def dispatch_variant(db, vlog, variant, username):
    try:
        enqueue_scenic_job_or_503(db, variant.processing_job_id,
            "app.workers.scenic_editing.render_scenic_variant", variant.processing_job_id, variant.id, username,
            project_id=vlog.session.project_id)
    except HTTPException as exc:
        variant.status, variant.error, variant.message = "failed", str(exc.detail), "任务入队失败，可重试"
        db.commit()


def submit_edited_vlog(session_id, payload: VlogSubmit, username, db):
    require_editing()
    session = _ensure_tourist_access(session_id, username, db, "write")
    draft = get_draft(db, session_id, payload.draft_id)
    existing = db.query(database.ScenicVlogJob).filter_by(draft_id=draft.id, draft_revision=payload.revision).first()
    if existing:
        return {"status": "accepted", "job_id": existing.processing_job_id, "vlog": _vlog_payload(existing)}
    if draft.revision != payload.revision:
        raise HTTPException(409, "提交版本已过期，请保存并重新提交最新草稿")
    data = json.loads(draft.data_json)
    data = canonical_edit(data, data)
    from app.services.scenic_templates import music_metadata
    try:
        music = music_metadata(data["music_id"]) if data["music_id"] not in {"", "none"} else None
    except (RuntimeError, OSError) as exc:
        raise HTTPException(503, str(exc)) from exc
    if music:
        data["music_version"], data["music_sha256"] = music["version"], music["sha256"]
        data["music_provenance"] = {key: value for key, value in music.items() if key != "beats"}
    else:
        for key in ("music_version", "music_sha256", "music_provenance"):
            data.pop(key, None)
    from app.services.scenic_editing import verify_sources
    try:
        verify_sources([clip for clips in data["variants"].values() for clip in clips if clip.get("included", True)])
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    vlog = database.ScenicVlogJob(session_id=session_id, draft_id=draft.id, draft_revision=draft.revision,
        snapshot_json=json.dumps(data, ensure_ascii=False), created_by_username=username, status="queued",
        segment_count=len({c["id"] for clips in data["variants"].values() for c in clips if c.get("included", True)}))
    db.add(vlog)
    try:
        db.flush()
        for orientation in ("landscape", "portrait"):
            job_id = new_processing_job(db, session.project_id, "精剪已排队")
            vlog.variants.append(database.ScenicVlogVariant(orientation=orientation, processing_job_id=job_id, message="精剪已排队"))
            if orientation == "landscape":
                vlog.processing_job_id = job_id
        session.status = "rendering"
        audit(db, username, "scenic.vlog_snapshot_submitted", "tourist_session", str(session_id), f"draft={draft.id}, revision={draft.revision}")
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(database.ScenicVlogJob).filter_by(draft_id=payload.draft_id, draft_revision=payload.revision).first()
        if not existing:
            raise
        return {"status": "accepted", "job_id": existing.processing_job_id, "vlog": _vlog_payload(existing)}
    for variant in list(vlog.variants):
        dispatch_variant(db, vlog, variant, username)
    from app.workers.scenic_editing import refresh_parent
    refresh_parent(db, vlog.id)
    db.expire_all()
    return {"status": "accepted", "job_id": vlog.processing_job_id, "vlog": _vlog_payload(vlog)}


@router.post(PREFIX + "/vlogs/{vlog_id}/variants/{variant_id}/retry")
def retry_variant(vlog_id: int, variant_id: int, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    require_editing()
    vlog = db.get(database.ScenicVlogJob, vlog_id)
    if not vlog:
        raise HTTPException(404, "成片不存在")
    _ensure_tourist_access(vlog.session_id, username, db, "write")
    variant = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, vlog_job_id=vlog_id).first()
    if not variant:
        raise HTTPException(404, "画幅不存在")
    changed = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, status="failed").update(
        {"status": "queued", "progress": 0, "error": "", "message": "重试已排队"}, synchronize_session=False)
    if not changed:
        raise HTTPException(409, "只有失败的画幅可以重试")
    db.refresh(variant)
    variant.processing_job_id = new_processing_job(db, vlog.session.project_id, "重试已排队")
    if variant.orientation == "landscape":
        vlog.processing_job_id = variant.processing_job_id
    db.commit()
    dispatch_variant(db, vlog, variant, username)
    from app.workers.scenic_editing import refresh_parent
    refresh_parent(db, vlog.id)
    db.expire_all()
    return {"vlog": _vlog_payload(vlog)}


@router.get(PREFIX + "/vlogs/{vlog_id}/variants/{variant_id}/{asset}")
def variant_asset(vlog_id: int, variant_id: int, asset: str, download: bool = Query(False),
                  username: str | None = Depends(get_scenic_asset_username), db: Session = Depends(database.get_db)):
    variant = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, vlog_job_id=vlog_id).first()
    vlog = db.get(database.ScenicVlogJob, vlog_id)
    if not variant or not vlog or asset not in {"full", "preview", "draft-preview", "cover"}:
        raise HTTPException(404, "成片资源不存在")
    if username is not None:
        _ensure_tourist_access(vlog.session_id, username, db)
    key = getattr(variant, asset.replace("-", "_") + "_object_key")
    return deliver_object(key, filename=Path(key).name, content_type="image/jpeg" if asset == "cover" else "video/mp4", download=download)


def metadata_media(db, project_id, media_id, username, permission="read"):
    ensure_access(project_id, username, db, permission)
    media = db.query(database.MediaFile).filter_by(id=media_id, project_id=project_id).first()
    if not media:
        raise HTTPException(404, "媒体不存在")
    return media


@router.get(PREFIX + "/projects/{project_id}/media/{media_id}/editing-metadata")
def media_metadata(project_id: int, media_id: int, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    metadata_media(db, project_id, media_id, username)
    row = db.query(database.ScenicMediaMetadata).filter_by(media_id=media_id).first()
    return {"recorded_at": as_utc(row.recorded_at).isoformat() if row and row.recorded_at else None, "location": row.location if row else ""}


@router.patch(PREFIX + "/projects/{project_id}/media/{media_id}/editing-metadata")
def update_media_metadata(project_id: int, media_id: int, payload: MediaEditingMetadata,
                          username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    media = metadata_media(db, project_id, media_id, username, "write")
    if media.media_type == "camera" and payload.recorded_at is not None:
        raise HTTPException(422, "相机时间使用实际采集时间，不能修改")
    if payload.recorded_at is not None and payload.recorded_at.tzinfo is None:
        raise HTTPException(422, "拍摄时间需要包含时区")
    row = db.query(database.ScenicMediaMetadata).filter_by(media_id=media_id).first()
    if row is None:
        row = database.ScenicMediaMetadata(media_id=media_id)
        db.add(row)
    if "recorded_at" in payload.model_fields_set:
        row.recorded_at = as_utc(payload.recorded_at).replace(tzinfo=None) if payload.recorded_at else None
    if "location" in payload.model_fields_set:
        row.location = payload.location
    audit(db, username, "scenic.media_metadata_updated", "media", str(media_id), payload.location)
    db.commit()
    return media_metadata(project_id, media_id, username, db)
