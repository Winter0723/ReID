"""Scenic tourist registration, matching, and segment endpoints."""

import datetime
import json
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import scenic_database as database
from app.services.camera_lifecycle import protect_recording_references
from app.api.auth_dependencies import (
    get_scenic_asset_username,
    get_scenic_current_username as get_current_username,
)
from app.api.scenic.service_adapters import (
    audit as _audit,
    ensure_access as _ensure_project_access,
    persist_scenic_hit_result as _persist_hit_result,
)
from app.api.scenic.system_routes import (
    _camera_point_name,
    _ensure_tourist_access,
    _scenic_storage_project_id,
    _segment_payload,
    _segment_seconds,
    _staff_segment,
    _tourist_entry_feature,
    _tourist_payload,
)
from app.schemas import TouristMatchRequest, TouristSegmentBulkUpdate, TouristSegmentUpdate
from app.scenic_editing_schemas import TouristReference
from app.services.asset_delivery import deliver_object
from app.services.camera_stream import scenic_camera_stream_manager as camera_stream_manager
from app.services.camera_stream import solidify_project_camera_recordings
from app.services.media_service import ImagePixelLimitError, load_verified_rgb_image as _load_verified_rgb_image
from app.services.schema_service import schema_dict as _schema_dict
from app.services.storage import UploadTooLargeError, storage
from app.services.time_utils import as_utc, utc_now as _utc_now
from app.services.scenic_search import search_visit
from app.services.vector_store import scenic_vector_store as vector_store


from app.services.search_service import pagination_payload
from app.services.scenic_operations import filter_created_at, work_queue_expressions, work_queue_counts

router = APIRouter()


def v1_register_tourist(
    project_id: int,
    ticket_code: str = Form(""),
    visitor_label: str = Form(""),
    contact_hint: str = Form(""),
    consent_granted: bool = Form(False),
    snapshot_object_key: str = Form(""),
    file: UploadFile = File(None),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db, "write")
    if not consent_granted:
        raise HTTPException(status_code=400, detail="需要游客授权后才能登记")
    ticket_code = ticket_code.strip() or (
        f"T{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S')}{secrets.randbelow(10000):04d}"
    )
    if db.query(database.TouristSession).filter(
        database.TouristSession.project_id == project_id,
        database.TouristSession.ticket_code == ticket_code,
    ).first():
        raise HTTPException(status_code=400, detail="票号已存在")
    entry_object_key = ""
    entry_filename = ""
    uploaded_entry_key = ""
    try:
        snapshot_object_key = snapshot_object_key.strip()
        if snapshot_object_key:
            expected_prefix = f"projects/{_scenic_storage_project_id(project_id)}/tourists/entry/"
            if not snapshot_object_key.startswith(expected_prefix):
                raise HTTPException(status_code=400, detail="抓拍图片不属于当前景区项目")
            image_path = storage.path_for(snapshot_object_key)
            if not image_path.exists():
                raise HTTPException(status_code=400, detail="抓拍图片不存在，请重新抓拍")
            _load_verified_rgb_image(image_path)
            entry_object_key = snapshot_object_key
            entry_filename = os.path.basename(snapshot_object_key)
        else:
            if file is None:
                raise HTTPException(status_code=400, detail="请先上传照片或使用实时相机抓拍")
            ext = os.path.splitext(file.filename or "")[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                raise HTTPException(status_code=400, detail="入园登记仅支持图片")
            stored = storage.save_upload(
                _scenic_storage_project_id(project_id),
                file.filename,
                file.file,
                category="tourists/entry",
            )
            uploaded_entry_key = stored.object_key
            _load_verified_rgb_image(storage.path_for(stored.object_key))
            entry_object_key = stored.object_key
            entry_filename = stored.filename
    except UploadTooLargeError as exc:
        if uploaded_entry_key:
            storage.delete(uploaded_entry_key)
        raise HTTPException(status_code=413, detail=str(exc))
    except ImagePixelLimitError as exc:
        if uploaded_entry_key:
            storage.delete(uploaded_entry_key)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except HTTPException:
        if uploaded_entry_key:
            storage.delete(uploaded_entry_key)
        raise
    except Exception as exc:
        if uploaded_entry_key:
            storage.delete(uploaded_entry_key)
        raise HTTPException(status_code=400, detail=f"无法读取入园照片: {exc}") from exc
    session = database.TouristSession(
        project_id=project_id,
        ticket_code=ticket_code,
        visitor_label=visitor_label.strip(),
        contact_hint=contact_hint.strip(),
        consent_granted=consent_granted,
        consent_version="scenic-v1",
        consent_granted_at=_utc_now(),
        entry_image_object_key=entry_object_key,
        entry_image_filename=entry_filename,
        created_by_username=username,
    )
    try:
        db.add(session)
        db.flush()
        _audit(db, username, "scenic.tourist_registered", "tourist_session", str(session.id), ticket_code)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if uploaded_entry_key:
            storage.delete(uploaded_entry_key)
        raise HTTPException(status_code=409, detail="票号已存在") from exc
    except Exception:
        db.rollback()
        if uploaded_entry_key:
            storage.delete(uploaded_entry_key)
        raise
    db.refresh(session)
    return _tourist_payload(session, db, include_children=True)


def v1_list_tourists(
    project_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    q: str = Query("", max_length=128),
    status: str = Query("", max_length=32),
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
    work_queue: Literal["needs_review", "needs_vlog", "failed", "awaiting_payment", "ready_delivery"] | None = None,
):
    _ensure_project_access(project_id, username, db)
    tourist = database.TouristSession
    query = db.query(tourist).filter(tourist.project_id == project_id)
    if q.strip():
        query = query.filter(or_(*[column.contains(q.strip(), autoescape=True)
                             for column in (tourist.ticket_code, tourist.visitor_label, tourist.contact_hint)]))
    if status:
        query = query.filter(tourist.status == status)
    query = filter_created_at(query, tourist.created_at, date_from, date_to)
    queues = work_queue_expressions()
    if work_queue:
        query = query.filter(queues[work_queue])
    total = query.count()
    rows = (query.add_columns(*queues.values())
            .order_by(tourist.created_at.desc(), tourist.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    payloads = []
    visitor_requests = {row.session_id: row for row in db.query(database.VisitorVisit).filter(
        database.VisitorVisit.session_id.in_([session.id for session, *_ in rows])).all()} if rows else {}
    for session, *flags in rows:
        payload = _tourist_payload(session, db)
        request = visitor_requests.get(session.id)
        payload["registration_source"] = "visitor" if request else "staff"
        payload["production_request_id"] = request.id if request else None
        payload["work_queues"] = [name for name, value in zip(queues, flags) if value]
        payloads.append(payload)
    return {"status": "success", "tourists": payloads,
            "pagination": pagination_payload(page, page_size, total),
            "work_queue_counts": work_queue_counts(db, project_id, queues)}


def v1_get_tourist(
    session_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    session = _ensure_tourist_access(session_id, username, db)
    payload = _tourist_payload(session, db, include_children=True)
    request = db.query(database.VisitorVisit).filter_by(session_id=session.id).first()
    payload.update(registration_source="visitor" if request else "staff", production_request_id=request.id if request else None)
    return payload


def v1_get_tourist_entry_image(
    session_id: int,
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    session = db.query(database.TouristSession).filter(database.TouristSession.id == session_id).first()
    if not session or not session.entry_image_object_key:
        raise HTTPException(status_code=404, detail="游客登记图片不存在")
    if username is not None:
        _ensure_project_access(session.project_id, username, db)
    return deliver_object(
        session.entry_image_object_key,
        filename=session.entry_image_filename,
        content_type="image/jpeg",
    )


@protect_recording_references
def v1_match_tourist_segments(
    session_id: int,
    payload: TouristMatchRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    session = _ensure_tourist_access(session_id, username, db, "write")
    visitor_visit = db.query(database.VisitorVisit).filter_by(session_id=session.id).first()
    if visitor_visit and not session.checked_out_at:
        raise HTTPException(409, "游客尚未结束游览")
    # Staff matching also confirms checkout. Freeze the boundary before model
    # inference/sealing so retries cannot extend the visit into later recordings.
    checked_out_at = session.checked_out_at or _utc_now()
    if as_utc(checked_out_at) < as_utc(session.created_at):
        raise HTTPException(409, "出园时间不能早于登记时间")
    if session.checked_out_at is None:
        session.checked_out_at = checked_out_at
        if session.status == "registered":
            session.status = "checked_out"
        _audit(db, username, "scenic.tourist_checked_out", "tourist_session", str(session.id))
        db.commit()
    filters = {
        "min_similarity": payload.min_similarity,
        "start_time": as_utc(session.created_at).isoformat(),
        "end_time": as_utc(session.checked_out_at).isoformat(),
    }
    query_feature = _tourist_entry_feature(session)
    solidify_project_camera_recordings(camera_stream_manager, db, session.project_id, username)
    hits = search_visit(db, vector_store, session, query_feature.cpu(), top_k=payload.top_k, filters=filters)

    old_segments = db.query(database.TouristVisitSegment).filter(database.TouristVisitSegment.session_id == session.id).all()
    old_by_target = {row.target_id: row for row in old_segments if row.target_id is not None}
    reused_ids = set()
    seen = set()
    segments = []
    accepted_hits = []
    for hit in hits:
        target_id = hit.metadata.get("target_id")
        if not target_id:
            continue
        target = db.query(database.DetectionTarget).filter(database.DetectionTarget.id == target_id).first()
        if not target:
            continue
        media = target.media_file
        seek_seconds = _segment_seconds(media, target.timestamp, target.source_info)
        start_seconds = max(0.0, seek_seconds - payload.clip_padding_seconds)
        end_seconds = min(
            start_seconds + payload.max_clip_seconds,
            max(seek_seconds + payload.clip_padding_seconds, start_seconds + payload.min_clip_seconds),
        )
        if target.project_id != session.project_id:
            continue
        from app.services.visitor_production import within_visit
        from app.services.scenic_editing import _recorded_at
        captured, _ = _recorded_at(target, media, media.scenic_metadata, seek_seconds) if media else (None, "unknown")
        if not captured or not (
            as_utc(session.created_at) <= as_utc(datetime.datetime.fromisoformat(captured)) <= as_utc(session.checked_out_at)
        ):
            continue
        first, _ = _recorded_at(target, media, media.scenic_metadata, start_seconds) if media else (None, "unknown")
        last, _ = _recorded_at(target, media, media.scenic_metadata, end_seconds) if media else (None, "unknown")
        if first and last:
            first_at, last_at = datetime.datetime.fromisoformat(first), datetime.datetime.fromisoformat(last)
            start_seconds += max(0.0, (as_utc(session.created_at) - as_utc(first_at)).total_seconds())
            end_seconds -= max(0.0, (as_utc(last_at) - as_utc(session.checked_out_at)).total_seconds())
        if end_seconds - start_seconds < 1 or not within_visit(target, session, start_seconds, end_seconds):
            continue
        camera_id = target.camera_id or str(hit.metadata.get("camera_id") or "")
        dedupe_key = (target.media_id, camera_id, int(start_seconds // max(1.0, payload.min_clip_seconds)))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        similarity = round(float(hit.metadata.get("similarity", 0.0)), 4)
        previous = old_by_target.get(target.id)
        segment = previous or database.TouristVisitSegment(
            session_id=session.id,
            project_id=session.project_id,
            target_id=target.id,
            media_id=target.media_id,
            camera_id=camera_id,
            camera_name=_camera_point_name(db, session.project_id, camera_id),
            timestamp=target.timestamp or "",
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            similarity=similarity,
            thumbnail_object_key=target.crop_object_key or target.crop_path or hit.path,
            original_media_object_key=media.object_key if media else "",
            included=True,
            sort_order=len(segments) + 1,
        )
        if previous:
            reused_ids.add(previous.id)
            # Preserve selection, but refresh authoritative ranges and media
            # references after camera recovery or a tighter visit boundary.
            segment.media_id = target.media_id
            segment.timestamp = target.timestamp or ""
            segment.start_seconds, segment.end_seconds = start_seconds, end_seconds
            segment.similarity = similarity
            segment.original_media_object_key = media.object_key if media else ""
            segment.thumbnail_object_key = target.crop_object_key or target.crop_path or hit.path
        db.add(segment)
        segments.append(segment)
        accepted_hits.append(hit)
    for old in old_segments:
        if old.id not in reused_ids:
            db.delete(old)
    session.segment_count = len(segments)
    session.best_similarity = max((segment.similarity for segment in segments), default=0.0)
    session.status = "matched" if segments else "no_match"
    task = database.SearchTask(
        project_id=session.project_id,
        username=username,
        query_type="scenic",
        query_content=f"游客 {session.ticket_code} 出园匹配",
        query_object_key=session.entry_image_object_key,
        filters_json=json.dumps(filters, ensure_ascii=False),
        top_k=payload.top_k,
    )
    db.add(task)
    db.flush()
    for rank, hit in enumerate(accepted_hits, start=1):
        _persist_hit_result(db, task.id, hit, rank)
    _audit(
        db,
        username,
        "scenic.tourist_matched",
        "tourist_session",
        str(session.id),
        json.dumps(
            {"segments": len(segments), "top_k": payload.top_k, "min_similarity": payload.min_similarity},
            ensure_ascii=False,
        ),
    )
    db.commit()
    for segment in segments:
        db.refresh(segment)
    db.refresh(session)
    return {
        "status": "success",
        "tourist": _tourist_payload(session, db, include_children=True),
        "segments": [_segment_payload(segment) for segment in segments],
    }


def v1_update_tourist_segment(
    session_id: int,
    segment_id: int,
    payload: TouristSegmentUpdate,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    session = _ensure_tourist_access(session_id, username, db, "write")
    segment = (
        db.query(database.TouristVisitSegment)
        .filter(database.TouristVisitSegment.id == segment_id, database.TouristVisitSegment.session_id == session.id)
        .first()
    )
    if not segment:
        raise HTTPException(status_code=404, detail="游玩片段不存在")
    updates = _schema_dict(payload, exclude_none=True)
    for key, value in updates.items():
        setattr(segment, key, value)
    _audit(
        db,
        username,
        "scenic.segment_updated",
        "tourist_session",
        str(session.id),
        json.dumps(updates, ensure_ascii=False),
    )
    db.commit()
    db.refresh(segment)
    return _segment_payload(segment)


def v1_bulk_update_tourist_segments(
    session_id: int,
    payload: TouristSegmentBulkUpdate,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    session = _ensure_tourist_access(session_id, username, db, "write")
    segment_ids = sorted({int(segment_id) for segment_id in payload.segment_ids if int(segment_id) > 0})
    if not segment_ids:
        raise HTTPException(status_code=400, detail="请选择要更新的片段")
    segments = (
        db.query(database.TouristVisitSegment)
        .filter(
            database.TouristVisitSegment.session_id == session.id,
            database.TouristVisitSegment.id.in_(segment_ids),
        )
        .all()
    )
    if not segments:
        raise HTTPException(status_code=404, detail="游玩片段不存在")
    for segment in segments:
        segment.included = payload.included
    _audit(
        db,
        username,
        "scenic.segments_bulk_updated",
        "tourist_session",
        str(session.id),
        json.dumps({"segment_ids": segment_ids, "included": payload.included}, ensure_ascii=False),
    )
    db.commit()
    for segment in segments:
        db.refresh(segment)
    return {
        "status": "success",
        "updated": len(segments),
        "segments": [_segment_payload(segment) for segment in sorted(segments, key=lambda item: (item.sort_order, item.id))],
    }


def v1_scenic_target_thumbnail(
    project_id: int,
    target_id: int,
    username: str | None = Depends(get_scenic_asset_username),
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


def v1_scenic_search_query_image(
    task_id: int,
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    task = db.query(database.SearchTask).filter(database.SearchTask.id == task_id).first()
    if not task or not task.query_object_key:
        raise HTTPException(status_code=404, detail="查询图片不存在")
    if username is not None:
        _ensure_project_access(task.project_id, username, db)
    return deliver_object(task.query_object_key, content_type="image/jpeg")


def v1_scenic_segment_thumbnail(
    segment_id: int,
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    segment = _staff_segment(segment_id, username, db)
    return deliver_object(segment.thumbnail_object_key, content_type="image/jpeg")


def v1_scenic_segment_original(
    segment_id: int,
    download: bool = Query(False),
    username: str | None = Depends(get_scenic_asset_username),
    db: Session = Depends(database.get_db),
):
    segment = _staff_segment(segment_id, username, db)
    media = segment.media_file
    object_key = segment.original_media_object_key or (media.object_key if media else "")
    return deliver_object(
        object_key,
        filename=media.filename if media else "segment.mp4",
        content_type=media.content_type if media else "video/mp4",
        download=download,
    )


@router.post("/api/v1/scenic/tourists/{session_id}/checkout")
def checkout_tourist(session_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    session = _ensure_tourist_access(session_id, username, db, "write")
    if session.checked_out_at is None:
        session.checked_out_at = _utc_now()
        if session.status == "registered":
            session.status = "checked_out"
        _audit(db, username, "scenic.tourist_checked_out", "tourist_session", str(session_id))
        db.commit()
    return _tourist_payload(session, db, include_children=True)


@router.patch("/api/v1/scenic/tourists/{session_id}/reference")
def update_tourist_reference(session_id: int, payload: TouristReference, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    session = _ensure_tourist_access(session_id, username, db, "write")
    if payload.entry_target_box:
        image = _load_verified_rgb_image(storage.path_for(session.entry_image_object_key))
        x1, y1, x2, y2 = payload.entry_target_box
        if (x2 - x1) * image.width < 30 or (y2 - y1) * image.height < 60:
            raise HTTPException(422, "所选人物区域太小，请选择清晰的完整人物")
    session.entry_target_box_json = json.dumps(payload.entry_target_box) if payload.entry_target_box else ""
    _audit(db, username, "scenic.tourist_reference_updated", "tourist_session", str(session_id))
    db.commit()
    return _tourist_payload(session, db, include_children=True)

router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/tourists",
    v1_register_tourist,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/tourists",
    v1_list_tourists,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}",
    v1_get_tourist,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/entry-image",
    v1_get_tourist_entry_image,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/match",
    v1_match_tourist_segments,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/segments/{segment_id}",
    v1_update_tourist_segment,
    methods=["PATCH"],
)
router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/segments",
    v1_bulk_update_tourist_segments,
    methods=["PATCH"],
)
router.add_api_route(
    "/api/v1/scenic/projects/{project_id}/targets/{target_id}/thumbnail",
    v1_scenic_target_thumbnail,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/search/tasks/{task_id}/query",
    v1_scenic_search_query_image,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/segments/{segment_id}/thumbnail",
    v1_scenic_segment_thumbnail,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/segments/{segment_id}/original",
    v1_scenic_segment_original,
    methods=["GET"],
)
