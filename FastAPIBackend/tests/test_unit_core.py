import datetime
import importlib
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def _test_runtime_path(filename: str) -> Path:
    root = Path(os.environ["MYREID_TEST_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


@pytest.mark.parametrize("database_module_name", ["database", "scenic_database"])
def test_sqlite_alembic_upgrade_repairs_active_media_duplicates_before_index(
    database_module_name,
):
    db_module = importlib.import_module(database_module_name)
    scope = "scenic" if database_module_name == "scenic_database" else "core"
    backend_dir = Path(__file__).resolve().parents[1]
    config_filename = "alembic.scenic.ini" if scope == "scenic" else "alembic.ini"
    config = Config(str(backend_dir / config_filename))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", db_module.SQLALCHEMY_DATABASE_URL)
    config.set_main_option("myreid.migration_scope", scope)
    command.downgrade(config, "20260604120000")

    username = f"legacy_{database_module_name}"
    with db_module.SessionLocal() as db:
        db.add(
            db_module.User(
                username=username,
                email=f"{username}@example.com",
                hashed_password="unused",
                role="operator",
            )
        )
        db.flush()
        project = db_module.Project(
            name="Legacy duplicate project",
            description="",
            owner_username=username,
        )
        db.add(project)
        db.flush()
        db.add_all(
            [
                db_module.MediaFile(
                    project_id=project.id,
                    uploader_username=username,
                    filename=f"legacy-{index}.jpg",
                    object_key=f"legacy/{database_module_name}/{index}.jpg",
                    sha256="a" * 64,
                    media_type="image",
                    status="uploaded",
                )
                for index in (1, 2)
            ]
        )
        db.commit()
        project_id = project.id

    db_module.init_database()

    with db_module.SessionLocal() as db:
        media_rows = (
            db.query(db_module.MediaFile)
            .filter(db_module.MediaFile.project_id == project_id)
            .order_by(db_module.MediaFile.id.asc())
            .all()
        )
        assert [row.status for row in media_rows] == ["uploaded", "duplicate"]
        assert media_rows[1].duplicate_of_media_id == media_rows[0].id
    with db_module.engine.connect() as connection:
        index_names = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA index_list('media_files')")
        }
    assert "uq_media_files_project_sha_active" in index_names


def test_paid_wechat_order_cannot_regress_on_delayed_failure_notification():
    from types import SimpleNamespace

    from app.api.scenic.system_routes import _apply_wechat_payment_status
    from app.services.payments import WechatPaymentNotification

    order = SimpleNamespace(
        order_no="SV-PAID",
        amount_cents=990,
        currency="CNY",
        status="paid",
        paid_at=datetime.datetime.now(datetime.UTC),
        delivered_at=datetime.datetime.now(datetime.UTC),
        provider_trade_state="SUCCESS",
        provider_trade_no="WX-PAID",
        payment_error="",
        last_payment_sync_at=None,
        notify_event_id="paid-event",
        session=SimpleNamespace(status="paid"),
    )
    delayed_failure = WechatPaymentNotification(
        event_id="delayed-failure-event",
        out_trade_no=order.order_no,
        trade_state="CLOSED",
        amount_cents=order.amount_cents,
        currency=order.currency,
    )

    assert _apply_wechat_payment_status(order, delayed_failure) is False
    assert order.status == "paid"
    assert order.session.status == "paid"
    assert order.provider_trade_state == "SUCCESS"
    assert order.notify_event_id == "delayed-failure-event"


def test_email_code_lifecycle_records_attempts():
    import database
    from app.api import common as main

    db = database.SessionLocal()
    try:
        code, record = main._create_email_code(db, "Person@Example.COM", "register")
        db.commit()

        with pytest.raises(main.HTTPException) as wrong:
            main._verify_email_code(db, "person@example.com", "000000", "register")
        assert wrong.value.status_code == 401

        db.refresh(record)
        assert record.attempts == 1
        assert main._verify_email_code(db, "person@example.com", code, "register").id == record.id
        db.commit()
        db.refresh(record)
        assert record.consumed_at is not None
    finally:
        db.close()


def test_filter_pagination_and_timestamp_helpers():
    from app.api import common as main

    compact = main._compact_filters(
        {
            "camera_id": " cam-a ",
            "media_id": None,
            "start_time": "",
            "min_similarity": 0.55,
        }
    )
    assert compact == {"camera_id": "cam-a", "min_similarity": 0.55}

    assert main._pagination_payload(page=2, page_size=10, total=21) == {
        "page": 2,
        "page_size": 10,
        "total": 21,
        "total_pages": 3,
        "has_previous": True,
        "has_next": True,
    }

    assert main._timestamp_to_seconds_for_payload("01:02:03") == 3723
    parsed = main._timestamp_to_seconds_for_payload(datetime.datetime(2026, 5, 21, 1, 2, 3))
    assert isinstance(parsed, float)


def test_camera_recording_url_and_seek_helpers():
    import database
    from app.api import common as main

    started_at = datetime.datetime(2026, 5, 21, 1, 2, 3, tzinfo=datetime.UTC)
    media = database.MediaFile(
        id=1,
        project_id=1,
        uploader_username="camera_user",
        filename="camera.mp4",
        object_key="projects/1/media/1/recordings/camera.mp4",
        content_type="application/x-camera-recording",
        size_bytes=0,
        sha256="pending",
        media_type="camera",
        status="streaming",
        created_at=started_at,
    )
    assert main._media_download_url(media) is None
    assert main._media_video_seek_seconds(media, started_at.isoformat()) is None

    media.content_type = "video/mp4"
    media.size_bytes = 1024
    captured_at = started_at + datetime.timedelta(seconds=3.5)
    assert main._media_download_url(media).startswith(
        "/api/v1/projects/1/media/1/content?asset_expires="
    )
    assert main._media_video_seek_seconds(media, captured_at.isoformat()) == 3.5


def test_camera_recording_save_updates_media_record():
    import database
    from app.api import common as main
    from app.services.camera_stream import CameraStreamState

    db = database.SessionLocal()
    recording_path = _test_runtime_path("myreid_camera_recording_unit.mp4")
    recording_path.write_bytes(b"fake mp4 bytes")
    try:
        user = database.User(username="camera_owner", email="camera@example.com", hashed_password=main.get_password_hash("password"), role="admin")
        db.add(user)
        db.flush()
        project = database.Project(name="Camera Project", description="", owner_username=user.username, project_type="camera", camera_source="0")
        db.add(project)
        db.flush()
        media = database.MediaFile(
            project_id=project.id,
            uploader_username=user.username,
            filename="pending.mp4",
            object_key=f"projects/{project.id}/camera/pending.mp4",
            content_type="application/x-camera-recording",
            size_bytes=0,
            sha256="pending",
            media_type="camera",
            status="streaming",
        )
        db.add(media)
        db.flush()
        state = CameraStreamState(
            project_id=project.id,
            media_id=media.id,
            job_id="unit-camera-job",
            source="0",
            sample_interval_seconds=1.0,
            min_confidence=0.35,
        )
        state.recording_filename = "camera-recording.mp4"

        main.camera_stream_manager._save_recording(db, state, recording_path)
        db.commit()
        db.refresh(media)

        assert media.content_type == "video/mp4"
        assert media.size_bytes == len(b"fake mp4 bytes")
        assert media.object_key.endswith("camera-recording.mp4")
        assert state.recording_url.split("?", 1)[0] == f"/objects/{media.object_key}"
    finally:
        db.close()
        recording_path.unlink(missing_ok=True)


def test_camera_probe_keeps_scanning_after_index_gap():
    from app.services.camera_stream import _probe_physical_camera_devices

    available = {0, 1, 2, 3, 5}

    class FakeFrame:
        shape = (480, 640, 3)

    class FakeCapture:
        def __init__(self, index: int):
            self.index = index

        def isOpened(self):
            return self.index in available

        def read(self):
            return (True, FakeFrame()) if self.isOpened() else (False, None)

        def release(self):
            pass

    devices = _probe_physical_camera_devices(8, lambda source: FakeCapture(int(source)))
    assert [device["index"] for device in devices] == [0, 1, 2, 3, 5]


def test_camera_services_are_shared_per_source_but_isolated_between_sources():
    from app.services.central_camera_service import get_camera_service

    iriun = get_camera_service("3")
    usb = get_camera_service("5")

    assert get_camera_service("3") is iriun
    assert get_camera_service("5") is usb
    assert iriun is not usb
    assert iriun.source == "3"
    assert usb.source == "5"


def test_camera_service_releases_only_after_last_consumer(monkeypatch):
    from app.services.central_camera_service import CentralCameraService

    service = CentralCameraService("7", use_environment_source=False)
    starts: list[str] = []
    stops: list[str] = []
    monkeypatch.setattr(service, "_start_thread", lambda: starts.append("start"))
    monkeypatch.setattr(service, "_stop_thread", lambda: stops.append("stop"))

    service.acquire()
    service.acquire()
    assert service.status()["consumers"] == 2

    service.release()
    assert service.status()["consumers"] == 1
    assert stops == []

    service.release()
    assert service.status()["consumers"] == 0
    assert stops == ["stop"]
    assert starts == ["start", "start"]


def test_persistent_camera_service_ignores_transient_release(monkeypatch):
    from app.services.central_camera_service import CentralCameraService

    service = CentralCameraService("8", use_environment_source=False)
    stops: list[str] = []
    monkeypatch.setattr(service, "_start_thread", lambda: None)
    monkeypatch.setattr(service, "_stop_thread", lambda: stops.append("stop"))

    service.start()
    service.acquire()
    service.release()
    assert stops == []

    service.stop()
    assert stops == ["stop"]


def test_camera_restart_reconciles_orphaned_stream_rows(client, register_scenic_user):
    import scenic_database as database
    from app.services.camera_stream import reconcile_orphaned_camera_streams

    user = register_scenic_user("camera_restart_owner", "camera-restart@example.com")
    project_response = client.post(
        "/api/v1/scenic/projects",
        headers=user["headers"],
        json={"name": "Camera restart project", "description": "restart reconciliation"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]

    with database.SessionLocal() as db:
        media = database.MediaFile(
            project_id=project_id,
            uploader_username=user["username"],
            filename="orphaned-camera.avi",
            object_key=f"projects/scenic/{project_id}/camera/orphaned-camera.avi",
            content_type="video/x-msvideo",
            size_bytes=0,
            sha256="a" * 64,
            media_type="camera",
            status="streaming",
        )
        db.add(media)
        db.flush()
        db.add(database.ProcessingJob(
            id="orphaned-camera-job",
            project_id=project_id,
            media_id=media.id,
            job_type="camera_stream",
            status="running",
            progress=0.5,
            message="capturing",
        ))
        db.commit()

    reconciled = reconcile_orphaned_camera_streams(database)
    assert reconciled == {"media": 1, "jobs": 1}

    with database.SessionLocal() as db:
        media = db.query(database.MediaFile).filter(database.MediaFile.sha256 == "a" * 64).one()
        job = db.query(database.ProcessingJob).filter(database.ProcessingJob.id == "orphaned-camera-job").one()
        assert media.status == "failed"
        assert job.status == "failed"
        assert job.progress == 1.0
        assert job.error == "服务异常重启，相机采集任务已终止"


def test_camera_status_fallback_uses_the_requested_source(monkeypatch):
    from app.services import media_service

    class FakeService:
        def __init__(self, source: str):
            self.source = source

        def status(self):
            return {
                "running": True,
                "source": self.source,
                "latest_frame_url": f"/gallery/central_camera/{self.source}/latest.jpg",
                "latest_frame_version": 1,
            }

    services = {"3": FakeService("3"), "5": FakeService("5")}
    monkeypatch.setattr(media_service, "find_camera_service", services.get)

    usb_status = media_service.merge_central_camera_status({}, camera_source="5")

    assert usb_status["monitor_source"] == "5"
    assert usb_status["latest_frame_url"].split("?", 1)[0] == "/gallery/central_camera/5/latest.jpg"


def test_scenic_camera_order_keeps_usb_alongside_phone_cameras(monkeypatch):
    from app.api.scenic import system_routes

    monkeypatch.setattr(
        system_routes,
        "list_camera_devices",
        lambda: [
            {"source": "0", "label": "iPhone Camera", "type": "iphone"},
            {"source": "4", "label": "USB Camera", "type": "camera"},
        ],
    )

    devices = system_routes._ordered_camera_devices()
    assert [device["source"] for device in devices] == ["0", "4"]


def test_fake_payment_client_is_local_only():
    from app.core.config import Settings
    from app.services.payments import (
        FakeWechatNativePaymentClient,
        PaymentConfigurationError,
        get_wechat_native_payment_client,
    )

    local_client = get_wechat_native_payment_client(
        Settings(app_env="development", payment_provider="")
    )
    assert isinstance(local_client, FakeWechatNativePaymentClient)

    with pytest.raises(PaymentConfigurationError):
        get_wechat_native_payment_client(
            Settings(app_env="production", payment_provider="fake_wechat")
        )

    with pytest.raises(PaymentConfigurationError):
        get_wechat_native_payment_client(
            Settings(app_env="staging", payment_provider="fake_wechat")
        )


def test_password_hash_supports_utf8_beyond_bcrypt_byte_limit():
    from passlib.context import CryptContext

    from app.core.security import get_password_hash, verify_password

    password = "长密码" * 30
    assert len(password.encode("utf-8")) > 72
    password_hash = get_password_hash(password)
    assert password_hash.startswith("$bcrypt-sha256$")
    assert verify_password(password, password_hash)
    assert not verify_password(password + "x", password_hash)

    legacy_hash = CryptContext(schemes=["bcrypt"]).hash("legacy-password")
    assert verify_password("legacy-password", legacy_hash)
    assert not verify_password("错误" * 40, legacy_hash)


def test_paid_wechat_status_requires_exact_amount_and_currency():
    from types import SimpleNamespace

    from app.api.scenic.system_routes import _apply_wechat_payment_status
    from app.services.payments import PaymentVerificationError, WechatPaymentStatus

    def order():
        return SimpleNamespace(
            order_no="SV-PRICE-CHECK",
            amount_cents=990,
            currency="CNY",
            status="pending",
            provider_trade_state="NOTPAY",
            provider_trade_no="",
            last_payment_sync_at=None,
            payment_error="",
            notify_event_id="",
            paid_at=None,
            delivered_at=None,
            session=SimpleNamespace(status="vlog_ready"),
        )

    with pytest.raises(PaymentVerificationError, match="缺少金额"):
        _apply_wechat_payment_status(
            order(),
            WechatPaymentStatus(out_trade_no="SV-PRICE-CHECK", trade_state="SUCCESS", currency="CNY"),
        )
    with pytest.raises(PaymentVerificationError, match="金额不匹配"):
        _apply_wechat_payment_status(
            order(),
            WechatPaymentStatus(
                out_trade_no="SV-PRICE-CHECK",
                trade_state="SUCCESS",
                amount_cents=1,
                currency="CNY",
            ),
        )
    with pytest.raises(PaymentVerificationError, match="缺少币种"):
        _apply_wechat_payment_status(
            order(),
            WechatPaymentStatus(
                out_trade_no="SV-PRICE-CHECK",
                trade_state="SUCCESS",
                amount_cents=990,
            ),
        )

    valid_order = order()
    assert _apply_wechat_payment_status(
        valid_order,
        WechatPaymentStatus(
            out_trade_no="SV-PRICE-CHECK",
            trade_state="SUCCESS",
            transaction_id="WX-1",
            amount_cents=990,
            currency="CNY",
        ),
    )
    assert valid_order.status == "paid"


def test_scenic_database_requires_explicit_production_url(tmp_path):
    from app.core.config import resolve_scenic_database_url

    with pytest.raises(RuntimeError, match="SCENIC_DATABASE_URL is required"):
        resolve_scenic_database_url(
            "",
            primary_database_url="postgresql+psycopg://app@example.test/app",
            app_env="production",
            base_dir=tmp_path,
        )

    with pytest.raises(RuntimeError, match="must use a production database"):
        resolve_scenic_database_url(
            "sqlite:///scenic.db",
            primary_database_url="postgresql+psycopg://app@example.test/app",
            app_env="staging",
            base_dir=tmp_path,
        )


def test_scenic_database_uses_a_sibling_sqlite_file_in_development(tmp_path):
    from app.core.config import resolve_scenic_database_url

    primary_url = f"sqlite:///{(tmp_path / 'primary.db').as_posix()}"
    assert resolve_scenic_database_url(
        "",
        primary_database_url=primary_url,
        app_env="development",
        base_dir=tmp_path,
    ) == f"sqlite:///{(tmp_path / 'scenic_system.db').as_posix()}"


def test_storage_rejects_oversized_upload_while_streaming(tmp_path, monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace

    from app.services import storage as storage_module

    monkeypatch.setattr(
        storage_module,
        "get_settings",
        lambda: SimpleNamespace(max_upload_mb=1, object_storage_dir=tmp_path),
    )
    object_storage = storage_module.LocalObjectStorage()

    with pytest.raises(storage_module.UploadTooLargeError):
        object_storage.save_upload(
            1,
            "too-large.bin",
            BytesIO(b"x" * (1024 * 1024 + 1)),
        )

    staging_dir = tmp_path / ".staging"
    assert staging_dir.exists()
    assert list(staging_dir.iterdir()) == []


def test_local_storage_rejects_path_traversal(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services import storage as storage_module

    monkeypatch.setattr(
        storage_module,
        "get_settings",
        lambda: SimpleNamespace(max_upload_mb=1, object_storage_dir=tmp_path),
    )
    object_storage = storage_module.LocalObjectStorage()

    with pytest.raises(storage_module.InvalidObjectKeyError):
        object_storage.path_for("projects/1/../../outside.txt")

    assert storage_module.ObjectStorage._safe_filename(r"..\..\secret.jpg") == "secret.jpg"


def test_image_decode_rejects_excessive_pixel_count():
    from io import BytesIO

    from PIL import Image

    from app.api.common import ImagePixelLimitError, _load_verified_rgb_image

    buffer = BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    with pytest.raises(ImagePixelLimitError):
        _load_verified_rgb_image(buffer.getvalue(), max_pixels=10)


def test_blocking_api_handlers_are_declared_synchronous():
    import inspect

    from app.api.routes import auth, projects, search
    from app.api.scenic import order_routes

    assert not inspect.iscoroutinefunction(projects.v1_upload_media)
    assert not inspect.iscoroutinefunction(search.v1_search_image)
    assert not inspect.iscoroutinefunction(auth.v1_upload_avatar)
    assert not inspect.iscoroutinefunction(order_routes.v1_create_scenic_order)
    assert inspect.iscoroutinefunction(order_routes.v1_wechat_payment_notify)


def test_job_manager_clamps_progress_and_tracks_terminal_duration():
    from app.api import common as main

    job = main.job_manager.create("unit-test", "queued")
    assert job.status == "queued"

    updated = main.job_manager.update(job.id, status="running", progress=2.5)
    assert updated.progress == 1.0

    updated = main.job_manager.update(job.id, status="failed", progress=-1, error="boom")
    assert updated.progress == 0.0
    assert updated.error == "boom"
    assert main.job_manager.get(job.id).status == "failed"


def test_job_manager_can_disable_local_job_fallback(monkeypatch):
    from app.api import common as main

    job = main.job_manager.create("unit-test", "queued")
    monkeypatch.setattr(main.job_manager, "allow_local_job_fallback", False)

    with pytest.raises(main.LocalJobFallbackDisabled):
        main.job_manager.enqueue(job.id, "app.workers.tasks.process_media_index", job.id)

    failed = main.job_manager.get(job.id)
    assert failed.status == "failed"
    assert "ALLOW_LOCAL_JOB_FALLBACK" in failed.error


def test_milvus_live_ingestion_does_not_flush_and_search_reads_new_writes(monkeypatch):
    from unittest.mock import Mock
    import torch
    from app.services.vector_store import MilvusVectorStore, VectorPoint
    store = MilvusVectorStore()
    client = Mock()
    client.search.return_value = [[{"id": "fresh", "distance": 1.0, "entity": {
        "vector_id": "fresh", "project_id": 1, "media_id": 1, "path": "crop.jpg",
    }}]]
    store._client = client
    monkeypatch.setattr(store, "ensure_ready", lambda: None)
    point = VectorPoint(id="fresh", vector=torch.ones(store.dimension), path="crop.jpg", metadata={"project_id": 1, "media_id": 1})
    assert store.upsert([point]) == 1
    client.flush.assert_not_called()
    result = store.search(point.vector, top_k=1, project_id=1)
    assert result[0].id == "fresh"
    assert client.search.call_args.kwargs["consistency_level"] == "Strong"
    assert client.search.call_args.kwargs["filter"] == "project_id == 1"
