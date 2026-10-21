import os


def create_media_record(
    db,
    media_model,
    *,
    project_id: int,
    username: str,
    filename: str | None = None,
    media_type: str,
    stored,
):
    media = media_model(
        project_id=project_id,
        uploader_username=username,
        filename=stored.filename,
        object_key=stored.object_key,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        media_type=media_type,
        status="uploaded",
    )
    db.add(media)
    db.flush()
    return media


def find_duplicate_media(db, media_model, project_id: int, sha256: str):
    if not sha256:
        return None
    return (
        db.query(media_model)
        .filter(
            media_model.project_id == project_id,
            media_model.sha256 == sha256,
            media_model.status != "failed",
            media_model.status != "duplicate",
        )
        .order_by(media_model.created_at.asc())
        .first()
    )


def encode_image_path_as_source(filename: str) -> str:
    if filename.startswith("[VID]_"):
        parts = filename.replace("[VID]_", "").split("_time_")
        if len(parts) == 2:
            video_name = parts[0]
            time_and_id = parts[1].split("_p")
            time_string = time_and_id[0].replace("-", ":")
            person_id = time_and_id[1].replace(".jpg", "") if len(time_and_id) > 1 else "0"
            return f"🎬 {video_name}\n⏱️ {time_string} (目标 {person_id})"
    if filename.startswith("[CAM]_"):
        parts = filename.replace("[CAM]_", "").split("_p")
        person_id = parts[1].replace(".jpg", "") if len(parts) > 1 else "0"
        return f"📷 Camera\n⏱️ 实时采集 (目标 {person_id})"
    return f"🖼️ {filename}"


def persist_hit_result(db, search_result_model, task_id: int, hit, rank: int):
    similarity = round(float(hit.metadata.get("similarity", 0.0)), 4)
    source_info = hit.metadata.get("source_info") or encode_image_path_as_source(os.path.basename(hit.path))
    result = search_result_model(
        search_task_id=task_id,
        target_id=hit.metadata.get("target_id"),
        rank=rank,
        path=hit.path,
        similarity=similarity,
        source_info=source_info,
    )
    db.add(result)
    db.flush()
    return result
