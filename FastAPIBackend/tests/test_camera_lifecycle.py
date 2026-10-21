import datetime
import json
import os
from unittest.mock import Mock

import pytest


def seed():
    import scenic_database as dbm
    from app.services.time_utils import utc_now
    with dbm.SessionLocal() as db:
        db.add(dbm.User(username='camera-owner', hashed_password='test', role='operator'))
        db.flush()
        project = dbm.Project(name='Autonomous scenic', owner_username='camera-owner')
        db.add(project)
        db.commit()
        return project.id, utc_now()


def recording(db, project_id, at, name='old', age_hours=13):
    import scenic_database as dbm
    from app.core.config import get_settings
    media = dbm.MediaFile(project_id=project_id, uploader_username='camera-owner', filename=name+'.avi',
        object_key=name, content_type='video/x-msvideo', size_bytes=4, sha256=name, media_type='camera',
        status='stopped', created_at=at - datetime.timedelta(hours=age_hours))
    db.add(media)
    db.flush()
    media.object_key=f'projects/scenic/{project_id}/media/{media.id}/recordings/{name}.avi'
    path = get_settings().object_storage_dir / media.object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'video')
    stamp=(at-datetime.timedelta(hours=age_hours)).timestamp()
    os.utime(path, (stamp, stamp))
    db.commit()
    return media, path


def test_autostart_retries_without_admin_and_respects_manual_stop(monkeypatch):
    from app.services.camera_lifecycle import ScenicCameraAutostart
    from app.services import camera_bridge, camera_stream
    seed()
    monkeypatch.setattr(camera_bridge, 'enabled', lambda: False)
    monkeypatch.setattr(camera_stream._MAINTENANCE_STOP, 'is_set', lambda: False)
    start = Mock(side_effect=[ValueError('offline'), {'running': True}])
    monkeypatch.setattr(camera_stream.scenic_camera_stream_manager, 'start', start)
    auto = ScenicCameraAutostart()
    auto.tick()
    auto.tick()
    auto.tick()
    assert start.call_count == 2
    assert start.call_args.kwargs['stream_key'] > 1_000_000


def test_disabled_camera_points_are_not_replaced(monkeypatch):
    import scenic_database as dbm
    from app.services.camera_lifecycle import ScenicCameraAutostart
    from app.services import camera_bridge, camera_stream
    project_id, _ = seed()
    with dbm.SessionLocal() as db:
        db.add(dbm.ScenicCameraPoint(project_id=project_id, name='disabled', camera_source='0', is_active=False))
        db.commit()
    monkeypatch.setattr(camera_bridge, 'enabled', lambda: False)
    start = Mock()
    monkeypatch.setattr(camera_stream.scenic_camera_stream_manager, 'start', start)
    ScenicCameraAutostart().tick()
    start.assert_not_called()


def test_core_autostart_restores_camera_projects_retries_and_respects_stop(monkeypatch):
    import database as dbm
    from app.services.camera_lifecycle import CoreCameraAutostart
    from app.services import camera_stream
    with dbm.SessionLocal() as db:
        db.add(dbm.User(username="owner", hashed_password="test", role="operator"))
        db.flush()
        camera = dbm.Project(name="Camera", owner_username="owner", project_type="camera", camera_source="2")
        db.add_all([camera, dbm.Project(name="Media", owner_username="owner", project_type="media")])
        db.commit()
        project_id = camera.id
    monkeypatch.setattr(camera_stream._MAINTENANCE_STOP, "is_set", lambda: False)
    start = Mock(side_effect=[ValueError("offline"), {"running": True}])
    monkeypatch.setattr(camera_stream.camera_stream_manager, "start", start)
    auto = CoreCameraAutostart()
    auto.tick()
    auto.tick()
    auto.tick()
    assert start.call_count == 2
    start.assert_called_with(project_id=project_id, username="owner", camera_source="2")


def test_core_autostart_can_be_disabled(monkeypatch):
    from app.services import camera_lifecycle, camera_stream
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "core_camera_auto_start", False)
    start = Mock()
    monkeypatch.setattr(camera_stream.camera_stream_manager, "start", start)
    camera_lifecycle.CoreCameraAutostart().tick()
    start.assert_not_called()


def test_startup_schedules_cameras_before_model_loading_and_cleans_up_on_failure(monkeypatch):
    import asyncio
    from app.api import app_factory as factory
    events = []
    monkeypatch.setattr(factory.vector_store, "ensure_ready", lambda: None)
    monkeypatch.setattr(factory.scenic_vector_store, "ensure_ready", lambda: None)
    monkeypatch.setattr(factory, "start_camera_recording_maintenance", lambda: events.append("cameras"))
    monkeypatch.setattr(factory, "stop_camera_recording_maintenance", lambda: events.append("stop"))
    monkeypatch.setattr(factory, "stop_camera_services", lambda: None)
    monkeypatch.setattr(factory.camera_stream_manager, "stop_all", lambda **kwargs: None)
    monkeypatch.setattr(factory.scenic_camera_stream_manager, "stop_all", lambda **kwargs: None)
    monkeypatch.setattr(factory.realtime.manager, "close", lambda: None)
    monkeypatch.setattr(factory.job_manager, "shutdown", lambda **kwargs: None)

    def fail_models():
        assert events == ["cameras"]
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(factory, "load_models", fail_models)

    async def start():
        async with factory.lifespan(None):
            pytest.fail("Startup must fail if model initialization fails")

    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(start())
    assert events == ["cameras", "stop"]


def test_retention_expires_only_old_unreferenced_recordings():
    import scenic_database as dbm
    from app.services.camera_lifecycle import cleanup_camera_recordings
    project_id, now = seed()
    with dbm.SessionLocal() as db:
        old, old_path = recording(db, project_id, now)
        fresh, fresh_path = recording(db, project_id, now, 'fresh', 11)
        old_id = old.id
    assert cleanup_camera_recordings(now) == 1
    assert not old_path.exists()
    assert fresh_path.exists()
    with dbm.SessionLocal() as db:
        assert db.get(dbm.MediaFile, old_id).status == 'expired'
    assert cleanup_camera_recordings(now) == 0


@pytest.mark.parametrize('reference', ['visit', 'segment', 'search', 'draft', 'snapshot', 'job', 'retry'])
def test_retention_protects_history_and_pending_work(reference):
    import scenic_database as dbm
    from app.services.camera_lifecycle import cleanup_camera_recordings
    project_id, now = seed()
    with dbm.SessionLocal() as db:
        media, path = recording(db, project_id, now)
        session = dbm.TouristSession(project_id=project_id, ticket_code='ticket', entry_image_object_key='entry.jpg',
            created_by_username='camera-owner', created_at=now-datetime.timedelta(hours=14),
            checked_out_at=None if reference == 'visit' else now-datetime.timedelta(hours=14))
        db.add(session)
        db.flush()
        if reference in {'segment', 'retry'}:
            db.add(dbm.TouristVisitSegment(session_id=session.id, project_id=project_id, media_id=media.id))
            if reference == 'retry':
                media.status = 'expiring'
        elif reference == 'search':
            target = dbm.DetectionTarget(project_id=project_id, media_id=media.id, crop_path='crop.jpg', vector_id='test-vector')
            db.add(target)
            task = dbm.SearchTask(project_id=project_id, username='camera-owner', query_type='scenic', query_content='test')
            db.add(task)
            db.flush()
            db.add(dbm.SearchResult(search_task_id=task.id, target_id=target.id, path='crop.jpg', similarity=.9))
        elif reference == 'draft':
            db.add(dbm.ScenicEditDraft(session_id=session.id, data_json=json.dumps({'clips':[{'media_id':media.id}]})))
        elif reference == 'snapshot':
            db.add(dbm.ScenicVlogJob(session_id=session.id, created_by_username='camera-owner',
                snapshot_json=json.dumps({'variants':{'landscape':[{'object_key':media.object_key}]}})))
        elif reference == 'job':
            db.add(dbm.ProcessingJob(id='upload', project_id=project_id, media_id=media.id, job_type='camera_recording', status='failed'))
        db.commit()
    assert cleanup_camera_recordings(now) == 0
    assert path.exists()


def test_raw_expiry_preserves_protected_hardlink():
    import scenic_database as dbm
    from app.core.config import get_settings
    from app.services.camera_lifecycle import cleanup_camera_recordings
    project_id, now = seed()
    with dbm.SessionLocal() as db:
        media, path = recording(db, project_id, now)
        db.add(dbm.ProcessingJob(id='upload', project_id=project_id, media_id=media.id, job_type='camera_recording', status='failed'))
        db.commit()
    raw = get_settings().object_storage_dir / 'camera_monitor/0/shared.avi'
    raw.parent.mkdir(parents=True, exist_ok=True)
    os.link(path, raw)
    cleanup_camera_recordings(now)
    assert not raw.exists()
    assert path.read_bytes() == b'video'


def test_retention_retries_partial_delete(monkeypatch):
    import scenic_database as dbm
    from app.services import camera_lifecycle as lifecycle
    project_id, now = seed()
    with dbm.SessionLocal() as db:
        media, path = recording(db, project_id, now)
        target = dbm.DetectionTarget(project_id=project_id, media_id=media.id, crop_path='missing.jpg',
                                    crop_object_key='crop.jpg', vector_id='retry-vector')
        db.add(target)
        db.commit()
    original = lifecycle.storage.delete
    def fail_crop(key):
        if key == 'crop.jpg':
            raise OSError('temporary failure')
        original(key)
    monkeypatch.setattr(lifecycle.storage, 'delete', fail_crop)
    assert lifecycle.cleanup_camera_recordings(now) == 0
    assert not path.exists()
    monkeypatch.setattr(lifecycle.storage, 'delete', original)
    assert lifecycle.cleanup_camera_recordings(now) == 1

def test_recently_finished_old_segment_is_not_expired():
    import scenic_database as dbm
    from app.services.camera_lifecycle import cleanup_camera_recordings
    project_id, now = seed()
    with dbm.SessionLocal() as db:
        _, path = recording(db, project_id, now)
        os.utime(path, (now.timestamp(), now.timestamp()))
    assert cleanup_camera_recordings(now) == 0
    assert path.exists()


def test_active_recording_is_not_expired(monkeypatch):
    import scenic_database as dbm
    from types import SimpleNamespace
    from app.services import camera_stream
    from app.services.camera_lifecycle import cleanup_camera_recordings
    project_id, now = seed()
    with dbm.SessionLocal() as db:
        media, path = recording(db, project_id, now)
        monkeypatch.setattr(camera_stream.scenic_camera_stream_manager, '_streams', {
            1: SimpleNamespace(media_id=media.id, running=True)})
    assert cleanup_camera_recordings(now) == 0
    assert path.exists()


def test_auto_start_can_be_disabled(monkeypatch):
    from app.services import camera_lifecycle, camera_stream
    from types import SimpleNamespace
    seed()
    start = Mock()
    monkeypatch.setattr(camera_stream.scenic_camera_stream_manager, 'start', start)
    monkeypatch.setattr(camera_lifecycle, 'get_settings', lambda: SimpleNamespace(scenic_camera_auto_start=False))
    camera_lifecycle.ScenicCameraAutostart().tick()
    start.assert_not_called()


def test_cleanup_does_not_touch_uploaded_camera_video():
    import scenic_database as dbm
    from app.services.camera_lifecycle import cleanup_camera_recordings
    project_id, now = seed()
    with dbm.SessionLocal() as db:
        media, path = recording(db, project_id, now)
        media.object_key = f'projects/scenic/{project_id}/simulation/1/upload.avi'
        db.commit()
    assert cleanup_camera_recordings(now) == 0
    assert path.exists()

def test_preview_only_capture_is_segmented(monkeypatch):
    from app.services import camera_stream
    from app.services.time_utils import utc_now
    service = Mock()
    service.source = '0'
    service.status.return_value = {'running': True}
    service.recording_info.return_value = {'started_at': utc_now()-datetime.timedelta(seconds=60)}
    monkeypatch.setattr(camera_stream, 'camera_services', lambda: [service])
    monkeypatch.setattr(camera_stream.camera_stream_manager, '_streams', {})
    monkeypatch.setattr(camera_stream.scenic_camera_stream_manager, '_streams', {})
    camera_stream.seal_due_camera_recordings()
    service.seal_current_recording.assert_called_once()


def test_startup_deduplicates_camera_sources(monkeypatch):
    import scenic_database as dbm
    from app.services import camera_bridge, camera_stream
    from app.services.camera_lifecycle import ScenicCameraAutostart
    project_id, _ = seed()
    with dbm.SessionLocal() as db:
        for name in ('one', 'two'):
            db.add(dbm.ScenicCameraPoint(project_id=project_id, name=name, camera_source='0', is_active=True))
        db.commit()
    monkeypatch.setattr(camera_bridge, 'enabled', lambda: False)
    monkeypatch.setattr(camera_stream._MAINTENANCE_STOP, 'is_set', lambda: False)
    start = Mock()
    monkeypatch.setattr(camera_stream.scenic_camera_stream_manager, 'start', start)
    ScenicCameraAutostart().tick()
    start.assert_called_once()
