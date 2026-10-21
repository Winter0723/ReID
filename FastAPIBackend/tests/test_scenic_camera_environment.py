from types import SimpleNamespace
from unittest.mock import Mock


def test_gateway_discovery_honors_configuration_instead_of_driver_brand(monkeypatch):
    from app.api.scenic import system_routes
    selected = dict(source='rtsp://mediamtx:8554/local/selected', label='Generic Virtual Camera',
                    kind='dshow', online=True, enabled=True)
    disabled = {**selected, 'source': 'rtsp://mediamtx:8554/local/disabled', 'enabled': False}
    offline = {**selected, 'source': 'rtsp://mediamtx:8554/local/offline', 'online': False}
    monkeypatch.setattr(system_routes, 'list_camera_devices', lambda: [selected, disabled, offline])
    assert system_routes._ordered_camera_devices() == [selected]


def test_bridge_scenic_wall_uses_actual_devices_and_deduplicates(monkeypatch):
    from app.services import camera_bridge as bridge
    source = 'rtsp://mediamtx:8554/local/usb'
    monkeypatch.setattr(bridge, 'enabled', lambda: True)
    monkeypatch.setattr(bridge, 'devices', lambda: [{'source': source, 'online': True}])
    monkeypatch.setattr(bridge, 'resolve', lambda value: source if value == '0' else value)
    def point(key, active=True):
        return SimpleNamespace(camera_source=key, camera_id=key, is_active=active)
    primary, stale, duplicate, remote = point('0'), point('3'), point(source), point('rtsp://nvr/front')
    assert bridge.scenic_points_for_environment([primary, stale, duplicate, remote]) == [primary, remote]
    monkeypatch.setattr(bridge, 'devices', lambda: [{'source': source, 'online': False}])
    assert bridge.scenic_points_for_environment([primary, stale, remote]) == [remote]


def test_discovery_reuses_default_source_alias(client, register_scenic_user, monkeypatch):
    from app.api.scenic import camera_routes
    from app.services import camera_bridge as bridge
    user = register_scenic_user('bridge_scenic', 'bridge@example.com')
    headers = user['headers']
    project = client.post('/api/v1/scenic/projects', headers=headers, json={'name': 'Single USB'}).json()
    prefix = f"/api/v1/scenic/projects/{project['id']}"
    for source in ('0', '3'):
        response = client.post(prefix + '/camera-points', headers=headers,
                              json={'name': 'Camera ' + source, 'camera_source': source, 'role': 'entry'})
        assert response.status_code == 200
    source = 'rtsp://mediamtx:8554/local/usb'
    devices = [{'source': source, 'label': 'USB Camera', 'online': True}]
    monkeypatch.setattr(bridge, 'enabled', lambda: True)
    monkeypatch.setattr(bridge, 'devices', lambda: devices)
    monkeypatch.setattr(bridge, 'resolve', lambda value: source if value == '0' else value)
    monkeypatch.setattr(camera_routes, '_ordered_camera_devices', lambda: devices)
    for _ in range(2):
        response = client.post(prefix + '/camera-points/discover', headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['created'] == 0
        assert len(response.json()['camera_points']) == 1
    assert len(client.get(prefix + '/camera-points', headers=headers).json()['camera_points']) == 1
    monkeypatch.setattr(camera_routes, '_camera_wall_payload', lambda *args, **kwargs: [])
    monkeypatch.setattr(camera_routes, '_scenic_camera_status_payload', lambda *args: {})
    start = Mock()
    monkeypatch.setattr(camera_routes.camera_stream_manager, 'start', start)
    monkeypatch.setattr(camera_routes.camera_stream_manager, 'is_running', lambda *args, **kwargs: False)
    response = client.post(prefix + '/camera-points/start-all', headers=headers, json={'camera_source': '0'})
    assert response.status_code == 200, response.text
    assert start.call_count == 1
    assert start.call_args.kwargs['camera_source'] == '0'

def test_bridge_without_devices_does_not_create_placeholder(client, register_scenic_user, monkeypatch):
    from app.api.scenic import camera_routes
    from app.services import camera_bridge as bridge
    import scenic_database as database
    user = register_scenic_user('no_usb', 'no_usb@example.com')
    headers = user['headers']
    project = client.post('/api/v1/scenic/projects', headers=headers, json={'name': 'No camera'}).json()
    monkeypatch.setattr(bridge, 'enabled', lambda: True)
    monkeypatch.setattr(bridge, 'devices', lambda: [])
    monkeypatch.setattr(camera_routes, '_ordered_camera_devices', lambda: [])
    prefix = f"/api/v1/scenic/projects/{project['id']}"
    response = client.post(prefix + '/camera-points/start-all', headers=headers, json={'camera_source': '0'})
    assert response.status_code == 400, response.text
    with database.SessionLocal() as db:
        assert db.query(database.ScenicCameraPoint).filter_by(project_id=project['id']).count() == 0
