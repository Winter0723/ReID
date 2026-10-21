from unittest.mock import Mock

import pytest


@pytest.fixture
def hotplug(monkeypatch):
    import scenic_database as dbm
    from app.services import camera_bridge, camera_stream, camera_hotplug
    camera_hotplug._PAUSED_PROJECTS.clear()
    monkeypatch.setattr(camera_bridge, 'enabled', lambda: True)
    monkeypatch.setattr(camera_stream._MAINTENANCE_STOP, 'is_set', lambda: False)
    usb = 'rtsp://mediamtx:8554/local/usb'
    phone = 'rtsp://mediamtx:8554/local/phone'
    devices = [dict(source=usb, label='USB A', online=True)]
    monkeypatch.setattr(camera_bridge, 'devices', lambda: devices)
    monkeypatch.setattr(camera_bridge, 'resolve', lambda source: usb if source == '0' else source)
    running = set()
    start = Mock(side_effect=lambda **kw: running.add(kw['stream_key']))
    stop = Mock(side_effect=lambda project_id, **kw: running.discard(kw['stream_key']))
    manager = camera_stream.scenic_camera_stream_manager
    monkeypatch.setattr(manager, 'start', start)
    monkeypatch.setattr(manager, 'stop', stop)
    monkeypatch.setattr(manager, 'is_running', lambda project_id, **kw: kw['stream_key'] in running)
    with dbm.SessionLocal() as db:
        db.add(dbm.User(username='hotplug', hashed_password='test', role='operator'))
        db.flush()
        project = dbm.Project(name='Hotplug', owner_username='hotplug')
        db.add(project)
        db.commit()
        project_id = project.id
    yield camera_hotplug.ScenicCameraHotplug(interval_seconds=0), devices, usb, phone, start, stop, running, project_id
    camera_hotplug._PAUSED_PROJECTS.clear()


def test_add_disconnect_reconnect_reuses_points_and_stops_only_lost_source(hotplug):
    import scenic_database as dbm
    auto, devices, usb, phone, start, stop, running, project_id = hotplug
    auto.tick()
    devices.append(dict(source=phone, label='Generic second camera', online=True))
    auto.tick()
    assert start.call_count == 2
    with dbm.SessionLocal() as db:
        before = [(p.id, p.camera_source) for p in db.query(dbm.ScenicCameraPoint).all()]
    devices[1]['online'] = False
    auto.tick()
    stop.assert_called_once()
    assert len(running) == 1
    devices[1]['online'] = True
    auto.tick()
    auto.tick()
    assert start.call_count == 3 and len(running) == 2
    with dbm.SessionLocal() as db:
        assert before == [(p.id, p.camera_source) for p in db.query(dbm.ScenicCameraPoint).all()]


def test_disabled_alias_and_manual_pause_survive_new_devices(hotplug):
    import scenic_database as dbm
    from app.services.camera_hotplug import pause_project, resume_project
    auto, devices, usb, phone, start, stop, running, project_id = hotplug
    with dbm.SessionLocal() as db:
        db.add(dbm.ScenicCameraPoint(project_id=project_id, name='disabled original', camera_source='0', is_active=False))
        db.commit()
    auto.tick()
    start.assert_not_called()
    pause_project(project_id)
    devices.append(dict(source=phone, label='Second', online=True))
    auto.tick()
    start.assert_not_called()
    resume_project(project_id)
    auto.tick()
    start.assert_called_once()
    assert start.call_args.kwargs['camera_source'] == phone
    with dbm.SessionLocal() as db:
        assert db.query(dbm.ScenicCameraPoint).count() == 2


def test_reconnect_waits_for_old_ingestion_to_finish(hotplug):
    auto, devices, usb, phone, start, stop, running, project_id = hotplug
    auto.tick()
    stop.side_effect = None  # Simulate asynchronous shutdown still draining a recording.
    devices[0]['online'] = False
    auto.tick()
    devices[0]['online'] = True
    auto.tick()
    assert start.call_count == 1
    running.clear()
    auto.tick()
    assert start.call_count == 2


def test_auto_start_setting_disables_gateway_ingestion(hotplug, monkeypatch):
    from types import SimpleNamespace
    from app.services import camera_hotplug
    auto, devices, usb, phone, start, stop, running, project_id = hotplug
    monkeypatch.setattr(camera_hotplug, 'get_settings', lambda: SimpleNamespace(scenic_camera_auto_start=False))
    auto.tick()
    start.assert_not_called()


def test_existing_external_camera_keeps_autostart_with_gateway_enabled(hotplug):
    import scenic_database as dbm
    from app.services.camera_lifecycle import ScenicCameraAutostart
    auto, devices, usb, phone, start, stop, running, project_id = hotplug
    with dbm.SessionLocal() as db:
        for name, source in [('Gateway USB', usb), ('External NVR', 'rtsp://nvr/front')]:
            db.add(dbm.ScenicCameraPoint(project_id=project_id, name=name, camera_source=source, is_active=True))
        db.commit()
    ScenicCameraAutostart().tick()
    start.assert_called_once()
    assert start.call_args.kwargs['camera_source'] == 'rtsp://nvr/front'
