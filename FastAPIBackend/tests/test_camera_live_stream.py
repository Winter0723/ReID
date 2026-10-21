import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from fastapi.responses import JSONResponse


def test_stream_skips_queued_frames_and_never_opens_or_records_camera():
    from app.services.camera_preview import preview_frames
    service = Mock()
    service.latest_preview.side_effect = [(b"first", 1), (b"latest", 9)]
    async def consume():
        frames = preview_frames(service, duration=10)
        first = await anext(frames)
        latest = await anext(frames)
        await frames.aclose()
        return first, latest
    first, latest = asyncio.run(consume())
    assert b"Content-Length: 5\r\n\r\nfirst" in first
    assert b"latest" in latest
    service.acquire.assert_not_called()
    service.release.assert_not_called()


def test_capture_publishes_one_shared_640_jpeg_without_disk_per_frame(monkeypatch):
    from app.services import central_camera_service as module
    service = module.CentralCameraService("0", use_environment_source=False)
    cap = Mock()
    cap.isOpened.return_value = True
    count = 0
    def read():
        nonlocal count
        count += 1
        if count == 4:
            service._stop_event.set()
        return True, np.zeros((720, 1280, 3), dtype=np.uint8)
    cap.read.side_effect = read
    monkeypatch.setattr(service, "_open_capture", lambda source: cap)
    snapshot = Mock()
    monkeypatch.setattr(service, "_save_central_latest_frame", snapshot)
    service._run()
    jpeg, index = service.latest_preview()
    assert index >= 1
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (360, 640)
    assert service.latest_frame()[0].shape[:2] == (360, 640)
    snapshot.assert_not_called()
    cap.release.assert_called_once()
    assert service.recording_info() == {}


def test_local_capture_negotiates_resolution_fps_and_small_buffer(monkeypatch):
    from app.services import central_camera_service as module
    cap = Mock()
    monkeypatch.setattr(module.cv2, "VideoCapture", lambda *args: cap)
    module.CentralCameraService._open_capture("0")
    cap.set.assert_any_call(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set.assert_any_call(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set.assert_any_call(cv2.CAP_PROP_FPS, 30)
    cap.set.assert_any_call(cv2.CAP_PROP_BUFFERSIZE, 1)


def test_core_stream_requires_project_access_and_path_bound_signature(client, register_user, monkeypatch):
    from app.services import camera_preview
    monkeypatch.setattr(camera_preview, "preview_response", lambda source: JSONResponse({"source": source}))
    owner, stranger = register_user("owner"), register_user("stranger")
    project = client.post("/api/v1/projects", headers=owner["headers"], json={
        "name": "Camera", "project_type": "camera", "camera_source": "3"}).json()
    base = f"/api/v1/projects/{project['id']}/camera"
    assert client.get(base + "/preview-stream").status_code == 401
    assert client.get(base + "/preview-stream", headers=stranger["headers"]).status_code in (403, 404)
    assert client.get(base + "/preview.mjpeg").status_code == 401
    ticket = client.get(base + "/preview-stream", headers=owner["headers"])
    assert ticket.status_code == 200
    assert client.get(ticket.json()["url"]).json() == {"source": "3"}
    assert client.get(ticket.json()["url"].replace("asset_signature=", "asset_signature=bad")).status_code == 401
    assert client.get(ticket.json()["url"].replace(f"projects/{project['id']}", "projects/99999")).status_code == 401


def test_scenic_stream_scope_and_disabled_point(client, register_scenic_user, register_user, monkeypatch):
    from app.services import camera_preview
    import scenic_database as database
    monkeypatch.setattr(camera_preview, "preview_response", lambda source: JSONResponse({"source": source}))
    owner, stranger = register_scenic_user("owner"), register_scenic_user("stranger")
    core = register_user("core")
    project = client.post("/api/v1/scenic/projects", headers=owner["headers"], json={"name": "Park"}).json()
    base = f"/api/v1/scenic/projects/{project['id']}/camera-points"
    point = client.post(base, headers=owner["headers"], json={"name": "Lake", "camera_source": "3", "role": "play"}).json()
    endpoint = base + f"/{point['id']}/preview-stream"
    assert client.get(endpoint, headers=core["headers"]).status_code == 401
    assert client.get(endpoint, headers=stranger["headers"]).status_code in (403, 404)
    ticket = client.get(endpoint, headers=owner["headers"])
    assert ticket.status_code == 200
    assert client.get(ticket.json()["url"]).json() == {"source": "3"}
    with database.SessionLocal() as db:
        db.get(database.ScenicCameraPoint, point["id"]).is_active = False
        db.commit()
    assert client.get(ticket.json()["url"]).status_code == 404
