import datetime
import json
import uuid
from io import BytesIO

import torch
from PIL import Image


def test_refresh_token_rotation_for_core_and_scenic(client, register_user, register_scenic_user):
    sessions = [
        ("/api/v1/auth", register_user("refresh_core", "refresh-core@example.com")),
        ("/api/v1/scenic/auth", register_scenic_user("refresh_scenic", "refresh-scenic@example.com")),
    ]

    for prefix, session in sessions:
        refreshed = client.post(
            f"{prefix}/refresh",
            headers={"Authorization": f"Bearer {session['refresh_token']}"},
        )
        assert refreshed.status_code == 200, refreshed.text
        tokens = refreshed.json()
        assert tokens["access_token"]
        assert tokens["refresh_token"]
        assert tokens["refresh_token"] != session["refresh_token"]

        me = client.get(
            f"{prefix}/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert me.status_code == 200, me.text
        assert me.json()["username"] == session["username"]

        reused = client.post(
            f"{prefix}/refresh",
            headers={"Authorization": f"Bearer {session['refresh_token']}"},
        )
        assert reused.status_code == 401


def test_job_status_requires_project_access_before_returning_live_state(
    client,
    register_user,
    register_scenic_user,
):
    import database
    from app.api import common as main
    import scenic_database

    core_owner = register_user("job_core_owner", "job-core-owner@example.com")
    core_intruder = register_user("job_core_intruder", "job-core-intruder@example.com")
    core_project = client.post(
        "/api/v1/projects",
        headers=core_owner["headers"],
        json={"name": "Private Core Jobs", "description": "authorization regression"},
    )
    assert core_project.status_code == 200, core_project.text

    core_record = main.job_manager.create("private-core-job", "sensitive core progress")
    with database.SessionLocal() as db:
        db.add(database.ProcessingJob(
            id=core_record.id,
            project_id=core_project.json()["id"],
            job_type=core_record.type,
            status=core_record.status,
            progress=core_record.progress,
            message=core_record.message,
        ))
        db.commit()

    assert client.get(
        f"/api/v1/jobs/{core_record.id}",
        headers=core_owner["headers"],
    ).status_code == 200
    assert client.get(
        f"/api/v1/jobs/{core_record.id}",
        headers=core_intruder["headers"],
    ).status_code == 403

    scenic_owner = register_scenic_user("job_scenic_owner", "job-scenic-owner@example.com")
    scenic_intruder = register_scenic_user("job_scenic_intruder", "job-scenic-intruder@example.com")
    scenic_project = client.post(
        "/api/v1/scenic/projects",
        headers=scenic_owner["headers"],
        json={"name": "Private Scenic Jobs", "description": "authorization regression"},
    )
    assert scenic_project.status_code == 200, scenic_project.text

    scenic_record = main.job_manager.create("private-scenic-job", "sensitive scenic progress")
    with scenic_database.SessionLocal() as db:
        db.add(scenic_database.ProcessingJob(
            id=scenic_record.id,
            project_id=scenic_project.json()["id"],
            job_type=scenic_record.type,
            status=scenic_record.status,
            progress=scenic_record.progress,
            message=scenic_record.message,
        ))
        db.commit()

    assert client.get(
        f"/api/v1/scenic/jobs/{scenic_record.id}",
        headers=scenic_owner["headers"],
    ).status_code == 200
    assert client.get(
        f"/api/v1/scenic/jobs/{scenic_record.id}",
        headers=scenic_intruder["headers"],
    ).status_code == 403


def test_auth_project_admin_and_observability_flow(client, register_user):
    admin = register_user("admin_user", "admin@example.com")

    health = client.get("/api/v1/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = client.get("/api/v1/readyz")
    assert ready.status_code == 200
    assert "database_ready" in ready.json()
    assert "database" not in ready.json()
    assert ready.json()["queue_mode"] == "local"
    assert "queue_local_fallback" in ready.json()["degraded_reasons"]

    me = client.get("/api/v1/auth/me", headers=admin["headers"])
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    created_user = client.post(
        "/api/v1/admin/users",
        headers=admin["headers"],
        json={"username": "viewer1", "email": "viewer1@example.com", "password": "viewer-pass", "role": "viewer"},
    )
    assert created_user.status_code == 201, created_user.text
    assert created_user.json()["role"] == "viewer"

    project = client.post(
        "/api/v1/projects",
        headers=admin["headers"],
        json={"name": "Integration Project", "description": "api test"},
    )
    assert project.status_code == 200, project.text
    project_id = project.json()["id"]

    assert client.get("/api/v1/projects", headers=admin["headers"]).status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/status", headers=admin["headers"]).status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/preview", headers=admin["headers"]).status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/media", headers=admin["headers"]).status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/jobs", headers=admin["headers"]).status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/audit", headers=admin["headers"]).status_code == 200
    assert client.get("/api/v1/database/status", headers=admin["headers"]).json()["status"] == "ok"
    assert client.get("/api/v1/metrics", headers=admin["headers"]).status_code == 200
    assert client.get("/api/v1/observability", headers=admin["headers"]).status_code == 200
    prometheus = client.get("/metrics")
    assert prometheus.status_code == 200
    assert "# HELP myreid_http_requests_total" in prometheus.text
    assert "myreid_queue_ready" in prometheus.text


def test_viewer_can_read_media_detail(client, register_user):
    import database

    owner = register_user("media_owner", "media-owner@example.com")
    viewer_password = "viewer-pass"
    admin_create_viewer = client.post(
        "/api/v1/admin/users",
        headers=owner["headers"],
        json={"username": "media_viewer", "email": "media-viewer@example.com", "password": viewer_password, "role": "viewer"},
    )
    assert admin_create_viewer.status_code == 201, admin_create_viewer.text
    project = client.post(
        "/api/v1/projects",
        headers=owner["headers"],
        json={"name": "Viewer Media Project", "description": "read permission"},
    )
    assert project.status_code == 200, project.text
    project_id = project.json()["id"]
    member = client.put(
        f"/api/v1/projects/{project_id}/members/media_viewer",
        headers=owner["headers"],
        json={"role": "viewer"},
    )
    assert member.status_code == 200, member.text

    db = database.SessionLocal()
    try:
        media = database.MediaFile(
            project_id=project_id,
            uploader_username=owner["username"],
            filename="detail.jpg",
            object_key="projects/1/originals/detail.jpg",
            content_type="image/jpeg",
            size_bytes=10,
            sha256="viewer-detail",
            media_type="image",
            status="indexed",
        )
        db.add(media)
        db.commit()
        media_id = media.id
    finally:
        db.close()

    viewer_login = client.post("/api/v1/auth/login", json={"username": "media_viewer", "password": viewer_password})
    assert viewer_login.status_code == 200, viewer_login.text
    viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
    detail = client.get(f"/api/v1/media/{media_id}", headers=viewer_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == media_id


def test_duplicate_upload_reuses_existing_media_without_new_row(client, register_user, monkeypatch):
    import database
    from app.api import common as main
    from app.api.routes import projects as project_routes

    user = register_user("duplicate_user", "duplicate@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=user["headers"],
        json={"name": "Duplicate Project", "description": "dedupe"},
    )
    assert project.status_code == 200, project.text
    project_id = project.json()["id"]
    monkeypatch.setattr(project_routes, "_enqueue_job", lambda *args: None)

    image = Image.new("RGB", (64, 128), "green")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    payload = buffer.getvalue()

    first = client.post(
        f"/api/v1/projects/{project_id}/media",
        headers=user["headers"],
        files={"file": ("person.jpg", BytesIO(payload), "image/jpeg")},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "accepted"
    first_media_id = first.json()["media_id"]

    second = client.post(
        f"/api/v1/projects/{project_id}/media",
        headers=user["headers"],
        files={"file": ("person-copy.jpg", BytesIO(payload), "image/jpeg")},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "duplicate"
    assert second.json()["existing_media_id"] == first_media_id

    db = database.SessionLocal()
    try:
        assert db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id).count() == 1
    finally:
        db.close()


def test_text_search_persists_results_history_and_csv_report(client, register_user, monkeypatch):
    import database
    from app.api import common as main
    from app.api.routes import search as search_routes
    from app.services.vector_store import VectorPoint

    user = register_user("search_user", "search@example.com")
    project_response = client.post(
        "/api/v1/projects",
        headers=user["headers"],
        json={"name": "Search Project", "description": "search test"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]

    vector_id = str(uuid.uuid4())
    db = database.SessionLocal()
    try:
        media = database.MediaFile(
            project_id=project_id,
            uploader_username=user["username"],
            filename="person.jpg",
            object_key="projects/1/originals/person.jpg",
            content_type="image/jpeg",
            size_bytes=123,
            sha256="abc123",
            media_type="image",
            status="indexed",
        )
        db.add(media)
        db.flush()
        target = database.DetectionTarget(
            project_id=project_id,
            media_id=media.id,
            crop_path="projects/1/crops/person.jpg",
            crop_object_key="projects/1/crops/person.jpg",
            source_info="person.jpg",
            vector_id=vector_id,
        )
        db.add(target)
        db.commit()
        target_id = target.id
        media_id = media.id
    finally:
        db.close()

    main.vector_store.upsert(
        [
            VectorPoint(
                id=vector_id,
                vector=torch.tensor([1.0, 0.0], dtype=torch.float32),
                path="projects/1/crops/person.jpg",
                metadata={
                    "target_id": target_id,
                    "project_id": project_id,
                    "media_id": media_id,
                    "source_info": "person.jpg",
                },
            )
        ]
    )
    monkeypatch.setattr(search_routes, "_clip_text_feature", lambda text: torch.tensor([[1.0, 0.0]], dtype=torch.float32))

    search = client.post(
        "/api/v1/search/text",
        headers=user["headers"],
        json={
            "project_id": project_id,
            "query_text": "person",
            "top_k": 5,
            "filters": {"min_similarity": 0.1},
        },
    )
    assert search.status_code == 200, search.text
    payload = search.json()
    assert payload["results"][0]["media_id"] == media_id
    assert payload["pagination"]["total"] == 1

    history = client.get("/api/v1/history", headers=user["headers"])
    assert history.status_code == 200
    assert history.json()["history"][0]["content"] == "person"

    detail = client.get(f"/api/v1/history/{payload['task_id']}", headers=user["headers"])
    assert detail.status_code == 200
    assert detail.json()["task"]["filters"] == {"min_similarity": 0.1}

    report = client.get(f"/api/v1/search/tasks/{payload['task_id']}/report?format=csv", headers=user["headers"])
    assert report.status_code == 200
    assert "person.jpg" in report.text


def test_camera_recording_surfaces_in_status_media_and_search(client, register_user, monkeypatch):
    import database
    from app.api import common as main
    from app.api.routes import search as search_routes
    from app.services.vector_store import VectorPoint

    user = register_user("camera_search_user", "camera-search@example.com")
    project_response = client.post(
        "/api/v1/projects",
        headers=user["headers"],
        json={"name": "Camera Recording Project", "description": "camera", "project_type": "camera", "camera_source": "0"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]
    started_at = datetime.datetime(2026, 5, 21, 1, 2, 3, tzinfo=datetime.UTC)
    captured_at = started_at + datetime.timedelta(seconds=4.25)
    vector_id = str(uuid.uuid4())

    db = database.SessionLocal()
    try:
        media = database.MediaFile(
            project_id=project_id,
            uploader_username=user["username"],
            filename="camera-recording.mp4",
            object_key="projects/1/media/1/recordings/camera-recording.mp4",
            content_type="video/mp4",
            size_bytes=2048,
            sha256="camera-recording",
            media_type="camera",
            status="stopped",
            created_at=started_at,
        )
        db.add(media)
        db.flush()
        media_path = main.storage.path_for(media.object_key)
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(b"private-camera-recording")
        target = database.DetectionTarget(
            project_id=project_id,
            media_id=media.id,
            crop_path="projects/1/crops/camera-person.jpg",
            crop_object_key="projects/1/crops/camera-person.jpg",
            source_info="camera target",
            camera_id="0",
            timestamp=captured_at.isoformat(),
            vector_id=vector_id,
        )
        db.add(target)
        db.commit()
        target_id = target.id
        media_id = media.id
    finally:
        db.close()

    main.vector_store.upsert(
        [
            VectorPoint(
                id=vector_id,
                vector=torch.tensor([1.0, 0.0], dtype=torch.float32),
                path="projects/1/crops/camera-person.jpg",
                metadata={
                    "target_id": target_id,
                    "project_id": project_id,
                    "media_id": media_id,
                    "camera_id": "0",
                    "timestamp": captured_at.isoformat(),
                    "source_info": "camera target",
                },
            )
        ]
    )
    monkeypatch.setattr(search_routes, "_clip_text_feature", lambda text: torch.tensor([[1.0, 0.0]], dtype=torch.float32))

    status = client.get(f"/api/v1/projects/{project_id}/camera/status", headers=user["headers"])
    assert status.status_code == 200, status.text
    expected_media_url = f"/api/v1/projects/{project_id}/media/{media_id}/content"
    signed_media_url = status.json()["recording_url"]
    assert signed_media_url.startswith(f"{expected_media_url}?asset_expires=")
    assert "&asset_signature=" in signed_media_url
    assert client.get(expected_media_url).status_code == 401
    signed_content = client.get(signed_media_url)
    assert signed_content.status_code == 200
    assert signed_content.content == b"private-camera-recording"
    assert client.get(f"{signed_media_url}x").status_code == 401
    content = client.get(expected_media_url, headers=user["headers"])
    assert content.status_code == 200
    assert content.content == b"private-camera-recording"

    media_list = client.get(f"/api/v1/projects/{project_id}/media", headers=user["headers"])
    assert media_list.status_code == 200, media_list.text
    media_payload = media_list.json()["media"][0]
    assert media_payload["object_url"].startswith(f"{expected_media_url}?asset_expires=")
    assert media_payload["targets"][0]["video_seek_seconds"] == 4.25

    search = client.post(
        "/api/v1/search/text",
        headers=user["headers"],
        json={"project_id": project_id, "query_text": "camera person", "top_k": 5},
    )
    assert search.status_code == 200, search.text
    result = search.json()["results"][0]
    assert result["original_media_url"].startswith(f"{expected_media_url}?asset_expires=")
    assert result["video_seek_seconds"] == 4.25
