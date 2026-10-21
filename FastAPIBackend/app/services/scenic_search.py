"""Search one visit, including recordings indexed with relative video offsets."""

from sqlalchemy import or_

import scenic_database as database
from app.services.time_utils import as_utc


def search_visit(db, vector_store, session, query_feature, *, top_k, filters):
    # Live camera hits carry absolute capture timestamps. Apply the visit window
    # before ranking, so old high-scoring hits cannot crowd out this visit.
    hits = vector_store.search(
        query_feature, top_k=top_k, project_id=session.project_id, filters=filters,
    )
    # Simulation/imported videos use HH:MM:SS offsets instead. Resolve their
    # windows from recording time, never from an ordinary video's upload time.
    media = database.MediaFile
    metadata = database.ScenicMediaMetadata
    target = database.DetectionTarget
    relative_targets = db.query(target.id).filter(
        target.media_id == media.id,
        target.project_id == session.project_id,
        target.timestamp.contains(":"),
        ~target.timestamp.contains("-"),
    ).exists()
    sources = (
        db.query(media.id, media.media_type, media.created_at, metadata.recorded_at)
        .outerjoin(metadata, metadata.media_id == media.id)
        .filter(
            media.project_id == session.project_id,
            relative_targets,
            or_(media.media_type == "camera", metadata.recorded_at.isnot(None)),
        ).all()
    )
    for media_id, media_type, created_at, recorded_at in sources:
        origin = as_utc(recorded_at or (created_at if media_type == "camera" else None))
        if origin is None:
            continue
        start = max(0.0, (as_utc(session.created_at) - origin).total_seconds())
        end = (as_utc(session.checked_out_at) - origin).total_seconds()
        if end < start:
            continue
        hits.extend(vector_store.search(
            query_feature, top_k=top_k, project_id=session.project_id,
            filters={**filters, "media_id": media_id, "start_time": start, "end_time": end},
        ))
    unique = {hit.id: hit for hit in hits}
    return sorted(unique.values(), key=lambda hit: float(hit.metadata.get("similarity", 0)), reverse=True)[:top_k]
