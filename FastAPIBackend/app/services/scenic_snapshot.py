"""Freeze old-client selections without changing the legacy rendering contract."""

from types import SimpleNamespace


def freeze_legacy_segments(segments):
    records = []
    fields = ("id", "media_id", "start_seconds", "end_seconds", "camera_name", "camera_id", "timestamp", "similarity", "thumbnail_object_key", "original_media_object_key", "sort_order")
    for segment in segments:
        row = {key: getattr(segment, key) for key in fields}
        media = segment.media_file
        row["media_file"] = {key: getattr(media, key, None) for key in ("id", "object_key", "media_type", "content_type", "filename")} if media else None
        records.append(row)
    return {"legacy_segments": records}


def thaw_legacy_segments(snapshot):
    result = []
    for record in snapshot["legacy_segments"]:
        row = dict(record)
        row["media_file"] = SimpleNamespace(**row["media_file"]) if row["media_file"] else None
        result.append(SimpleNamespace(**row))
    return result
