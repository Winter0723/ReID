import json
import math

from PIL import Image
from sqlalchemy.orm import Session, joinedload

import database
from app.services.audit_service import add_audit_log
from app.services.asset_delivery import signed_asset_url
from app.services.camera_stream import camera_stream_manager, solidify_project_camera_recordings
from app.services.media_service import (
    media_download_url as _media_download_url,
    target_video_seek_seconds as _target_video_seek_seconds,
    timestamp_to_seconds_for_payload as _timestamp_to_seconds_for_payload,
)
from app.services.model_runtime import clip_image_feature as _model_clip_image_feature
from app.services.model_runtime import clip_text_feature as _model_clip_text_feature
from app.services.persistence_service import persist_hit_result
from app.services.vector_store import vector_store

MAX_SEARCH_WINDOW = 500


def _compact_filters(filters: dict) -> dict:
    compacted = {}
    for key, value in (filters or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        compacted[key] = value.strip() if isinstance(value, str) else value
    return compacted

def _pagination_payload(page: int, page_size: int, total: int) -> dict:
    total_pages = math.ceil(total / page_size) if total else 0
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_previous": page > 1 and total > 0,
        "has_next": page < total_pages,
    }


def _query_filter_summary(filters: dict) -> str:
    if not filters:
        return "无"
    labels = {
        "min_similarity": "相似度下限",
        "camera_id": "摄像头",
        "media_id": "媒体",
        "start_time": "开始时间",
        "end_time": "结束时间",
    }
    return "；".join(f"{labels.get(key, key)}={value}" for key, value in filters.items())


_UNRESOLVED_RELATION = object()


def _search_result_payload(
    row: database.SearchResult,
    db: Session | None,
    fallback_metadata: dict = None,
    *,
    target_override=_UNRESOLVED_RELATION,
    media_override=_UNRESOLVED_RELATION,
    scope: str = "core",
) -> dict:
    metadata = dict(fallback_metadata or {})
    target = row.target if target_override is _UNRESOLVED_RELATION else target_override
    if db is not None and row.target_id and target is None and target_override is _UNRESOLVED_RELATION:
        target = db.query(database.DetectionTarget).filter(database.DetectionTarget.id == row.target_id).first()

    if media_override is _UNRESOLVED_RELATION:
        media = target.media_file if target and target.media_file else None
    else:
        media = media_override
    media_id = target.media_id if target else metadata.get("media_id")
    if db is not None and media is None and media_id and media_override is _UNRESOLVED_RELATION:
        media = db.query(database.MediaFile).filter(database.MediaFile.id == media_id).first()

    crop_object_key = target.crop_object_key if target else None
    crop_path = target.crop_path if target else row.path
    if target:
        prefix = "/api/v1/scenic" if scope == "scenic" else "/api/v1"
        thumbnail_url = signed_asset_url(
            f"{prefix}/projects/{target.project_id}/targets/{target.id}/thumbnail"
        )
    else:
        thumbnail_url = ""
    timestamp = target.timestamp if target else metadata.get("timestamp")
    timestamp_seconds = _timestamp_to_seconds_for_payload(timestamp)
    media_type = media.media_type if media else metadata.get("media_type")
    original_media_url = _media_download_url(media, scope=scope)
    original_media_path = media.object_key if media and original_media_url else metadata.get("original_media_path")
    video_seek_seconds = _target_video_seek_seconds(target, media, metadata)

    detail_metadata = {
        **metadata,
        "target_id": target.id if target else row.target_id,
        "project_id": target.project_id if target else metadata.get("project_id"),
        "media_id": media.id if media else media_id,
        "media_filename": media.filename if media else metadata.get("media_filename"),
        "media_type": media_type,
        "camera_id": target.camera_id if target else metadata.get("camera_id"),
        "timestamp": timestamp,
        "timestamp_seconds": timestamp_seconds,
        "original_media_url": original_media_url,
        "original_media_path": original_media_path,
        "video_seek_seconds": video_seek_seconds,
    }

    return {
        "id": row.id,
        "rank": row.rank,
        "target_id": target.id if target else row.target_id,
        "path": thumbnail_url,
        "thumbnail_url": thumbnail_url,
        "similarity": row.similarity,
        "source_info": row.source_info,
        "media_id": media.id if media else media_id,
        "media_filename": media.filename if media else metadata.get("media_filename"),
        "media_type": media_type,
        "camera_id": target.camera_id if target else metadata.get("camera_id"),
        "timestamp": timestamp,
        "timestamp_seconds": timestamp_seconds,
        "original_media_url": original_media_url,
        "original_media_path": original_media_path,
        "video_seek_seconds": video_seek_seconds,
        "metadata": detail_metadata,
    }


def _run_enterprise_search(
    *,
    db: Session,
    username: str,
    project_id: int,
    query_type: str,
    query_content: str,
    query_feature,
    top_k: int,
    page: int,
    page_size: int,
    filters: dict,
    query_object_key: str = None,
) -> dict:
    filters = _compact_filters(filters)
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 12)))
    search_limit = min(MAX_SEARCH_WINDOW, max(int(top_k or page_size), page * page_size))
    solidify_project_camera_recordings(camera_stream_manager, db, project_id, username)
    hits = vector_store.search(query_feature.cpu(), top_k=search_limit, project_id=project_id, filters=filters)

    task = database.SearchTask(
        project_id=project_id,
        username=username,
        query_type=query_type,
        query_content=query_content,
        query_object_key=query_object_key,
        filters_json=json.dumps(filters, ensure_ascii=False),
        top_k=search_limit,
    )
    db.add(task)
    db.flush()

    target_ids = {
        int(hit.metadata["target_id"])
        for hit in hits
        if hit.metadata.get("target_id") is not None
    }
    targets_by_id = {}
    if target_ids:
        targets_by_id = {
            target.id: target
            for target in db.query(database.DetectionTarget)
            .options(joinedload(database.DetectionTarget.media_file))
            .filter(database.DetectionTarget.id.in_(target_ids))
            .all()
        }
    fallback_media_ids = {
        int(hit.metadata["media_id"])
        for hit in hits
        if hit.metadata.get("media_id") is not None
        and hit.metadata.get("target_id") not in targets_by_id
    }
    media_by_id = {}
    if fallback_media_ids:
        media_by_id = {
            media.id: media
            for media in db.query(database.MediaFile)
            .filter(database.MediaFile.id.in_(fallback_media_ids))
            .all()
        }

    payloads = []
    for rank, hit in enumerate(hits, start=1):
        row = persist_hit_result(db, database.SearchResult, task.id, hit, rank)
        target = targets_by_id.get(hit.metadata.get("target_id"))
        media = target.media_file if target else media_by_id.get(hit.metadata.get("media_id"))
        payloads.append(
            _search_result_payload(
                row,
                None,
                hit.metadata,
                target_override=target,
                media_override=media,
            )
        )

    add_audit_log(
        db,
        database.AuditLog,
        username,
        f"search.{query_type}",
        "project",
        str(project_id),
        json.dumps({
            "query": query_content,
            "filters": filters,
            "results": len(payloads),
            "top_k": search_limit,
        }, ensure_ascii=False),
    )
    db.commit()

    start = (page - 1) * page_size
    end = start + page_size
    return {
        "status": "success",
        "task_id": task.id,
        "query_object_url": signed_asset_url(f"/api/v1/search/tasks/{task.id}/query") if query_object_key else None,
        "pagination": _pagination_payload(page, page_size, len(payloads)),
        "results": payloads[start:end],
    }


def _clip_image_feature(image: Image.Image):
    return _model_clip_image_feature(image)


def _clip_text_feature(text: str):
    return _model_clip_text_feature(text)



pagination_payload = _pagination_payload
query_filter_summary = _query_filter_summary
search_result_payload = _search_result_payload
run_enterprise_search = _run_enterprise_search
clip_image_feature = _clip_image_feature
clip_text_feature = _clip_text_feature