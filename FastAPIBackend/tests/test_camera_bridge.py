from types import SimpleNamespace
from unittest.mock import Mock


def test_rtsp_probe_allows_handshake_and_keyframe(monkeypatch):
    from app.services import camera_stream
    service = Mock(source='rtsp://mediamtx:8554/local/usb')
    service.wait_for_frame.return_value = (object(), None, 1, {})
    monkeypatch.setattr(camera_stream, 'get_camera_service', lambda source: service)
    assert camera_stream.CameraStreamManager._probe_capture_source('0') == ''
    service.wait_for_frame.assert_called_once_with(timeout=10.0)
    service.release.assert_called_once()


def test_s3_camera_alias_is_uploaded_and_stale_cache_invalidated(tmp_path):
    from app.services.storage import S3ObjectStorage
    storage = S3ObjectStorage.__new__(S3ObjectStorage)
    storage.client = Mock()
    storage.bucket = 'test'
    storage.cache_dir = tmp_path / 'cache'
    recording = tmp_path / 'recording.avi'
    recording.write_bytes(b'finalized-recording')
    cached = storage.cache_dir / 'projects/1/camera.avi'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'incomplete')
    uploaded = []
    storage.client.upload_fileobj.side_effect = lambda body, *args, **kwargs: uploaded.append(body.read())
    storage.publish_path('projects/1/camera.avi', recording, 'video/x-msvideo')
    assert uploaded == [b'finalized-recording']
    assert not cached.exists()


def test_s3_stream_stop_seals_before_publishing(monkeypatch):
    from app.services import camera_stream
    from app.services.storage import S3ObjectStorage
    monkeypatch.setattr(camera_stream, 'storage', S3ObjectStorage.__new__(S3ObjectStorage))
    seal = Mock(return_value=[1])
    monkeypatch.setattr(camera_stream, '_solidify_project_camera_recordings', seal)
    manager, db, state = camera_stream.camera_stream_manager, Mock(), SimpleNamespace(source='0', media_id=1, project_id=1, sealed_media_id=None)
    manager._save_recording(db, state, None)
    seal.assert_called_once_with(manager, db, 1, '', stopping_state=state)


def test_missing_s3_recording_returns_missing_file_and_cleans_partial_cache(tmp_path):
    import pytest
    from botocore.exceptions import ClientError
    from app.services.storage import S3ObjectStorage
    storage = S3ObjectStorage.__new__(S3ObjectStorage)
    storage.client, storage.bucket, storage.cache_dir = Mock(), 'test', tmp_path
    def missing(bucket, key, destination):
        from pathlib import Path
        Path(destination).write_bytes(b'partial')
        raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
    storage.client.download_file.side_effect = missing
    with pytest.raises(FileNotFoundError, match='素材文件'):
        storage.path_for('recordings/missing.avi')
    assert not list(tmp_path.rglob('*.tmp'))


def test_camera_rollover_commits_before_releasing_ingestion_lock(monkeypatch, tmp_path):
    import threading
    import scenic_database as d
    from app.services import camera_stream
    from app.services.time_utils import utc_now
    from app.core.config import get_settings
    from app.services.storage import S3ObjectStorage
    with d.SessionLocal() as db:
        user = d.User(username='camera-owner', email='camera@example.com', hashed_password='test', role='admin')
        db.add(user); db.flush()
        project = d.Project(owner_username=user.username, name='camera')
        db.add(project); db.flush()
        media = d.MediaFile(project_id=project.id, uploader_username=user.username, filename='old.avi', object_key='old', media_type='camera', size_bytes=0, status='streaming')
        db.add(media); db.commit()
        other_project=d.Project(owner_username=user.username,name='other-camera-project')
        db.add(other_project);db.flush()
        other_media=d.MediaFile(project_id=other_project.id,uploader_username=user.username,filename='other.avi',object_key='other',media_type='camera',size_bytes=0,status='streaming')
        db.add(other_media);db.commit()
        other_id=other_media.id
        state = camera_stream.CameraStreamState(project_id=project.id, media_id=media.id, job_id='stream', source='test', sample_interval_seconds=1, min_confidence=.5)
        other_state=camera_stream.CameraStreamState(project_id=other_project.id,media_id=other_id,job_id='other-stream',source='test',sample_interval_seconds=1,min_confidence=.5)
        state.running = True
        manager = SimpleNamespace(_inference_lock=threading.Lock(), _lock=threading.Lock(), _streams={1:state,2:other_state}, db_module=d,
            _storage_project_id=lambda pid:f'scenic/{pid}', _safe_source=lambda source:source)
        source = tmp_path / 'camera_20261013_010000_000000_1.avi'; source.write_bytes(b'sealed')
        service = Mock(); service.seal_current_recording.return_value = {'path':source,'object_key':'central/sealed','filename':source.name,'started_at':utc_now()}
        monkeypatch.setattr(camera_stream,'get_camera_service',lambda key:service)
        fake = S3ObjectStorage.__new__(S3ObjectStorage); fake.publish_path=Mock(); fake.url_for=lambda key:'private'
        monkeypatch.setattr(camera_stream,'storage',fake)
        monkeypatch.setattr(camera_stream.job_manager, 'create', Mock(side_effect=AssertionError('Redis must not block sealing')))
        original_commit = db.commit
        checked=[]
        def commit():
            assert not manager._inference_lock.acquire(blocking=False)
            original_commit()
            with d.SessionLocal() as other:
                assert other.get(d.MediaFile,state.media_id) is not None
            checked.append(state.media_id)
        monkeypatch.setattr(db,'commit',commit)
        old_id=media.id
        assert camera_stream.solidify_project_camera_recordings(manager,db,project.id,user.username)==[old_id]
        assert checked==[state.media_id] and state.media_id!=old_id
        assert manager._inference_lock.acquire(blocking=False)
        manager._inference_lock.release()
        assert db.get(d.MediaFile,old_id).status=='sealed'
        assert db.query(d.ProcessingJob).filter_by(media_id=old_id,job_type='camera_recording',status='queued').count()==1
        assert other_state.media_id!=other_id
        assert db.get(d.MediaFile,other_id).status=='sealed'
        service.seal_current_recording.assert_called_once_with(timeout=5.0)
        fake.publish_path.assert_not_called()


def test_background_pool_bounds_concurrency_and_deduplicates_active_requests():
    import threading
    from app.services.job_manager import KeyedTaskPool
    pool = KeyedTaskPool(2, 'test-visits')
    release, first, second = threading.Event(), threading.Event(), threading.Event()
    def work(started):
        started.set()
        assert release.wait(3)
    try:
        assert pool.submit(1, work, first)
        assert first.wait(1)
        assert not pool.submit(1, work, second)
        assert pool.submit(2, work, second)
        assert second.wait(1)
        assert not pool.submit(3, work, threading.Event())
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_slow_camera_seal_does_not_block_visit_or_backup_schedulers(monkeypatch):
    import threading
    from app.services import camera_stream
    sealing, visits, backup, release = (threading.Event() for _ in range(4))
    def slow_seal():
        sealing.set()
        assert release.wait(3)
    monkeypatch.setattr(camera_stream, '_MAINTENANCE_THREADS', [])
    monkeypatch.setattr(camera_stream, '_MAINTENANCE_POOLS', [])
    monkeypatch.setattr(camera_stream, '_MAINTENANCE_STOP', threading.Event())
    monkeypatch.setattr(camera_stream, 'seal_due_camera_recordings', slow_seal)
    monkeypatch.setattr(camera_stream, 'prepare_pending_visit_recordings', lambda pool: visits.set())
    monkeypatch.setattr(camera_stream, 'publish_pending_camera_recordings', lambda pool: backup.set())
    try:
        camera_stream.start_camera_recording_maintenance()
        assert sealing.wait(2)
        assert visits.wait(1) and backup.wait(1)
    finally:
        release.set()
        camera_stream.stop_camera_recording_maintenance()
    assert not any(thread.is_alive() for thread in camera_stream._MAINTENANCE_THREADS)


def test_camera_seal_timeout_does_not_return_live_recording(monkeypatch):
    import pytest
    from app.services.central_camera_service import CentralCameraService
    service = CentralCameraService('test')
    monkeypatch.setattr(service,'acquire',Mock())
    monkeypatch.setattr(service,'release',Mock())
    service._stop_event.set()
    service._recording_path = 'incomplete.avi'
    with pytest.raises(RuntimeError,match='封存'):
        service.seal_current_recording(timeout=.1)
    service.release.assert_called_once()


def test_stopping_stream_serializes_final_seal_and_commit(tmp_path):
    import threading
    import scenic_database as d
    from app.services.camera_stream import CameraStreamManager, CameraStreamState
    db, cap = Mock(), Mock()
    state = CameraStreamState(project_id=1, media_id=1, job_id='stop', source='test', sample_interval_seconds=1, min_confidence=.5)
    state.stop_event.set()
    checks = []
    manager = SimpleNamespace(_lock=threading.Lock(), _inference_lock=threading.Lock(), _streams={1:state},
        db_module=SimpleNamespace(SessionLocal=lambda:db,MediaFile=d.MediaFile), _open_capture=lambda source:cap,
        _new_recording_path=lambda state:tmp_path/'unused.avi', _update_job=Mock())
    def check():
        acquired = manager._inference_lock.acquire(blocking=False)
        if acquired:
            manager._inference_lock.release()
        checks.append(not acquired)
    manager._save_recording = lambda *args:check()
    db.commit.side_effect=check
    CameraStreamManager._run_stream(manager,state)
    assert checks == [True,True]
    assert not state.running
    cap.release.assert_called_once()
    db.close.assert_called_once()


def test_segment_limits_are_ten_seconds_or_optional_target_count(monkeypatch, tmp_path):
    from datetime import timedelta
    from app.core.config import get_settings
    from app.services.camera_stream import CameraStreamState, recording_segment_due
    from app.services.time_utils import utc_now
    state = CameraStreamState(project_id=1, media_id=1, job_id='segment', source='0', sample_interval_seconds=1, min_confidence=.5)
    settings = get_settings()
    monkeypatch.setattr(settings, 'camera_recording_segment_seconds', 10)
    monkeypatch.setattr(settings, 'camera_recording_segment_targets', 0)
    start = utc_now()
    state.recording_started_at = start
    assert not recording_segment_due(state, start+timedelta(seconds=9.9))
    assert recording_segment_due(state, start+timedelta(seconds=10))
    monkeypatch.setattr(settings, 'camera_recording_segment_targets', 2)
    state.segment_targets = 2
    assert recording_segment_due(state, start+timedelta(seconds=1))
    state.segment_targets = 0
    monkeypatch.setattr(settings, 'camera_recording_segment_max_bytes', 1)
    state.recording_path = tmp_path/'recording.avi'
    state.recording_path.write_bytes(b'video')
    assert recording_segment_due(state, start)


def test_s3_only_reads_unchanged_sealed_local_recordings(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.services.storage import S3ObjectStorage
    monkeypatch.setattr(get_settings(), 'object_storage_dir', tmp_path/'objects')
    store = S3ObjectStorage.__new__(S3ObjectStorage)
    store.client, store.bucket, store.cache_dir = Mock(), 'test', tmp_path/'cache'
    key = 'projects/scenic/1/media/1/recordings/sealed.avi'
    path = get_settings().object_storage_dir/key
    path.parent.mkdir(parents=True)
    path.write_bytes(b'sealed-video')
    marker = path.with_suffix('.avi.sealed')
    marker.write_text(str(path.stat().st_size))
    assert store.path_for(key)==path
    store.client.download_file.assert_not_called()
    # A changed source must not silently use a stale local seal marker.
    path.write_bytes(b'changed')
    store.client.download_file.side_effect=lambda bucket,key,target:__import__('pathlib').Path(target).write_bytes(b'published')
    assert store.path_for(key).read_bytes()==b'published'


def test_sealed_fingerprint_reuses_bytes_but_detects_same_size_mutation(monkeypatch, tmp_path):
    import os
    from app.services import scenic_editing
    path=tmp_path/'sealed.avi'
    path.write_bytes(b'original')
    path.with_suffix('.avi.sealed').write_text(str(path.stat().st_size))
    original=scenic_editing._hash_source
    calls=[]
    def hash_source(file):
        calls.append(True)
        return original(file)
    monkeypatch.setattr(scenic_editing,'_hash_source',hash_source)
    first=scenic_editing.source_sha256(path)
    assert scenic_editing.source_sha256(path)==first
    assert len(calls)==(1 if os.name=='posix' else 2)
    saved=path.stat()
    path.write_bytes(b'modified')
    os.utime(path,ns=(saved.st_atime_ns,saved.st_mtime_ns))
    assert scenic_editing.source_sha256(path)!=first
    assert len(calls)==(2 if os.name=='posix' else 3)


def test_camera_upload_outbox_is_idempotent_and_does_not_publish_during_seal(monkeypatch, tmp_path):
    import scenic_database as d
    from app.services import camera_stream
    from app.workers import tasks
    from app.core.config import get_settings
    from app.services.storage import S3ObjectStorage
    fake = S3ObjectStorage.__new__(S3ObjectStorage)
    fake.publish_path=Mock()
    monkeypatch.setattr(camera_stream, 'storage', fake)
    monkeypatch.setattr(tasks, 'storage', fake)
    with d.SessionLocal() as db:
        user=d.User(username='publisher',email='publisher@example.com',hashed_password='test')
        db.add(user);db.flush()
        project=d.Project(owner_username=user.username,name='publisher')
        db.add(project);db.flush()
        media=d.MediaFile(project_id=project.id,uploader_username=user.username,filename='part.avi',object_key='projects/scenic/1/part.avi',media_type='camera')
        db.add(media);db.flush()
        path=get_settings().object_storage_dir/media.object_key
        path.parent.mkdir(parents=True);path.write_bytes(b'complete-video')
        manager=SimpleNamespace(db_module=d)
        job_id=camera_stream.queue_sealed_camera_recording(manager,db,media)
        assert camera_stream.queue_sealed_camera_recording(manager,db,media)==job_id
        db.commit()
        media_id=media.id
        fake.publish_path.assert_not_called()
    tasks.publish_camera_recording(job_id,media_id,'scenic')
    tasks.publish_camera_recording(job_id,media_id,'scenic')
    fake.publish_path.assert_called_once()
    with d.SessionLocal() as db:
        assert db.get(d.ProcessingJob,job_id).status=='succeeded'
        assert db.get(d.MediaFile,media_id).size_bytes==len(b'complete-video')
