import os
import shutil
import sys
import tempfile
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
TEST_PARENT = ROOT_DIR / "tmp" / "pytest"
TEST_PARENT.mkdir(parents=True, exist_ok=True)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="run_", dir=TEST_PARENT)).resolve()
os.environ["MYREID_TEST_ROOT"] = str(TEST_ROOT)

TEST_ROOT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

# Never inherit a developer's production database, storage, SMTP or Redis.
for key in tuple(os.environ):
    if key.startswith(("WECHAT_", "SMTP_", "S3_", "VISITOR_WECHAT_")):
        os.environ.pop(key)
os.environ.update({
    "APP_ENV": "test", "DATABASE_URL": f"sqlite:///{TEST_ROOT / 'test.db'}",
    "SCENIC_DATABASE_URL": f"sqlite:///{TEST_ROOT / 'scenic_test.db'}",
    "SECRET_KEY": "test-secret-key", "EMAIL_CODE_DEV_MODE": "1",
    "OBJECT_STORAGE_MODE": "local", "OBJECT_STORAGE_DIR": str(TEST_ROOT / "object_storage"),
    "GALLERY_DIR": str(TEST_ROOT / "gallery"), "VECTOR_BACKEND": "memory",
    "REDIS_URL": "redis://127.0.0.1:1/15", "MODEL_DEVICE": "cpu",
    "ALLOW_LOCAL_JOB_FALLBACK": "1", "PAYMENT_PROVIDER": "fake",
    "VISITOR_AUTH_PROVIDER": "wechat", "VISITOR_PAYMENT_PROVIDER": "wechat",
    "BOOTSTRAP_ADMIN_EMAIL": "", "SMTP_HOST": "", "MYREID_CAMERA_BRIDGE_DIR": "",
    "CAMERA_AUTO_START": "0", "SCENIC_CAMERA_AUTO_START": "1",
    "VISITOR_REQUIRE_STAFF_VERIFICATION": "0", "CAMERA_SOURCES": '{"0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9", "10": "10", "11": "11", "12": "12", "13": "13", "14": "14", "15": "15", "test": "0", "preview-test": "0", "handoff": "0", "cam-1":"0", "sim-1":"0", "test-rtsp": "rtsp://test.invalid/live"}', "PUBLIC_REGISTRATION_ROLE": "operator",
})


def assert_test_path(path):
    resolved = Path(path).resolve()
    if resolved == TEST_ROOT or TEST_ROOT not in resolved.parents:
        raise RuntimeError("Refusing destructive test operation outside isolated runtime")
    return resolved


@pytest.fixture(autouse=True)
def reset_backend_state():
    import database
    import scenic_database
    from app.api import common
    from app.core.config import get_settings
    from app.services.job_manager import job_manager
    from app.services.payments import FAKE_WECHAT_ORDERS
    from app.services.vector_store import scenic_vector_store, vector_store

    for engine in (database.engine, scenic_database.engine):
        if engine.url.get_backend_name() != "sqlite" or not engine.url.database:
            raise RuntimeError("Tests may only reset isolated SQLite databases")
        assert_test_path(engine.url.database)
    assert_test_path(get_settings().object_storage_dir)
    assert_test_path(common.BASE_GALLERY_DIR)
    database.Base.metadata.drop_all(bind=database.engine)
    with database.engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    database.init_database()
    scenic_database.Base.metadata.drop_all(bind=scenic_database.engine)
    with scenic_database.engine.begin() as connection:
        # This legacy-only table is still present in the shared migration
        # history but intentionally has no scenic ORM model.
        connection.execute(text("DROP TABLE IF EXISTS search_histories"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    scenic_database.init_database()

    common.ACTIVE_SESSIONS.clear()
    common.GUEST_USAGE.clear()
    Path(common.BASE_GALLERY_DIR).mkdir(parents=True, exist_ok=True)

    storage_root = get_settings().object_storage_dir
    shutil.rmtree(assert_test_path(storage_root), ignore_errors=True)
    storage_root.mkdir(parents=True, exist_ok=True)

    manager = job_manager
    manager._redis_retry_after = float("inf")
    manager._redis_unavailable = True
    manager._local_records.clear()
    with manager._local_lock:
        manager._local_enqueued_total = 0
        manager._local_running = 0
        manager._local_completed_total = 0
        manager._local_failed_total = 0

    if hasattr(vector_store, "_points"):
        with vector_store._lock:
            vector_store._points = []
            vector_store.path.parent.mkdir(parents=True, exist_ok=True)
            vector_store.path.unlink(missing_ok=True)
    if hasattr(scenic_vector_store, "_points"):
        with scenic_vector_store._lock:
            scenic_vector_store._points = []
            scenic_vector_store.path.parent.mkdir(parents=True, exist_ok=True)
            scenic_vector_store.path.unlink(missing_ok=True)
    FAKE_WECHAT_ORDERS.clear()

    yield
    # Complete local tasks before the next test drops its database tables.
    from concurrent.futures import ThreadPoolExecutor
    manager._local_executor.shutdown(wait=True)
    manager._local_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="myreid-test-job")


@pytest.fixture
def client():
    import main

    return TestClient(main.app)


@pytest.fixture
def register_user(client):
    sequence = count(1)

    def _register(username=None, email=None, password="test-password"):
        index = next(sequence)
        username = username or f"user{index}"
        email = email or f"{username}@example.com"
        code_response = client.post(
            "/api/v1/auth/email-code/request",
            json={"username": username, "email": email, "purpose": "register"},
        )
        assert code_response.status_code == 200, code_response.text
        code = code_response.json()["debug_code"]
        register_response = client.post(
            "/api/v1/auth/register",
            json={"username": username, "email": email, "password": password, "code": code},
        )
        assert register_response.status_code == 200, register_response.text
        payload = register_response.json()
        return {
            "username": username,
            "email": email,
            "password": password,
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "headers": {"Authorization": f"Bearer {payload['access_token']}"},
        }

    return _register


@pytest.fixture
def register_scenic_user(client):
    sequence = count(1)

    def _register(username=None, email=None, password="test-password"):
        index = next(sequence)
        username = username or f"scenic_user{index}"
        email = email or f"{username}@example.com"
        code_response = client.post(
            "/api/v1/scenic/auth/email-code/request",
            json={"username": username, "email": email, "purpose": "register"},
        )
        assert code_response.status_code == 200, code_response.text
        code = code_response.json()["debug_code"]
        register_response = client.post(
            "/api/v1/scenic/auth/register",
            json={"username": username, "email": email, "password": password, "code": code},
        )
        assert register_response.status_code == 200, register_response.text
        payload = register_response.json()
        return {
            "username": username,
            "email": email,
            "password": password,
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "headers": {"Authorization": f"Bearer {payload['access_token']}"},
        }

    return _register
