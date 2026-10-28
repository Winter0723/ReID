"""Visit boundaries must apply before ranking and before persisting references."""

import datetime as dt
import json

import pytest
import torch


START = dt.datetime(2026, 10, 20, 2, tzinfo=dt.UTC)
END = START + dt.timedelta(minutes=10)


@pytest.fixture
def visit(client, register_scenic_user, monkeypatch):
    import scenic_database as dbm
    from app.api.scenic import tourist_routes

    user = register_scenic_user()
    project_id = client.post("/api/v1/scenic/projects", headers=user["headers"],
                             json={"name": "Visit window"}).json()["id"]
    with dbm.SessionLocal() as db:
        session = dbm.TouristSession(
            project_id=project_id, ticket_code="WINDOW", created_by_username=user["username"],
            entry_image_object_key="entry.jpg", created_at=START, checked_out_at=END,
        )
        db.add(session)
        db.commit()
        session_id = session.id
    monkeypatch.setattr(tourist_routes, "_tourist_entry_feature", lambda _: torch.tensor([[1., 0.]]))
    monkeypatch.setattr(tourist_routes, "solidify_project_camera_recordings", lambda *args: None)
    return user, project_id, session_id


def add_hit(visit, name, captured, *, score=1., media_type="camera", relative=False, annotated=False):
    import scenic_database as dbm
    from app.services.vector_store import VectorPoint, scenic_vector_store

    user, project_id, _ = visit
    origin = captured - dt.timedelta(seconds=10)
    timestamp = "00:00:10" if relative else captured.isoformat()
    with dbm.SessionLocal() as db:
        media = dbm.MediaFile(
            project_id=project_id, uploader_username=user["username"], filename=f"{name}.mp4",
            object_key=f"{name}.mp4", media_type=media_type, status="indexed", created_at=origin,
        )
        db.add(media)
        db.flush()
        if annotated:
            db.add(dbm.ScenicMediaMetadata(media_id=media.id, recorded_at=origin))
        target = dbm.DetectionTarget(
            project_id=project_id, media_id=media.id, crop_path=f"{name}.jpg",
            crop_object_key=f"{name}.jpg", vector_id=name, timestamp=timestamp,
            source_info="video_seek_seconds=10.000", camera_id=name,
        )
        db.add(target)
        db.commit()
        point = VectorPoint(name, torch.tensor([score, 0.]), f"{name}.jpg",
                            {"project_id": project_id, "media_id": media.id, "target_id": target.id,
                             "timestamp": timestamp, "similarity": score})
    scenic_vector_store.upsert([point])
    return point


def match(client, visit, **payload):
    user, _, session_id = visit
    response = client.post(f"/api/v1/scenic/tourists/{session_id}/match",
                           headers=user["headers"], json={"min_similarity": 0.1, **payload})
    assert response.status_code == 200, response.text
    return response.json()


def test_window_filters_before_top_k_and_persists_only_current_visit(client, visit):
    import scenic_database as dbm
    from app.services.camera_lifecycle import recording_is_protected

    old = add_hit(visit, "yesterday", START - dt.timedelta(days=1))
    add_hit(visit, "later", END + dt.timedelta(seconds=1))
    current = add_hit(visit, "current", START + dt.timedelta(minutes=1), score=0.8)
    result = match(client, visit, top_k=1)
    assert [s["target_id"] for s in result["segments"]] == [current.metadata["target_id"]]
    with dbm.SessionLocal() as db:
        assert [r.target_id for r in db.query(dbm.SearchResult).all()] == [current.metadata["target_id"]]
        filters = json.loads(db.query(dbm.SearchTask).one().filters_json)
        assert filters["start_time"] == START.isoformat()
        assert filters["end_time"] == END.isoformat()
        media = db.get(dbm.MediaFile, old.metadata["media_id"])
        assert not recording_is_protected(db, media, START - dt.timedelta(hours=23))


@pytest.mark.parametrize("captured,expected", [(START, (10., 12.)), (END, (8., 10.))])
def test_padding_is_clipped_to_visit_boundaries(client, visit, captured, expected):
    add_hit(visit, "edge", captured)
    segment = match(client, visit, clip_padding_seconds=2)["segments"][0]
    assert (segment["start_seconds"], segment["end_seconds"]) == expected


@pytest.mark.parametrize("media_type,annotated", [("camera", False), ("video", True)])
def test_relative_recordings_use_recording_origin_before_ranking(client, visit, media_type, annotated):
    add_hit(visit, "old-relative", START - dt.timedelta(days=1),
            relative=True, media_type=media_type, annotated=annotated)
    current = add_hit(visit, "current-relative", START + dt.timedelta(minutes=1),
                      relative=True, media_type=media_type, annotated=annotated, score=0.8)
    assert match(client, visit, top_k=1)["segments"][0]["target_id"] == current.metadata["target_id"]


def test_unknown_video_capture_time_does_not_use_upload_time(client, visit):
    import scenic_database as dbm

    add_hit(visit, "unknown", START + dt.timedelta(minutes=1), relative=True, media_type="video")
    assert match(client, visit)["segments"] == []
    with dbm.SessionLocal() as db:
        assert db.query(dbm.SearchResult).count() == 0


def test_stale_index_hit_cannot_leave_history_or_segment_reference(client, visit, monkeypatch):
    import scenic_database as dbm
    from app.api.scenic import tourist_routes

    stale = add_hit(visit, "stale", START - dt.timedelta(seconds=1))
    current = add_hit(visit, "current", START + dt.timedelta(minutes=1))
    with dbm.SessionLocal() as db:
        for hit in (stale, current):
            db.add(dbm.TouristVisitSegment(
                session_id=visit[2], project_id=visit[1], target_id=hit.metadata["target_id"],
                media_id=hit.metadata["media_id"], included=False,
            ))
        db.commit()
    monkeypatch.setattr(tourist_routes.vector_store, "search", lambda *args, **kwargs: [stale, current])
    match(client, visit)
    with dbm.SessionLocal() as db:
        assert [r.target_id for r in db.query(dbm.SearchResult).all()] == [current.metadata["target_id"]]
        assert [r.target_id for r in db.query(dbm.TouristVisitSegment).all()] == [current.metadata["target_id"]]
        assert not db.query(dbm.TouristVisitSegment).one().included


def test_invalid_visit_range_is_rejected_before_search(client, visit, monkeypatch):
    import scenic_database as dbm
    from app.api.scenic import tourist_routes

    with dbm.SessionLocal() as db:
        db.get(dbm.TouristSession, visit[2]).checked_out_at = START - dt.timedelta(seconds=1)
        db.commit()
    def unexpected(_):
        pytest.fail("invalid visit must not run inference")
    monkeypatch.setattr(tourist_routes, "_tourist_entry_feature", unexpected)
    response = client.post(f"/api/v1/scenic/tourists/{visit[2]}/match",
                           headers=visit[0]["headers"], json={})
    assert response.status_code == 409


def test_staff_checkout_is_fixed_before_matching_and_survives_failure(client, visit, monkeypatch):
    import scenic_database as dbm
    from app.api.scenic import tourist_routes
    from app.schemas import TouristMatchRequest
    from app.services.time_utils import as_utc

    user, _, session_id = visit
    with dbm.SessionLocal() as db:
        db.get(dbm.TouristSession, session_id).checked_out_at = None
        db.commit()
    monkeypatch.setattr(tourist_routes, "_utc_now", lambda: END)
    def fail(_):
        raise RuntimeError("model unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(tourist_routes, "_tourist_entry_feature", fail)
        with dbm.SessionLocal() as db, pytest.raises(RuntimeError, match="model unavailable"):
            tourist_routes.v1_match_tourist_segments(session_id, TouristMatchRequest(), user["username"], db)
    with dbm.SessionLocal() as db:
        session = db.get(dbm.TouristSession, session_id)
        assert as_utc(session.checked_out_at) == END
        assert session.status == "checked_out"
    monkeypatch.setattr(tourist_routes, "_utc_now", lambda: END + dt.timedelta(days=1))
    add_hit(visit, "after-checkout", END + dt.timedelta(seconds=10))
    assert match(client, visit)["segments"] == []
    with dbm.SessionLocal() as db:
        assert as_utc(db.get(dbm.TouristSession, session_id).checked_out_at) == END


def test_core_search_still_uses_whole_project(isolated_model_binding):
    from app.services.vector_store import VectorPoint, vector_store

    vector_store.upsert([
        VectorPoint(str(index), torch.tensor([1., 0.]), "crop.jpg",
                    {"project_id": project_id, "timestamp": timestamp})
        for index, (project_id, timestamp) in enumerate([
            (1, (START - dt.timedelta(days=30)).isoformat()), (1, END.isoformat()),
            (1, "00:00:10"), (2, START.isoformat()),
        ])
    ])
    assert {hit.id for hit in vector_store.search(torch.tensor([1., 0.]), top_k=10, project_id=1)} == {"0", "1", "2"}
