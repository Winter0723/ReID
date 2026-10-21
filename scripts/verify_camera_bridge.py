"""Run inside the production backend with: Get-Content scripts/verify_camera_bridge.py -Raw | docker exec -i myreid-production-backend-1 python -
Creates one clearly named acceptance project and retains its recording for review.
"""
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
import cv2
import database
import scenic_database
from app.core.security import create_token
from app.services.camera_bridge import devices, resolve, capture_url

BASE = 'http://127.0.0.1:2889'


def token_for(module, scope):
    with module.SessionLocal() as db:
        user = db.query(module.User).filter(module.User.is_active.is_(True)).first()
        assert user, f'No active {scope} account available'
        return create_token(user.username, expires_minutes=5, scope=scope)


def request(path, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE + path, headers=headers,
                                 data=None if data is None else json.dumps(data).encode())
    try:
        response = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"{path}: {exc.code}: {exc.read().decode()}") from None
    with response:
        raw = response.read()
        return json.loads(raw) if 'json' in response.headers.get('Content-Type', '') else raw


core_token, scenic_token = token_for(database, 'core'), token_for(scenic_database, 'scenic')
report = {'ready': request('/api/v1/readyz')}
assert report['ready']['status'] == 'ready', report['ready']
for name, url, token in [('core', '/api/v1/cameras?refresh=true', core_token),
                         ('scenic', '/api/v1/scenic/cameras?refresh=true', scenic_token)]:
    result = request(url, token)
    report[name + '_devices'] = result['devices']
    assert any(item.get('online') and 'USB' in item['label'] for item in result['devices']), result
source = resolve('0')
cap = cv2.VideoCapture(capture_url(source), cv2.CAP_FFMPEG,
                       [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
try:
    assert cap.isOpened(), 'RTSP did not open'
    frames = []
    for _ in range(30):
        ok, frame = cap.read()
        assert ok and frame is not None, 'RTSP frame failed'
        frames.append(frame)
    report['rtsp'] = {'frames': len(frames), 'width': frame.shape[1], 'height': frame.shape[0]}
finally:
    cap.release()
project = request('/api/v1/projects', core_token,
                  {'name': 'USB 摄像头生产验收 ' + time.strftime('%Y-%m-%d %H:%M:%S'),
                   'description': '真实 Windows DirectShow → RTSP/TCP → Docker 生产后端验收；录像保留供回放。',
                   'project_type': 'camera', 'camera_source': '0'})
project_id = project['id']
report['project_id'] = project_id
prefix = f'/api/v1/projects/{project_id}'
realtime = request(prefix + '/realtime/cameras', core_token)
assert any(d['source'] == source for d in realtime['devices']), realtime
report['realtime_devices'] = realtime['devices']
try:
    request(prefix + '/camera/start', core_token, {'camera_source': '0', 'sample_interval_seconds': 1, 'min_confidence': .35})
    versions = set()
    for _ in range(15):
        time.sleep(2)
        status = request(prefix + '/camera/status?light=true', core_token)
        monitor = status.get('monitor_status', {})
        versions.add(monitor.get('frame_index', 0))
        if len(versions - {0}) >= 3:
            break
    assert status.get('running'), status
    assert len(versions - {0}) >= 3, status
    preview_url = status.get('latest_frame_url') or monitor.get('latest_frame_url')
    assert preview_url, status
    preview = request(preview_url, core_token)
    assert isinstance(preview, bytes) and len(preview) > 1000
    report['live'] = {'frame_indices': sorted(versions), 'preview_bytes': len(preview),
                      'indexed_targets': status.get('indexed_targets'), 'error': status.get('error')}
finally:
    request(prefix + '/camera/stop', core_token, {})
with database.SessionLocal() as db:
    media = db.query(database.MediaFile).filter(database.MediaFile.project_id == project_id).first()
    assert media and media.size_bytes > 0, 'Recording is empty'
    report['recording'] = {'media_id': media.id, 'size_bytes': media.size_bytes, 'status': media.status}
    recording = request(prefix + f'/media/{media.id}/content', core_token)
path = Path('/tmp/myreid-camera-acceptance.avi')
path.write_bytes(recording)
cap = cv2.VideoCapture(str(path))
try:
    ok, frame = cap.read()
    assert ok and frame is not None, 'Recording cannot be decoded'
    report['recording']['decoded'] = True
finally:
    cap.release()
    path.unlink(missing_ok=True)
print(json.dumps(report, ensure_ascii=False, indent=2))
