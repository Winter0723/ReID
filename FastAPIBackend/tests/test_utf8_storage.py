from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import Mock

import pytest
from botocore.handlers import validate_ascii_metadata
from PIL import Image

from app.services.storage import S3ObjectStorage


@pytest.fixture
def s3_storage(tmp_path):
    store = S3ObjectStorage.__new__(S3ObjectStorage)
    store.bucket = "test"
    store.cache_dir = tmp_path / "cache"
    store.staging_dir = tmp_path / "staging"
    store.client = Mock()
    objects = {}

    def upload(body, bucket, key, ExtraArgs):
        # Exercise the SDK validator that rejected the original Chinese name.
        validate_ascii_metadata(ExtraArgs)
        objects[key] = body.read()

    store.client.upload_fileobj.side_effect = upload
    store.client.download_file.side_effect = lambda bucket, key, path: Path(path).write_bytes(objects[key])
    store.client.generate_presigned_url.return_value = "https://storage.example.test/image"
    return store


@pytest.mark.parametrize("filename", [
    "微信图片_20260622173239_8_67-min.jpg", "游客 🏞️ 100%.jpg", "photo.jpg",
])
def test_s3_filename_round_trip_preserves_utf8(s3_storage, filename):
    stored = s3_storage.save_upload(1, filename, BytesIO(b"photo"))
    metadata = s3_storage.client.upload_fileobj.call_args.kwargs["ExtraArgs"]["Metadata"]
    assert unquote(metadata["filename"], encoding="utf-8", errors="strict") == filename
    assert metadata["filename-encoding"] == "utf-8-percent"
    assert stored.filename == filename
    assert stored.object_key.endswith(filename)
    assert s3_storage.path_for(stored.object_key).read_bytes() == b"photo"
    assert list(s3_storage.staging_dir.iterdir()) == []


def test_scenic_registration_accepts_chinese_photo_in_s3(client, register_scenic_user, monkeypatch, s3_storage):
    from app.api.scenic import tourist_routes
    import scenic_database as database

    monkeypatch.setattr(tourist_routes, "storage", s3_storage)
    user = register_scenic_user()
    project = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": "中文景区"})
    assert project.status_code == 200, project.text
    photo = BytesIO()
    Image.new("RGB", (20, 30)).save(photo, format="JPEG")
    filename = "微信图片_20260622173239_8_67-min.jpg"
    response = client.post(
        f"/api/v1/scenic/projects/{project.json()['id']}/tourists", headers=user["headers"],
        data={"consent_granted": "true", "visitor_label": "游客甲"},
        files={"file": (filename, photo.getvalue(), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    with database.SessionLocal() as db:
        session = db.query(database.TouristSession).one()
        assert session.entry_image_filename == filename
        assert session.visitor_label == "游客甲"


def test_entry_photo_detection_reads_unicode_path_without_opencv_file_io(tmp_path, monkeypatch):
    from app.api.scenic import system_routes

    path = tmp_path / "微信入园照片.png"
    Image.new("RGB", (30, 50), color=(200, 100, 20)).save(path)
    monkeypatch.setattr(system_routes, "storage", SimpleNamespace(path_for=lambda key: path))
    monkeypatch.setattr(system_routes.cv2, "imread", Mock(side_effect=AssertionError("Use the verified RGB image")))
    detect = Mock(return_value=[])
    feature = Mock(return_value="feature")
    monkeypatch.setattr(system_routes, "detect_people", detect)
    monkeypatch.setattr(system_routes, "clip_image_feature", feature)
    assert system_routes._tourist_entry_feature(SimpleNamespace(
        entry_image_object_key="entry", entry_target_box_json="")) == "feature"
    frame = detect.call_args.args[0]
    assert frame.shape == (50, 30, 3)
    assert frame[0, 0].tolist() == [20, 100, 200]
