from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image


def create_point(client, user, source="7"):
    project = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": "Preview park"})
    assert project.status_code == 200, project.text
    project_id = project.json()["id"]
    point = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points", headers=user["headers"],
        json={"name": "Lake camera", "camera_source": source, "role": "play"},
    )
    assert point.status_code == 200, point.text
    return project_id, point.json()["id"]


def test_preview_lease_recovers_and_expires_without_releasing_other_consumers(monkeypatch):
    from app.services import central_camera_service as camera_module
    timers = []

    class FakeTimer:
        def __init__(self, interval, callback, args):
            self.callback, self.args, self.cancelled = callback, args, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def fire(self):
            self.callback(*self.args)

    monkeypatch.setattr(camera_module.threading, "Timer", FakeTimer)
    service = camera_module.CentralCameraService("preview-test", use_environment_source=False)
    start, stop = Mock(), Mock()
    monkeypatch.setattr(service, "_start_thread", start)
    monkeypatch.setattr(service, "_stop_thread", stop)
    service.acquire()  # Existing core ingestion also owns this physical source.
    service.preview()
    service.preview()  # Renewal restarts the capture if it stopped unexpectedly.
    assert start.call_count == 3
    assert service._consumer_count == 2
    assert timers[0].cancelled
    timers[0].fire()  # A cancelled callback can already be waiting for its lock.
    assert service._consumer_count == 2
    timers[1].fire()
    assert service._consumer_count == 1
    stop.assert_not_called()
    service.release()
    assert service._consumer_count == 0
    stop.assert_called_once()
    service.preview()
    service.stop()
    assert timers[2].cancelled
    timers[2].fire()
    assert service._consumer_count == 0
    assert stop.call_count == 2


def test_preview_opens_selected_source_without_ingestion_and_rejects_disabled_points(client, register_scenic_user, monkeypatch):
    from app.api.scenic import camera_routes
    from app.services import media_service
    import scenic_database as database
    user = register_scenic_user("preview_user", "preview@example.com")
    project_id, point_id = create_point(client, user)
    service = SimpleNamespace(preview=Mock())
    get_service = Mock(return_value=service)
    start_ingestion = Mock(side_effect=AssertionError("preview must not start ingestion"))
    monkeypatch.setattr(camera_routes, "get_camera_service", get_service)
    monkeypatch.setattr(media_service, "find_camera_service", lambda _source: None)
    monkeypatch.setattr(camera_routes.camera_stream_manager, "start", start_ingestion)
    url = f"/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/preview"
    response = client.post(url, headers=user["headers"])
    assert response.status_code == 200, response.text
    get_service.assert_called_once_with("7")
    service.preview.assert_called_once_with()
    start_ingestion.assert_not_called()
    assert response.json()["camera"]["id"] == response.json()["camera"]["point_id"] == point_id
    with database.SessionLocal() as db:
        assert db.query(database.ProcessingJob).filter_by(project_id=project_id).count() == 0
        assert db.query(database.MediaFile).filter_by(project_id=project_id).count() == 0
        db.query(database.ScenicCameraPoint).filter_by(id=point_id).update({"is_active": False})
        db.commit()
    assert client.post(url, headers=user["headers"]).status_code == 404
    assert get_service.call_count == 1


def test_preview_checks_write_access_and_point_project_before_opening_camera(client, register_scenic_user, monkeypatch):
    from app.api.scenic import camera_routes
    import scenic_database as database
    user = register_scenic_user("preview_permissions", "preview-permissions@example.com")
    project_id, point_id = create_point(client, user)
    other_project_id, _ = create_point(client, user, source="8")
    get_service = Mock(side_effect=AssertionError("invalid request opened a camera"))
    monkeypatch.setattr(camera_routes, "get_camera_service", get_service)
    wrong_project = client.post(
        f"/api/v1/scenic/projects/{other_project_id}/camera-points/{point_id}/preview", headers=user["headers"],
    )
    assert wrong_project.status_code == 404
    with database.SessionLocal() as db:
        db.query(database.User).filter_by(username=user["username"]).update({"role": "viewer"})
        db.commit()
    read_only = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/preview", headers=user["headers"],
    )
    assert read_only.status_code == 403
    get_service.assert_not_called()


def test_stopped_ingestion_uses_current_source_and_snapshot_instead_of_stale_jpeg(client, register_scenic_user, monkeypatch, tmp_path):
    from app.api.scenic import camera_routes, system_routes
    from app.services import media_service
    user = register_scenic_user("fresh_preview", "fresh-preview@example.com")
    project_id, point_id = create_point(client, user)
    stream_key = system_routes._scenic_camera_stream_key(project_id, point_id)
    old_url = f"/gallery/scenic_project_{project_id}/camera/{stream_key}/latest.jpg"
    stale_path = tmp_path / f"scenic_project_{project_id}" / "camera" / str(stream_key) / "latest.jpg"
    stale_path.parent.mkdir(parents=True)
    Image.new("RGB", (24, 24), "red").save(stale_path)
    monitor = {"running": True, "source": "7", "latest_frame_url": "/gallery/central_camera/7/latest.jpg", "latest_frame_version": 42}
    current_frame = np.full((24, 24, 3), (0, 255, 0), dtype=np.uint8)
    service = SimpleNamespace(status=lambda: dict(monitor), latest_frame=lambda: (current_frame, None, 42))
    find_service = Mock(return_value=service)
    find_monitor = Mock(return_value=service)
    monkeypatch.setattr(camera_routes, "find_camera_service", find_service)
    monkeypatch.setattr(media_service, "find_camera_service", find_monitor)
    monkeypatch.setattr(camera_routes, "BASE_GALLERY_DIR", str(tmp_path))
    monkeypatch.setattr(system_routes, "BASE_GALLERY_DIR", str(tmp_path))
    ingest = {"running": False, "camera_source": "old-source", "latest_frame_url": old_url, "latest_frame_version": 1}
    monkeypatch.setattr(system_routes.camera_stream_manager, "status", lambda *args, **kwargs: dict(ingest))
    wall_url = f"/api/v1/scenic/projects/{project_id}/camera-wall"
    wall = client.get(wall_url, headers=user["headers"])
    assert wall.status_code == 200, wall.text
    assert wall.json()["camera_wall"][0]["latest_frame_url"].split("?", 1)[0] == monitor["latest_frame_url"]
    assert wall.json()["camera_wall"][0]["latest_frame_version"] == 42
    find_monitor.assert_called_with("7")
    snapshot_url = f"/api/v1/scenic/projects/{project_id}/camera/snapshot?point_id={point_id}"
    snapshot = client.post(snapshot_url, headers=user["headers"])
    assert snapshot.status_code == 200, snapshot.text
    find_service.assert_called_once_with("7")
    saved = client.get(snapshot.json()["snapshot_url"], headers=user["headers"])
    assert saved.status_code == 200, saved.text
    pixel = Image.open(BytesIO(saved.content)).convert("RGB").getpixel((12, 12))
    assert pixel[1] > 200 and pixel[0] < 20  # Current green image, not old red JPEG.
    monitor["latest_frame_url"] = ""
    assert client.get(wall_url, headers=user["headers"]).json()["camera_wall"][0]["latest_frame_url"] == ""
    monitor["running"] = False
    assert client.get(wall_url, headers=user["headers"]).json()["camera_wall"][0]["latest_frame_url"] == ""
    assert client.post(snapshot_url, headers=user["headers"]).status_code == 400
    # Slow ingestion must not hold back the live capture preview.
    ingest.update(running=True, camera_source="7")
    monitor.update(running=True, latest_frame_url="/gallery/central_camera/7/latest.jpg")
    active = client.get(wall_url, headers=user["headers"])
    assert active.json()["camera_wall"][0]["latest_frame_url"].split("?", 1)[0] == monitor["latest_frame_url"]
    assert active.json()["camera_wall"][0]["latest_frame_version"] == monitor["latest_frame_version"]


def test_delayed_core_release_does_not_stop_new_scenic_consumer(monkeypatch):
    from app.services.central_camera_service import CentralCameraService

    service = CentralCameraService("handoff", use_environment_source=False)
    monkeypatch.setattr(service, "_start_thread", Mock())
    service.acquire()
    original_stop = service._stop_thread

    def delayed_stop():
        service.acquire()  # Scenic enters after core's last release decision.
        original_stop()

    monkeypatch.setattr(service, "_stop_thread", delayed_stop)
    service.release()
    assert service.status()["consumers"] == 1
    assert not service._stop_event.is_set()
    monkeypatch.setattr(service, "_stop_thread", original_stop)
    service.release()
    assert service._stop_event.is_set()


def test_scenic_start_waits_for_core_capture_to_exit_without_reopening(monkeypatch):
    from app.services.central_camera_service import CentralCameraService, CameraSourceBusyError
    import pytest

    service = CentralCameraService("handoff", use_environment_source=False)
    old_thread = Mock()
    old_thread.is_alive.return_value = True
    service._thread = old_thread
    service._stop_event.set()
    with pytest.raises(CameraSourceBusyError):
        service.acquire()
    assert service._thread is old_thread
    assert service._stop_event.is_set()
    assert service.status()["consumers"] == 0
    old_thread.join.assert_called_once_with(timeout=5)


def test_start_all_busy_source_returns_retryable_response_and_rolls_back_new_streams(
    client, register_scenic_user, monkeypatch,
):
    from app.api.scenic import camera_routes
    from app.services.central_camera_service import CameraSourceBusyError

    user = register_scenic_user("busy_camera", "busy-camera@example.com")
    project_id, point_id = create_point(client, user)
    response = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points", headers=user["headers"],
        json={"name": "Second camera", "camera_source": "8", "role": "play"},
    )
    assert response.status_code == 200
    manager = camera_routes.camera_stream_manager
    monkeypatch.setattr(manager, "is_running", Mock(return_value=False))
    start = Mock(side_effect=[{}, CameraSourceBusyError("Camera is still stopping")])
    monkeypatch.setattr(manager, "start", start)
    cleanup = Mock()
    monkeypatch.setattr(camera_routes, "_stop_scenic_stream_keys", cleanup)
    response = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points/start-all",
        headers=user["headers"], json={"camera_source": "7"},
    )
    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == "2"
    cleanup.assert_called_once_with(
        project_id, [camera_routes._scenic_camera_stream_key(project_id, point_id)],
    )


def test_camera_success_clears_previous_cached_frame_error():
    from app.services.camera_stream import scenic_camera_stream_manager
    from app.services.job_manager import job_manager
    record = job_manager.create("camera_stream")
    state = SimpleNamespace(job_id=record.id)
    db_job = SimpleNamespace()
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = db_job
    scenic_camera_stream_manager._update_job(db, state, status="running", progress=0.5, message="retrying", error="old overflow")
    assert db_job.error == "old overflow"
    scenic_camera_stream_manager._update_job(db, state, status="running", progress=0.5, message="indexed")
    assert not db_job.error
    assert not job_manager.get(record.id).error


def test_startup_reconciles_orphans_with_camera_auto_start_disabled(monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from app.api import app_factory
    from app.core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "camera_auto_start", False)
    reconcile = Mock(return_value={"media": 0, "jobs": 0})
    monkeypatch.setattr(app_factory, "reconcile_orphaned_camera_streams", reconcile)
    monkeypatch.setattr(app_factory, "load_models", Mock())
    monkeypatch.setattr(app_factory.vector_store, "ensure_ready", Mock())
    monkeypatch.setattr(app_factory.scenic_vector_store, "ensure_ready", Mock())
    monkeypatch.setattr(app_factory.realtime.manager, "start", Mock())
    monkeypatch.setattr(app_factory.realtime.manager, "close", Mock())
    monkeypatch.setattr(app_factory, "stop_camera_services", Mock())
    monkeypatch.setattr(app_factory.job_manager, "shutdown", Mock())
    start = Mock()
    monkeypatch.setattr(app_factory.central_camera_service, "start", start)
    async def run():
        async with app_factory.lifespan(FastAPI()):
            pass
    asyncio.run(run())
    assert [call.args[0] for call in reconcile.call_args_list] == [app_factory.database, app_factory.scenic_database]
    start.assert_not_called()
