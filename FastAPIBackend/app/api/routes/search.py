import csv
import io
import json

from PIL import Image
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

import database

from app.api.auth_dependencies import get_core_asset_username, get_current_username
from app.services.audit_service import add_audit_log
from app.services.media_service import (
    ImagePixelLimitError,
    load_verified_rgb_image as _load_verified_rgb_image,
)
from app.schemas import JobOut, SearchResponse, TextSearchRequest
from app.services.asset_delivery import deliver_object, signed_asset_url
from app.services.job_manager import job_manager
from app.services.permission_service import ensure_project_access
from app.services.schema_service import schema_dict as _schema_dict
from app.services.search_service import (
    clip_image_feature as _clip_image_feature,
    clip_text_feature as _clip_text_feature,
    pagination_payload as _pagination_payload,
    query_filter_summary as _query_filter_summary,
    run_enterprise_search as _run_enterprise_search,
    search_result_payload as _search_result_payload,
)
from app.services.storage import UploadTooLargeError, storage
from app.services.task_service import enqueue_job_or_503, job_to_schema as _job_to_schema

router = APIRouter()


def _audit(db, username, action, resource_type, resource_id=None, detail=""):
    return add_audit_log(db, database.AuditLog, username, action, resource_type, resource_id, detail)


def _ensure_project_access(project_id, username, db, permission="read"):
    return ensure_project_access(
        db,
        project_model=database.Project,
        project_member_model=database.ProjectMember,
        user_model=database.User,
        project_id=project_id,
        username=username,
        permission=permission,
    )


def _enqueue_job_or_503(db, job_id, task_path, *args, project_id=None, media_id=None):
    return enqueue_job_or_503(
        db,
        database.ProcessingJob,
        job_id,
        task_path,
        *args,
        project_id=project_id,
        media_id=media_id,
    )


@router.get("/api/v1/search/tasks/{task_id}/query")
def v1_get_search_query_image(
    task_id: int,
    username: str | None = Depends(get_core_asset_username),
    db: Session = Depends(database.get_db),
):
    task = db.query(database.SearchTask).filter(database.SearchTask.id == task_id).first()
    if not task or not task.query_object_key:
        raise HTTPException(status_code=404, detail="查询图片不存在")
    if username is not None:
        _ensure_project_access(task.project_id, username, db)
    return deliver_object(task.query_object_key, content_type="image/jpeg")

@router.get("/api/v1/history")
def v1_list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    preview_size: int = Query(4, ge=0, le=12),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    task_query = db.query(database.SearchTask).filter(database.SearchTask.username == username)
    total = task_query.count()
    tasks = (
        task_query.order_by(database.SearchTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    task_ids = [task.id for task in tasks]
    result_counts = {}
    preview_rows_by_task = {task_id: [] for task_id in task_ids}
    if task_ids:
        result_counts = dict(
            db.query(database.SearchResult.search_task_id, func.count(database.SearchResult.id))
            .filter(database.SearchResult.search_task_id.in_(task_ids))
            .group_by(database.SearchResult.search_task_id)
            .all()
        )
        if preview_size:
            ranked_results = (
                db.query(
                    database.SearchResult.id.label("result_id"),
                    func.row_number().over(
                        partition_by=database.SearchResult.search_task_id,
                        order_by=(database.SearchResult.rank.asc(), database.SearchResult.similarity.desc()),
                    ).label("preview_rank"),
                )
                .filter(database.SearchResult.search_task_id.in_(task_ids))
                .subquery()
            )
            preview_rows = (
                db.query(database.SearchResult)
                .join(ranked_results, ranked_results.c.result_id == database.SearchResult.id)
                .filter(ranked_results.c.preview_rank <= preview_size)
                .options(joinedload(database.SearchResult.target).joinedload(database.DetectionTarget.media_file))
                .order_by(
                    database.SearchResult.search_task_id.asc(),
                    database.SearchResult.rank.asc(),
                    database.SearchResult.similarity.desc(),
                )
                .all()
            )
            for row in preview_rows:
                preview_rows_by_task[row.search_task_id].append(row)

    history = []
    for task in tasks:
        _ensure_project_access(task.project_id, username, db)
        filters = json.loads(task.filters_json or "{}")
        history.append({
            "id": task.id,
            "project_id": task.project_id,
            "session_id": str(task.project_id),
            "type": task.query_type,
            "content": task.query_content,
            "filters": filters,
            "filter_summary": _query_filter_summary(filters),
            "query_object_url": signed_asset_url(f"/api/v1/search/tasks/{task.id}/query") if task.query_object_key else None,
            "created_at": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "result_count": result_counts.get(task.id, 0),
            "results": [_search_result_payload(row, None) for row in preview_rows_by_task[task.id]],
        })
    return {
        "status": "success",
        "history": history,
        "pagination": _pagination_payload(page, page_size, total),
    }


@router.get("/api/v1/history/{history_id}")
def v1_get_history_detail(
    history_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    task = db.query(database.SearchTask).filter(database.SearchTask.id == history_id, database.SearchTask.username == username).first()
    if not task:
        raise HTTPException(status_code=404, detail="检索记录不存在")
    _ensure_project_access(task.project_id, username, db)
    result_query = db.query(database.SearchResult).filter(database.SearchResult.search_task_id == task.id)
    total = result_query.count()
    rows = (
        result_query.options(
            joinedload(database.SearchResult.target).joinedload(database.DetectionTarget.media_file)
        )
        .order_by(database.SearchResult.rank.asc(), database.SearchResult.similarity.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    filters = json.loads(task.filters_json or "{}")
    return {
        "status": "success",
        "task": {
            "id": task.id,
            "project_id": task.project_id,
            "type": task.query_type,
            "content": task.query_content,
            "filters": filters,
            "filter_summary": _query_filter_summary(filters),
        "query_object_url": signed_asset_url(f"/api/v1/search/tasks/{task.id}/query") if task.query_object_key else None,
            "created_at": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "result_count": total,
        },
        "pagination": _pagination_payload(page, page_size, total),
        "results": [_search_result_payload(row, None) for row in rows],
    }


@router.get("/api/v1/search/results/{result_id}")
def v1_get_search_result_detail(result_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    row = (
        db.query(database.SearchResult)
        .options(
            joinedload(database.SearchResult.search_task),
            joinedload(database.SearchResult.target).joinedload(database.DetectionTarget.media_file),
        )
        .filter(database.SearchResult.id == result_id)
        .first()
    )
    if not row or row.search_task.username != username:
        raise HTTPException(status_code=404, detail="检索结果不存在")
    _ensure_project_access(row.search_task.project_id, username, db)
    task = row.search_task
    payload = _search_result_payload(row, None)
    payload["task"] = {
        "id": task.id,
        "project_id": task.project_id,
        "type": task.query_type,
        "content": task.query_content,
        "filters": json.loads(task.filters_json or "{}"),
        "created_at": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return payload


@router.get("/api/v1/search/tasks/{task_id}/report")
def v1_export_search_report(
    task_id: int,
    report_format: str = Query("csv", alias="format", pattern="^(csv|json)$"),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    task = db.query(database.SearchTask).filter(database.SearchTask.id == task_id, database.SearchTask.username == username).first()
    if not task:
        raise HTTPException(status_code=404, detail="检索记录不存在")
    _ensure_project_access(task.project_id, username, db)
    rows = (
        db.query(database.SearchResult)
        .options(joinedload(database.SearchResult.target).joinedload(database.DetectionTarget.media_file))
        .filter(database.SearchResult.search_task_id == task.id)
        .order_by(database.SearchResult.rank.asc(), database.SearchResult.similarity.desc())
        .all()
    )
    filters = json.loads(task.filters_json or "{}")
    result_payloads = [_search_result_payload(row, None) for row in rows]

    if report_format == "json":
        return {
            "status": "success",
            "task": {
                "id": task.id,
                "project_id": task.project_id,
                "type": task.query_type,
                "content": task.query_content,
                "filters": filters,
                "created_at": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "results": result_payloads,
        }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "rank",
        "result_id",
        "similarity",
        "query_type",
        "query_content",
        "filters",
        "project_id",
        "media_id",
        "media_filename",
        "media_type",
        "camera_id",
        "timestamp",
        "video_seek_seconds",
        "thumbnail_url",
        "original_media_url",
        "original_media_path",
        "source_info",
    ])
    for item in result_payloads:
        writer.writerow([
            item.get("rank"),
            item.get("id"),
            item.get("similarity"),
            task.query_type,
            task.query_content,
            _query_filter_summary(filters),
            task.project_id,
            item.get("media_id"),
            item.get("media_filename"),
            item.get("media_type"),
            item.get("camera_id"),
            item.get("timestamp"),
            item.get("video_seek_seconds"),
            item.get("thumbnail_url"),
            item.get("original_media_url"),
            item.get("original_media_path"),
            item.get("source_info"),
        ])

    filename = f"myreid_search_report_{task.id}.csv"
    _audit(db, username, "search.report_exported", "project", str(task.project_id), filename)
    db.commit()
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/api/v1/history/{history_id}")
def v1_delete_history(history_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    task = db.query(database.SearchTask).filter(database.SearchTask.id == history_id, database.SearchTask.username == username).first()
    if task:
        _ensure_project_access(task.project_id, username, db)
        db.query(database.SearchResult).filter(database.SearchResult.search_task_id == history_id).delete()
        db.delete(task)
        _audit(db, username, "history.deleted", "search_task", str(history_id), "")
        db.commit()
    return {"status": "success"}


@router.get("/api/v1/jobs/{job_id}", response_model=JobOut)
def v1_get_job(job_id: str, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    db_job = db.query(database.ProcessingJob).filter(database.ProcessingJob.id == job_id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="任务不存在")
    _ensure_project_access(db_job.project_id, username, db)
    try:
        record = job_manager.get(job_id)
    except Exception:
        record = None
    if record:
        return _job_to_schema(record)
    return JobOut(
        id=db_job.id,
        type=db_job.job_type,
        status=db_job.status,
        progress=db_job.progress,
        message=db_job.message,
        error=db_job.error,
        created_at=db_job.created_at,
        updated_at=db_job.updated_at,
    )


@router.post("/api/v1/search/text", response_model=SearchResponse)
def v1_search_text(payload: TextSearchRequest, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    _ensure_project_access(payload.project_id, username, db)
    query_feature = _clip_text_feature(payload.query_text)
    filters = _schema_dict(payload.filters, exclude_none=True)
    return _run_enterprise_search(
        db=db,
        username=username,
        project_id=payload.project_id,
        query_type="text",
        query_content=payload.query_text,
        query_feature=query_feature,
        top_k=payload.top_k,
        page=payload.page,
        page_size=payload.page_size,
        filters=filters,
    )


@router.post("/api/v1/search/image", response_model=SearchResponse)
def v1_search_image(
    project_id: int,
    file: UploadFile = File(...),
    top_k: int = Query(50, ge=1, le=500),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    min_similarity: float = Query(None, ge=-1.0, le=1.0),
    camera_id: str = Query(None),
    media_id: int = Query(None, ge=1),
    start_time: str = Query(None),
    end_time: str = Query(None),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_project_access(project_id, username, db)
    try:
        query_object = storage.save_upload(
            project_id,
            file.filename or "query-image",
            file.file,
            category="queries",
        )
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    try:
        query_path = storage.path_for(query_object.object_key)
        query_image = _load_verified_rgb_image(query_path)
        query_feature = _clip_image_feature(query_image)
    except ImagePixelLimitError as exc:
        storage.delete(query_object.object_key)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        storage.delete(query_object.object_key)
        raise HTTPException(status_code=400, detail="上传文件不是有效图片") from exc
    except Exception:
        storage.delete(query_object.object_key)
        raise
    filters = {
        "min_similarity": min_similarity,
        "camera_id": camera_id,
        "media_id": media_id,
        "start_time": start_time,
        "end_time": end_time,
    }
    try:
        return _run_enterprise_search(
            db=db,
            username=username,
            project_id=project_id,
            query_type="image",
            query_content=file.filename or "query-image",
            query_feature=query_feature,
            top_k=top_k,
            page=page,
            page_size=page_size,
            filters=filters,
            query_object_key=query_object.object_key,
        )
    except Exception:
        storage.delete(query_object.object_key)
        raise


@router.post("/api/v1/index/rebuild")
def v1_index_rebuild(project_id: int, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    _ensure_project_access(project_id, username, db, "manage")
    job = job_manager.create("index_rebuild", "索引重建已排队")
    db.add(database.ProcessingJob(
        id=job.id,
        project_id=project_id,
        job_type=job.type,
        status=job.status,
        progress=job.progress,
        message=job.message,
    ))
    _audit(db, username, "index.rebuild.requested", "project", str(project_id), "")
    db.commit()
    _enqueue_job_or_503(
        db,
        job.id,
        "app.workers.tasks.rebuild_project_index",
        job.id,
        project_id,
        username,
        project_id=project_id,
    )
    return {"status": "accepted", "job_id": job.id}

