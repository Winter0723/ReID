from types import SimpleNamespace
from unittest.mock import Mock


def test_light_camera_status_keeps_polling_live_frames_after_restart(client, register_user, monkeypatch):
    from app.api.routes import projects
    from app.services import media_service

    user = register_user()
    response = client.post("/api/v1/projects", headers=user["headers"], json={
        "name": "Live camera", "project_type": "camera", "camera_source": "3",
    })
    assert response.status_code == 200, response.text
    project_id = response.json()["id"]
    state = {"running": True, "camera_source": "3", "indexed_targets": 17}
    monitor = {"running": True, "source": "3", "latest_frame_url": "/gallery/central_camera/3/latest.jpg", "latest_frame_version": 41}
    monkeypatch.setattr(projects.camera_stream_manager, "status", lambda _project: dict(state))
    monkeypatch.setattr(media_service, "find_camera_service", lambda _source: SimpleNamespace(status=lambda: dict(monitor)))
    url = f"/api/v1/projects/{project_id}/camera/status"
    for running, version in [(True, 41), (False, 41), (True, 42), (True, 43)]:
        state["running"] = running
        monitor.update(running=running, latest_frame_version=version)
        response = client.get(url, params={"light": "true"}, headers=user["headers"])
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["running"] is running
        assert payload["indexed_targets"] == 17
        assert payload["monitor_status"]["latest_frame_version"] == version
        assert payload["monitor_source"] == "3"


def test_light_camera_status_does_not_query_project_statistics(monkeypatch, tmp_path):
    from app.api.routes import projects

    monkeypatch.setattr(projects, "BASE_GALLERY_DIR", str(tmp_path))
    monkeypatch.setattr(projects.camera_stream_manager, "status", lambda _project: {"running": True, "indexed_targets": 5})
    monkeypatch.setattr(projects, "_merge_central_camera_status", lambda payload, **_kwargs: payload)
    db = Mock()
    db.query.side_effect = AssertionError("Light frame polling must not query targets or recordings")
    result = projects._camera_status_payload(7, SimpleNamespace(project_type="camera", camera_source="3"), db, light=True)
    assert result["indexed_targets"] == 5
    assert result["running"] is True
