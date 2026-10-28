from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from botocore.exceptions import ClientError
import pytest

from app.services import asset_delivery


def test_local_preview_in_s3_mode_supports_seeking(monkeypatch, tmp_path):
    key = "projects/1/media/2/previews/clip.mp4"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"0123456789")
    monkeypatch.setattr(asset_delivery, "get_settings", lambda: SimpleNamespace(object_storage_dir=tmp_path))
    store = Mock()
    monkeypatch.setattr(asset_delivery, "storage", store)
    app = FastAPI()

    @app.get("/preview")
    def preview():
        return asset_delivery.deliver_video_preview(key)

    with TestClient(app) as client:
        response = client.get("/preview", headers={"Range": "bytes=3-6"})
        assert response.status_code == 206
        assert response.content == b"3456"
        assert response.headers["content-type"] == "video/mp4"
    store.path_for.assert_not_called()


def test_s3_media_stays_on_api_and_supports_ranges(monkeypatch, tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0123456789")
    storage = Mock()
    storage.path_for.return_value = media
    monkeypatch.setattr(asset_delivery, "storage", storage)
    monkeypatch.setattr(asset_delivery, "get_settings", lambda: SimpleNamespace(object_storage_mode="s3"))
    app = FastAPI()

    @app.get("/media")
    def content():
        return asset_delivery.deliver_object("projects/1/video.mp4", content_type="video/mp4")

    with TestClient(app) as client:
        response = client.get("/media", follow_redirects=False)
        assert response.status_code == 200
        assert response.content == b"0123456789"
        assert response.headers["cache-control"] == "private, no-store"
        assert "location" not in response.headers
        partial = client.get("/media", headers={"Range": "bytes=2-5"})
        assert partial.status_code == 206
        assert partial.content == b"2345"
    storage.signed_url_for.assert_not_called()


@pytest.mark.parametrize("missing", [
    ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"),
    FileNotFoundError("素材文件未上传或已移除"),
])
def test_missing_s3_media_returns_404(monkeypatch, missing):
    storage = Mock()
    storage.path_for.side_effect = missing
    monkeypatch.setattr(asset_delivery, "storage", storage)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asset_delivery.deliver_object("projects/1/missing.jpg")
    assert exc.value.status_code == 404


def test_live_camera_response_uses_same_snapshot_for_body_and_length(monkeypatch, tmp_path):
    from app.api.app_factory import CameraGalleryFiles
    image = tmp_path / 'latest.jpg'
    image.write_bytes(b'old-image')
    from app.services.asset_delivery import signed_asset_url
    gallery = CameraGalleryFiles(directory=tmp_path)
    original = gallery.lookup_path
    def replaced_after_stat(path):
        result = original(path)
        replacement = tmp_path / 'next.jpg'
        replacement.write_bytes(b'new-complete-image-with-a-different-size')
        replacement.replace(image)
        return result
    monkeypatch.setattr(gallery, 'lookup_path', replaced_after_stat)
    app = FastAPI(); app.mount('/gallery', gallery)
    with TestClient(app) as client:
        response = client.get(signed_asset_url('/gallery/latest.jpg'), headers={'If-None-Match': '*', 'If-Modified-Since': 'Wed, 31 Dec 2099 23:59:59 GMT'})
        assert response.status_code == 200
        assert response.content == image.read_bytes()
        assert int(response.headers['content-length']) == len(response.content)
        assert response.headers['cache-control'] == 'no-store'
        head = client.head(signed_asset_url('/gallery/latest.jpg'))
        assert head.status_code == 200 and not head.content
        assert int(head.headers['content-length']) == image.stat().st_size
        assert client.post(signed_asset_url('/gallery/latest.jpg')).status_code == 405
        assert client.get(signed_asset_url('/gallery/missing/latest.jpg')).status_code == 404


def test_concurrent_preview_requests_wait_for_complete_atomic_output(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from pathlib import Path
    from app.core.config import get_settings
    from app.services import media_service
    source=tmp_path/'source.avi';source.write_bytes(b'input')
    store=Mock();store.path_for.return_value=source;store.url_for.side_effect=lambda key:key
    monkeypatch.setattr(media_service,'storage',store)
    monkeypatch.setattr(media_service,'ffmpeg_executable',lambda:'ffmpeg')
    monkeypatch.setattr(media_service,'_video_preview_encoder_arg_sets',lambda exe:[[]])
    started,complete=Event(),Event()
    outputs=[]
    def generate(cmd,**kwargs):
        output=Path(cmd[-1]);outputs.append(output)
        output.write_bytes(b'partial');started.set()
        assert complete.wait(5)
        output.write_bytes(b'complete-video')
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr(media_service.subprocess,'run',generate)
    media=SimpleNamespace(id=1,object_key='source.avi',media_type='camera',content_type='video/x-msvideo',size_bytes=5,filename='source.avi')
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(media_service.video_preview_payload,media,'scenic/1')
        assert started.wait(5)
        second=pool.submit(media_service.video_preview_payload,media,'scenic/1')
        try:
            with pytest.raises(TimeoutError):second.result(timeout=.1)
            assert outputs[0].name.startswith('.')
        finally:
            complete.set()
        a,b=first.result(),second.result()
    assert a['preview_object_key']==b['preview_object_key']
    assert len(outputs)==1
    assert (get_settings().object_storage_dir/a['preview_object_key']).read_bytes()==b'complete-video'
