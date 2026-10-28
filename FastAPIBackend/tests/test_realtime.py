from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import time

import cv2
import numpy as np
import pytest
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app.services.person_tracking import IdentityGate, create_tracker, update_tracker


def test_identity_requires_fresh_evidence_and_reconfirmation():
    gate = IdentityGate()
    assert gate.update({1: .8}) == ("pending", None)
    for _ in range(10):
        assert gate.update({1: .8}, measured=False) == ("pending", None)
    assert gate.update({1: .8}) == ("pending", None)
    assert gate.update({1: .8}) == ("confirmed", 1)
    assert gate.update({}) == ("lost", None)
    assert gate.update({1: .8}) == ("pending", None)
    gate.update({1: .8})
    assert gate.update({1: .8}) == ("reconfirmed", 1)
    assert gate.update({1: .8, 2: .79}) == ("pending", None)
    assert gate.target is None


def test_absent_target_cannot_transfer_identity_or_confirm_a_low_score():
    gate = IdentityGate()
    for _ in range(3):
        gate.update({1: .8})
    assert gate.update({2: .60})[1] is None
    assert gate.update({2: .8}, crowded=True)[1] is None
    assert gate.update({2: .4})[0] == "lost"


def test_real_bytetrack_does_not_return_lost_boxes():
    from app.services.model_runtime import Detection
    tracker = create_tracker()
    tracks = update_tracker(tracker, [Detection(20, 10, 80, 110, .9)], (120, 160))
    assert len(tracks) == 1
    assert int(tracks[0][-1]) == 0
    assert len(update_tracker(tracker, [], (120, 160))) == 0


def test_capture_only_never_opens_writer_and_shared_release_preserves_recording(monkeypatch):
    from app.services.central_camera_service import CentralCameraService
    service = CentralCameraService("0", use_environment_source=False)
    monkeypatch.setattr(service, "_start_thread", Mock())
    service.acquire(record=False)
    cap = Mock()
    cap.isOpened.return_value = True
    def read():
        service._stop_event.set()
        return True, np.zeros((64, 64, 3), np.uint8)
    cap.read.side_effect = read
    monkeypatch.setattr(service, "_open_capture", lambda source: cap)
    prepare, writer = Mock(), Mock()
    monkeypatch.setattr(service, "_prepare_recording_session", prepare)
    monkeypatch.setattr(service, "_open_recording_writer", writer)
    service._run()
    prepare.assert_not_called()
    writer.assert_not_called()
    service._stop_event.clear()
    service.acquire()
    service.release(record=False)
    assert service._consumer_count == 1
    assert service._recording_consumers == 1
    assert not service._stop_event.is_set()
    service.release()
    assert service._recording_consumers == 0


@pytest.fixture
def realtime_env(monkeypatch, tmp_path):
    from app.api.routes import realtime as api
    from app.services.realtime import RealtimeManager
    manager = RealtimeManager()
    monkeypatch.setattr(RealtimeManager, "root", property(lambda self: tmp_path))
    monkeypatch.setattr(api, "manager", manager)
    yield api, manager
    manager.close()


def test_rest_permissions_and_input_validation(client, register_user, realtime_env):
    api, manager = realtime_env
    user, stranger = register_user(), register_user()
    project = client.post('/api/v1/projects', headers=user['headers'], json={'name': 'realtime'}).json()['id']
    url = f'/api/v1/projects/{project}/realtime'
    body = {'reference_id': 'missing', 'crop': [0, 0, 1, 1], 'source': {'camera': '0'}}
    assert client.post(url+'/sessions', json=body).status_code == 401
    assert client.post(url+'/sessions', json=body, headers=stranger['headers']).status_code == 403
    for source in ({'camera': 'file:///private'}, {'camera': '0', 'media_id': 1}, {}):
        assert client.post(url+'/sessions', json={**body, 'source': source}, headers=user['headers']).status_code == 422
    assert client.post(url+'/sessions', json={**body, 'crop': [1, 0, 0, 1]}, headers=user['headers']).status_code == 422
    assert client.post(url+'/sessions', json=body, headers=user['headers']).status_code == 404


def test_ticket_is_single_use_and_bound_to_owner_session_origin(client, register_user, realtime_env, tmp_path):
    from app.services.realtime import Session
    _, manager = realtime_env
    user, stranger = register_user(), register_user()
    project = client.post('/api/v1/projects', headers=user['headers'], json={'name': 'realtime'}).json()['id']
    session = Session(project, user['username'], tmp_path/'image', [0,0,1,1], '0', True, False, lambda: None)
    session.status = 'running'
    manager.sessions[session.id] = session
    url = f'/api/v1/projects/{project}/realtime/sessions/{session.id}'
    assert client.post(url+'/stream-ticket', headers=stranger['headers']).status_code == 403
    ticket = client.post(url+'/stream-ticket', headers=user['headers']).json()['ticket']
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url+'/stream?ticket='+ticket, headers={'origin': 'https://untrusted.example'}):
            pass
    with client.websocket_connect(url+'/stream?ticket='+ticket, headers={'origin': 'http://localhost:5173'}) as ws:
        assert ws.receive_json()['status'] == 'running'
        ws.send_json({'action': 'stop'})
        assert ws.receive_json()['status'] == 'stopped'
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url+'/stream?ticket='+ticket, headers={'origin': 'http://localhost:5173'}):
            pass


def test_video_session_streams_synchronized_frames_and_cleans_up(tmp_path, monkeypatch, isolated_model_binding):
    import torch
    from app.services import model_runtime
    from app.services.realtime import Session
    from app.services.model_runtime import Detection
    path = tmp_path/'source.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160,120))
    for i in range(12):
        writer.write(np.full((120,160,3), i*10, np.uint8))
    writer.release()
    reference = tmp_path/'reference.jpg'
    Image.new('RGB',(50,100)).save(reference)
    monkeypatch.setattr(model_runtime, 'clip_image_feature', lambda image: torch.tensor([[1.,0.]]))
    monkeypatch.setattr(model_runtime, 'clip_image_features', lambda images: torch.tensor([[1.,0.]]*len(images)))
    monkeypatch.setattr(model_runtime, 'detect_people', lambda frame, **kw: [Detection(20,10,80,110,.9)])
    cleaned = Mock()
    session = Session(1,'owner',reference,[0,0,1,1],path,False,False,cleaned)
    session.connected = True
    session.thread.start()
    frames = []
    deadline = time.monotonic()+8
    while session.thread.is_alive() and time.monotonic()<deadline:
        with session.lock:
            packet = session.packet
            if packet and packet['sequence'] > session.acked:
                frames.append(packet)
                session.acked = packet['sequence']
        time.sleep(.01)
    session.thread.join(timeout=1)
    assert session.status == 'completed', session.error
    assert len(frames) == 12
    assert frames[0]['source_time'] == 0
    assert frames[-1]['target_state'] == 'confirmed'
    import base64
    image = cv2.imdecode(np.frombuffer(base64.b64decode(frames[-1]['jpeg']), np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (frames[-1]['height'], frames[-1]['width'])
    cleaned.assert_called_once()


def test_expiry_stops_orphaned_sessions_and_removes_only_unclaimed_inputs(realtime_env, tmp_path):
    from app.services.realtime import Input, Session
    _, manager = realtime_env
    for key, claimed in [('unused', False), ('in_use', True)]:
        path = tmp_path/key
        path.write_bytes(b'test')
        manager.inputs[key] = Input(key, 1, 'owner', path, 'video', time.monotonic()-3601, claimed)
    session = Session(1,'owner',tmp_path/'reference',[0,0,1,1],'0',True,False,lambda: None)
    session.disconnected_at = time.monotonic()-31
    manager.sessions[session.id] = session
    manager.tickets['expired'] = (session.id, time.monotonic()-1)
    manager.collect_expired()
    assert not (tmp_path/'unused').exists()
    assert (tmp_path/'in_use').exists()
    assert session.stop_event.is_set()
    assert 'expired' not in manager.tickets


def test_pause_resume_discards_pending_frame_and_resets_camera_only(tmp_path):
    from app.services.realtime import Session
    for camera in [False, True]:
        session = Session(1,'owner',tmp_path/'reference',[0,0,1,1],'0',camera,False,lambda: None)
        session.packet = {'sequence': 5}
        session.sequence = 5
        session.control('pause')
        assert session.paused and session.packet is None and session.acked == 5
        assert session.epoch == 1
        session.control('resume')
        assert not session.paused and session.epoch == 2
        assert session.reset is camera


def test_tracker_preserves_detection_indices_through_high_low_score_subsets():
    from app.services.model_runtime import Detection
    tracker = create_tracker()
    rows = update_tracker(tracker, [Detection(0,0,10,20,.15), Detection(20,10,80,110,.9)], (120,160))
    assert len(rows) == 1 and int(rows[0][-1]) == 1
    # Low-confidence association still refers to the second original detection.
    rows = update_tracker(tracker, [Detection(0,0,10,20,.9), Detection(20,10,80,110,.2)], (120,160))
    assert any(int(row[-1]) == 1 and abs(float(row[5])-.2)<.001 for row in rows)


def test_realtime_camera_enumeration_respects_allowlist_without_opening_devices(monkeypatch):
    from app.api.routes.realtime import camera_devices
    from app.services import camera_stream
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), 'camera_sources', {'1': '1'})
    monkeypatch.setattr(camera_stream, '_camera_names', lambda: ['Iriun Webcam', 'USB Camera'])
    assert camera_devices() == [{'source': '1', 'label': 'USB Camera'}]
    monkeypatch.setattr(get_settings(), 'camera_sources', {
        '0': '0', '1': '1', 'entrance': 'rtsp://private.example/live', 'usb-alias': '1',
    })
    assert camera_devices() == [
        {'source': '0', 'label': 'Iriun Webcam'},
        {'source': '1', 'label': 'USB Camera'},
        {'source': 'entrance', 'label': 'entrance', 'type': 'configured'},
        {'source': 'usb-alias', 'label': 'usb-alias', 'type': 'configured'},
    ]


def test_upload_to_websocket_loop_excludes_indexing_and_enforces_single_slot(client, register_user, realtime_env, tmp_path, monkeypatch):
    import torch
    from app.services import model_runtime
    from app.services.model_runtime import Detection
    _, manager = realtime_env
    monkeypatch.setattr(model_runtime, 'detect_people', lambda frame, **kw: [Detection(10,5,40,55,.9)])
    monkeypatch.setattr(model_runtime, 'clip_image_feature', lambda image: torch.tensor([[1.,0.]]))
    monkeypatch.setattr(model_runtime, 'clip_image_features', lambda images: torch.tensor([[1.,0.]]*len(images)))
    user = register_user()
    project = client.post('/api/v1/projects', headers=user['headers'], json={'name':'live'}).json()['id']
    root = f'/api/v1/projects/{project}/realtime'
    image = BytesIO(); Image.new('RGB',(80,60)).save(image,format='PNG')
    response = client.post(root+'/inputs?kind=image',headers=user['headers'],files={'file':('ref.png',image.getvalue(),'image/png')})
    assert response.status_code == 200, response.text
    reference = response.json()
    assert reference['boxes'][0]['confidence'] == .9
    path = tmp_path/'video.avi'
    writer = cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(80,60))
    for _ in range(30): writer.write(np.zeros((60,80,3),np.uint8))
    writer.release()
    response = client.post(root+'/inputs?kind=video',headers=user['headers'],files={'file':('clip.avi',path.read_bytes(),'video/avi')})
    assert response.status_code == 200, response.text
    video = response.json()
    body = {'reference_id':reference['id'],'crop':[0,0,1,1],'source':{'input_id':video['id']}}
    response = client.post(root+'/sessions',headers=user['headers'],json=body)
    assert response.status_code == 200, response.text
    session_id = response.json()['id']
    assert client.post(root+'/sessions',headers=user['headers'],json=body).status_code == 409
    ticket = client.post(root+f'/sessions/{session_id}/stream-ticket',headers=user['headers']).json()['ticket']
    with client.websocket_connect(root+f'/sessions/{session_id}/stream?ticket={ticket}',headers={'origin':'http://localhost:5173'}) as ws:
        while True:
            message = ws.receive_json()
            if message['type'] == 'frame':
                assert message['session_id'] == session_id and message['jpeg'] and message['epoch'] == 0
                ws.send_json({'action':'stop'})
                break
            ws.send_json({'pong':True})
    manager.sessions[session_id].thread.join(timeout=5)
    assert not manager.inputs
    import database
    with database.SessionLocal() as db:
        assert db.query(database.MediaFile).filter(database.MediaFile.project_id==project).count() == 0
        assert db.query(database.ProcessingJob).filter(database.ProcessingJob.project_id==project).count() == 0


def test_rtsp_capture_has_bounded_open_and_read_timeouts(monkeypatch):
    from app.services.central_camera_service import CentralCameraService
    factory = Mock()
    monkeypatch.setattr(cv2, 'VideoCapture', factory)
    CentralCameraService._open_capture('test-rtsp')
    factory.assert_called_once_with('rtsp://test.invalid/live', cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])


def test_video_preview_decodes_first_frame_without_model_inference(tmp_path):
    from app.api.routes.realtime import video_preview
    import base64
    path = tmp_path / "preview.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (160, 120))
    assert writer.isOpened()
    writer.write(np.full((120, 160, 3), (30, 80, 190), dtype=np.uint8))
    writer.release()
    result = video_preview(path)
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(result["preview"].split(",", 1)[1]), np.uint8), cv2.IMREAD_COLOR)
    assert (result["width"], result["height"]) == (160, 120)
    assert decoded.shape == (120, 160, 3)
    assert decoded[:, :, 2].mean() > 180


def test_project_video_preview_enforces_membership(client, register_user, realtime_env):
    user, stranger = register_user(), register_user()
    project = client.post('/api/v1/projects', headers=user['headers'], json={'name': 'preview'}).json()['id']
    url = f'/api/v1/projects/{project}/realtime/media/999/preview'
    assert client.get(url).status_code == 401
    assert client.get(url, headers=stranger['headers']).status_code == 403
    assert client.get(url, headers=user['headers']).status_code == 404
