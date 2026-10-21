from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image


def test_camera_returns_unseen_cached_frame_without_waiting(monkeypatch):
    from app.services.central_camera_service import CentralCameraService

    service = CentralCameraService("test-rtsp", use_environment_source=False)
    service._frame = np.zeros((20, 30, 3), np.uint8)
    service._frame_index = 8
    service._frame_time = datetime.now(timezone.utc)
    wait = Mock(side_effect=AssertionError("A new cached frame must return immediately"))
    monkeypatch.setattr(service._frame_available, "wait", wait)
    frame, captured, index, _ = service.wait_for_frame(timeout=0.5, after_index=7)
    assert index == 8 and captured == service._frame_time
    assert frame is not service._frame
    assert np.array_equal(frame, service._frame)
    wait.assert_not_called()


def test_camera_waits_for_next_frame_if_cached_frame_was_already_consumed(monkeypatch):
    from app.services.central_camera_service import CentralCameraService

    service = CentralCameraService("test-rtsp", use_environment_source=False)
    service._frame = np.zeros((20, 30, 3), np.uint8)
    service._frame_index = 8
    def publish(**kwargs):
        service._frame_index = 9
    wait = Mock(side_effect=publish)
    monkeypatch.setattr(service._frame_available, "wait", wait)
    assert service.wait_for_frame(timeout=0.5, after_index=8)[2] == 9
    wait.assert_called_once()


def test_remote_camera_startup_jitter_and_recovery_do_not_skip_fresh_frame(tmp_path, monkeypatch):
    from app.services import realtime, model_runtime, central_camera_service

    clock = [0.0]
    monkeypatch.setattr(realtime, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000 + clock[0]))
    monkeypatch.setattr(realtime, "create_tracker", lambda *args: Mock())
    monkeypatch.setattr(realtime, "update_tracker", lambda *args: [])
    feature = Mock()
    feature.detach.return_value.float.return_value.cpu.return_value.numpy.return_value = np.array([1.0, 0.0])
    monkeypatch.setattr(model_runtime, "clip_image_feature", lambda image: feature)
    detect = Mock(return_value=[])
    monkeypatch.setattr(model_runtime, "detect_people", detect)
    reference = tmp_path / "reference.jpg"
    Image.new("RGB", (30, 50)).save(reference)
    session = realtime.Session(1, "owner", reference, [0, 0, 1, 1], "test-rtsp", True, False, Mock())
    session.connected = True
    frame = np.zeros((20, 30, 3), np.uint8)
    samples = iter([(0.5, None, 0), (2.0, None, 0), (3.0, 3.0, 1),
                    (4.0, 3.0, 1), (7.0, 3.0, 1), (7.5, 7.5, 2)])
    observations = []
    def read(**kwargs):
        observations.append((session.error, session.sequence, kwargs["after_index"]))
        item = next(samples, None)
        if item is None:
            session.stop_event.set()
            return None, None, 2, {}
        clock[0], captured, index = item
        return (frame if captured is not None else None,
                datetime.fromtimestamp(1000 + captured, timezone.utc) if captured is not None else None, index, {})
    service = Mock()
    service.wait_for_frame.side_effect = read
    monkeypatch.setattr(central_camera_service, "get_camera_service", lambda source: service)
    session.run()
    assert all(not error for error, _, _ in observations[:5])
    assert "重新连接" in observations[5][0]
    assert observations[-1] == ("", 2, 2)
    assert detect.call_count == 2  # No stale/duplicate inference; recovery's first frame is used.
    service.latest_frame.assert_not_called()
    service.acquire.assert_called_once_with(record=False)
    service.release.assert_called_once_with(record=False)
