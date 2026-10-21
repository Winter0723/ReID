import io
import logging
import ssl
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image
from fastapi import HTTPException


def test_gallery_and_local_objects_require_path_bound_signatures(client):
    from app.core.config import get_settings
    from app.services.asset_delivery import signed_asset_url
    settings = get_settings()
    for root, prefix in ((settings.gallery_dir, "/gallery"), (settings.object_storage_dir, "/objects")):
        (root / "private.jpg").write_bytes(b"synthetic-private")
        (root / "other.jpg").write_bytes(b"other")
        url = f"{prefix}/private.jpg"
        assert client.get(url).status_code == 403
        signed = signed_asset_url(url)
        assert client.get(signed).content == b"synthetic-private"
        assert client.get(signed.replace("private.jpg", "other.jpg")).status_code == 403
        assert client.get(url + "?asset_expires=1&asset_signature=bad").status_code == 403


@pytest.mark.parametrize("scope", ["core", "scenic"])
def test_logout_revokes_access_immediately(client, register_user, register_scenic_user, scope):
    user = (register_user if scope == "core" else register_scenic_user)()
    prefix = "/api/v1/auth" if scope == "core" else "/api/v1/scenic/auth"
    assert client.get(prefix + "/me", headers=user["headers"]).status_code == 200
    assert client.post(prefix + "/logout", headers={"Authorization": "Bearer " + user["refresh_token"]}).status_code == 200
    assert client.get(prefix + "/me", headers=user["headers"]).status_code == 401


def test_password_change_revokes_access_and_last_admin_cannot_deactivate(client, register_user):
    user = register_user()
    assert client.delete("/api/v1/auth/me", headers=user["headers"]).status_code == 400
    assert client.post("/api/v1/auth/password", headers=user["headers"], json={
        "current_password": user["password"], "new_password": "a-new-strong-password"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=user["headers"]).status_code == 401


def test_camera_policy_rejects_untrusted_endpoints_and_keeps_secret_out_of_id(monkeypatch):
    from app.core.config import get_settings
    from app.schemas import CameraStartRequest
    from app.services.camera_policy import capture_source, safe_camera_name
    settings = get_settings()
    secret = "rtsp://camera:private-password@192.0.2.10/live"
    monkeypatch.setattr(settings, "camera_sources", {"entrance": secret})
    assert CameraStartRequest(camera_source="entrance").camera_source == "entrance"
    assert capture_source("entrance") == secret
    for source in (secret, "http://127.0.0.1/admin", "file:///etc/passwd", "C:/private.mp4", "99"):
        with pytest.raises(ValueError):
            CameraStartRequest(camera_source=source)
    assert "password" not in safe_camera_name(secret)


def test_smtp_uses_verified_tls(monkeypatch):
    from app.core.config import get_settings
    from app.services import email_service
    settings = get_settings()
    for name, value in {"email_code_dev_mode": False, "smtp_host": "smtp.example.invalid",
                        "smtp_from": "test@example.invalid", "smtp_username": ""}.items():
        monkeypatch.setattr(settings, name, value)
    for implicit in (True, False):
        monkeypatch.setattr(settings, "smtp_use_ssl", implicit)
        monkeypatch.setattr(settings, "smtp_use_tls", True)
        factory = MagicMock()
        monkeypatch.setattr(email_service.smtplib, "SMTP_SSL" if implicit else "SMTP", factory)
        email_service.send_email_code("visitor@example.invalid", "123456", "login")
        context = factory.call_args.kwargs["context"] if implicit else factory.return_value.__enter__.return_value.starttls.call_args.kwargs["context"]
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_email_code_is_single_use():
    import database
    from app.services.email_service import create_email_code, verify_email_code
    with database.SessionLocal() as db:
        code, _ = create_email_code(db, database.EmailLoginCode, "one@example.invalid", "register")
        db.commit()
        verify_email_code(db, database.EmailLoginCode, "one@example.invalid", code, "register")
        db.commit()
    with database.SessionLocal() as db:
        with pytest.raises(HTTPException):
            verify_email_code(db, database.EmailLoginCode, "one@example.invalid", code, "register")


def test_exif_orientation_is_applied_before_reference_crop():
    from app.services.media_service import load_verified_rgb_image
    image = Image.new("RGB", (120, 240), "green")
    exif = image.getexif(); exif[274] = 6
    source = io.BytesIO(); image.save(source, "JPEG", exif=exif)
    assert load_verified_rgb_image(source.getvalue()).size == (240, 120)


def test_http_client_logs_are_not_enabled_by_debug_app_logging():
    from app.core.observability import configure_logging
    configure_logging("DEBUG")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore.connection").isEnabledFor(logging.DEBUG)
    configure_logging("INFO")


def test_identity_verification_blocks_owned_media(monkeypatch):
    from app.core.config import get_settings
    from app.services.visitor_auth import owned_visit
    from app.services.time_utils import utc_now
    monkeypatch.setattr(get_settings(), "visitor_require_staff_verification", True)
    row = SimpleNamespace(session=SimpleNamespace(checked_out_at=utc_now()), verified_at=None)
    db = MagicMock(); db.query.return_value.filter_by.return_value.first.return_value = row
    with pytest.raises(HTTPException) as error:
        owned_visit(db, 1, SimpleNamespace(id=1))
    assert error.value.status_code == 403
    row.verified_at = utc_now()
    assert owned_visit(db, 1, SimpleNamespace(id=1)) is row


def test_recovery_requeues_committed_but_undelivered_variant(monkeypatch, client, register_scenic_user):
    import scenic_database as dbm
    from app.services import task_recovery
    from app.services.time_utils import utc_now
    user = register_scenic_user()
    pid = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": "Recovery"}).json()["id"]
    with dbm.SessionLocal() as db:
        session = dbm.TouristSession(project_id=pid, ticket_code="SYNTHETIC", entry_image_object_key="synthetic.jpg", created_by_username=user["username"])
        db.add(session); db.flush()
        vlog = dbm.ScenicVlogJob(session_id=session.id, created_by_username=user["username"])
        db.add(vlog); db.flush()
        job = dbm.ProcessingJob(id="lost-job", project_id=pid, job_type="scenic_vlog_variant", status="queued")
        db.add(job); db.flush()
        db.add(dbm.ScenicVlogVariant(vlog_job_id=vlog.id, orientation="landscape", processing_job_id=job.id,
            status="queued", updated_at=utc_now() - timedelta(minutes=5)))
        db.commit()
    def assert_worker_can_start(job_id, task_path, queued_id, variant_id, username):
        from filelock import FileLock
        with FileLock(task_recovery.execution_lock_path(variant_id, job_id), timeout=0):
            pass
    enqueue = MagicMock(side_effect=assert_worker_can_start)
    monkeypatch.setattr(task_recovery, "queue_state", lambda _: None)
    monkeypatch.setattr(task_recovery.job_manager, "enqueue", enqueue)
    task_recovery.recover_variants()
    assert enqueue.call_count == 1
    assert enqueue.call_args.args[0] == "lost-job"
    task_recovery.recover_variants()  # Retry window prevents immediate duplicate admission.
    assert enqueue.call_count == 1


def test_retention_dry_run_then_delete_only_expired_personal_media(client, register_scenic_user):
    import importlib.util
    from pathlib import Path
    import scenic_database as d
    from app.services.storage import storage
    from app.services.time_utils import utc_now
    spec = importlib.util.spec_from_file_location("cleanup_test", Path(__file__).resolve().parents[2] / "scripts/cleanup_personal_data.py")
    cleanup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cleanup)
    user = register_scenic_user()
    pid = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": "Retention"}).json()["id"]
    stored = storage.save_upload(pid, "synthetic.jpg", io.BytesIO(b"test"))
    with d.SessionLocal() as db:
        old = d.TouristSession(project_id=pid, ticket_code="OLD", entry_image_object_key=stored.object_key,
            created_by_username=user["username"], created_at=utc_now() - timedelta(days=40))
        fresh = d.TouristSession(project_id=pid, ticket_code="NEW", entry_image_object_key="new.jpg", created_by_username=user["username"])
        db.add_all([old, fresh]); db.commit(); old_id, fresh_id = old.id, fresh.id
    assert cleanup.cleanup()["sessions"] == [{"id": old_id, "objects": 1}]
    assert storage.path_for(stored.object_key).exists()
    assert cleanup.cleanup(apply=True)["sessions"] == [{"id": old_id, "objects": 1}]
    assert not storage.path_for(stored.object_key).exists()
    with d.SessionLocal() as db:
        assert db.get(d.TouristSession, old_id) is None
        assert db.get(d.TouristSession, fresh_id) is not None


def test_logging_and_audit_redact_url_credentials():
    from app.services.redaction import redact_credentials
    value = redact_credentials("rtsp://camera:private-password@host/live https://oauth.invalid/?secret=hidden&code=one-time&ok=1")
    assert "private-password" not in value and "hidden" not in value and "one-time" not in value
    assert "ok=1" in value


def test_camera_enumeration_skips_unconfigured_indices_and_bridge_ids_roundtrip(monkeypatch):
    from app.services import camera_bridge, camera_stream
    from app.services.camera_policy import public_camera_devices, validate_camera_source
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "camera_sources", {"0": "0", "entrance": "rtsp://private:secret@host/live"})
    def refused(_):
        raise ValueError("Unconfigured index")
    assert camera_stream._probe_physical_camera_devices(3, refused) == []
    bridge_source = "rtsp://mediamtx:8554/local/device-1"
    monkeypatch.setattr(camera_bridge, "enabled", lambda: True)
    monkeypatch.setattr(camera_bridge, "devices", lambda: [{"source": bridge_source, "label": "USB"}])
    devices = public_camera_devices([{"source": "2"}, {"source": bridge_source, "label": "USB"}])
    assert all("rtsp://" not in str(row) and "secret" not in str(row) for row in devices)
    assert validate_camera_source(devices[0]["source"]) == bridge_source
    # Explicitly configured numeric devices remain selectable even if the
    # current probe did not find them; unconfigured index 2 stays hidden.
    assert {row["source"] for row in devices} == {devices[0]["source"], "0", "entrance"}
    assert validate_camera_source("0") == "0"
    with pytest.raises(ValueError):
        validate_camera_source("2")


def test_s3_snapshot_preserves_metadata_and_refuses_corruption_or_overwrite(monkeypatch, tmp_path):
    import importlib.util
    import json
    import sys
    from pathlib import Path
    import boto3
    from app.core.config import get_settings
    spec = importlib.util.spec_from_file_location("s3_snapshot_test", Path(__file__).resolve().parents[2] / "scripts/object_store_backup.py")
    snapshot = importlib.util.module_from_spec(spec); spec.loader.exec_module(snapshot)
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{"Contents": [{"Key": "private/picture.jpg"}]}]
    client.download_file.side_effect = lambda bucket, key, path: Path(path).write_bytes(b"synthetic-media")
    client.head_object.return_value = {"ContentType": "image/jpeg", "Metadata": {"sha256": "source-hash"}}
    client.list_objects_v2.return_value = {"KeyCount": 0}
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)
    monkeypatch.setattr(get_settings(), "object_storage_mode", "s3")
    directory = tmp_path / "snapshot"
    monkeypatch.setattr(sys, "argv", ["snapshot", "backup", str(directory), "--writers-stopped"])
    snapshot.main()
    monkeypatch.setattr(sys, "argv", ["snapshot", "restore", str(directory), "--writers-stopped"])
    snapshot.main()
    assert client.upload_file.call_args.kwargs["ExtraArgs"] == client.head_object.return_value
    client.upload_file.reset_mock()
    client.list_objects_v2.return_value = {"KeyCount": 1}
    with pytest.raises(SystemExit):
        snapshot.main()
    assert not client.upload_file.called
    client.list_objects_v2.return_value = {"KeyCount": 0}
    manifest = json.loads((directory / "manifest.json").read_text())
    (directory / manifest[0]["file"]).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        snapshot.main()
    assert not client.upload_file.called
