import copy
import io
import json
from pathlib import Path

import pytest
from PIL import Image


def seed(client, register_scenic_user):
    import scenic_database as database
    from app.services.storage import storage
    user = register_scenic_user()
    project = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": "游览公园"}).json()["id"]
    image = io.BytesIO()
    Image.new("RGB", (320, 480), (55, 125, 90)).save(image, format="JPEG")
    response = client.post(f"/api/v1/scenic/projects/{project}/tourists", headers=user["headers"],
        data={"consent_granted": "true", "visitor_label": "游客 <测试>"}, files={"file": ("entry.jpg", image.getvalue(), "image/jpeg")})
    assert response.status_code == 200, response.text
    tourist = response.json()["id"]
    with database.SessionLocal() as db:
        for index in range(3):
            content = io.BytesIO()
            Image.new("RGB", (640, 360), (30 + index * 55, 80 + index * 20, 120)).save(content, format="JPEG")
            stored = storage.save_upload(f"scenic/{project}", f"photo-{index}.jpg", io.BytesIO(content.getvalue()))
            media = database.MediaFile(project_id=project, uploader_username=user["username"], filename=stored.filename,
                object_key=stored.object_key, media_type="image", status="indexed", content_type="image/jpeg")
            db.add(media)
            db.flush()
            db.add(database.TouristVisitSegment(session_id=tourist, project_id=project, media_id=media.id,
                start_seconds=0, end_seconds=6, original_media_object_key=stored.object_key,
                thumbnail_object_key=stored.object_key, camera_name=f"点位{index}", included=True, sort_order=index))
        db.commit()
    return user, project, tourist


def auto(client, user, tourist):
    response = client.post(f"/api/v1/scenic/tourists/{tourist}/drafts/auto", headers=user["headers"], json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_draft_source_authority_revision_and_snapshot(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services.job_manager import job_manager
    queued = []
    monkeypatch.setattr(job_manager, "enqueue", lambda *args: queued.append(args))
    user, project, tourist = seed(client, register_scenic_user)
    draft = auto(client, user, tourist)
    assert draft["data"]["music_id"] == "oga_revelation"
    original_key = draft["data"]["variants"]["portrait"][0]["object_key"]
    edited = copy.deepcopy(draft["data"])
    edited["title"] = "确定的版本"
    edited["variants"]["portrait"][0]["object_key"] = "../../private.txt"
    edited["sources"][0]["source_sha256"] = "forged"
    path = f"/api/v1/scenic/tourists/{tourist}/drafts/{draft['id']}"
    result = client.put(path, headers=user["headers"], json={"revision": 1, "data": edited})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["variants"]["portrait"][0]["object_key"] == original_key
    assert client.put(path, headers=user["headers"], json={"revision": 1, "data": edited}).status_code == 409
    submit_url = f"/api/v1/scenic/tourists/{tourist}/vlog"
    submitted = client.post(submit_url, headers=user["headers"], json={"draft_id": draft["id"], "revision": 2})
    assert submitted.status_code == 200, submitted.text
    vlog = submitted.json()["vlog"]
    assert len(vlog["variants"]) == 2
    assert len(queued) == 2
    duplicate = client.post(submit_url, headers=user["headers"], json={"draft_id": draft["id"], "revision": 2})
    assert duplicate.json()["vlog"]["id"] == vlog["id"]
    assert len(queued) == 2
    changed = result.json()["data"]
    changed["title"] = "后续编辑"
    assert client.put(path, headers=user["headers"], json={"revision": 2, "data": changed}).status_code == 200
    with database.SessionLocal() as db:
        db.query(database.TouristVisitSegment).filter_by(session_id=tourist).delete()
        db.commit()
        frozen = json.loads(db.get(database.ScenicVlogJob, vlog["id"]).snapshot_json)
        assert frozen["title"] == "确定的版本"
        assert frozen["music_sha256"]
    incomplete = client.post(f"/api/v1/scenic/tourists/{tourist}/orders", headers=user["headers"], json={"vlog_job_id": vlog["id"]})
    assert incomplete.status_code == 400


def test_variant_retry_and_delivery_are_bound_to_paid_version(client, register_scenic_user, monkeypatch, tmp_path):
    import scenic_database as database
    from app.services.job_manager import job_manager
    from app.services import scenic_render
    from app.workers.scenic_editing import render_scenic_variant
    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, _, tourist = seed(client, register_scenic_user)
    draft = auto(client, user, tourist)
    vlog = client.post(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 1}).json()["vlog"]
    failed = {"portrait"}
    def fake_render(snapshot, orientation, output_dir, progress):
        if orientation in failed:
            raise RuntimeError("encoder unavailable")
        result = {"duration_seconds": 12.0}
        for asset in ("full", "preview", "draft_preview", "cover"):
            path = output_dir / f"{asset}.{'jpg' if asset == 'cover' else 'mp4'}"
            path.write_bytes(f"{orientation}:{asset}".encode())
            result[f"{asset}_path"] = path
        progress(0.5, "测试渲染")
        return result
    monkeypatch.setattr(scenic_render, "render_variant", fake_render)
    with database.SessionLocal() as db:
        variants = [(v.id, v.processing_job_id, v.orientation) for v in db.get(database.ScenicVlogJob, vlog["id"]).variants]
    for variant_id, job_id, orientation in variants:
        if orientation in failed:
            with pytest.raises(RuntimeError, match="encoder unavailable"):
                render_scenic_variant(job_id, variant_id, user["username"])
        else:
            render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogJob, vlog["id"])
        assert current.status == "partial"
        original_landscape = current.full_object_key
    portrait_id = next(v[0] for v in variants if v[2] == "portrait")
    retried = client.post(f"/api/v1/scenic/vlogs/{vlog['id']}/variants/{portrait_id}/retry", headers=user["headers"])
    assert retried.status_code == 200, retried.text
    failed.clear()
    with database.SessionLocal() as db:
        new_job = db.get(database.ScenicVlogVariant, portrait_id).processing_job_id
    render_scenic_variant(new_job, portrait_id, user["username"])
    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogJob, vlog["id"])
        assert current.status == "succeeded"
        assert current.full_object_key == original_landscape
    order_response = client.post(f"/api/v1/scenic/tourists/{tourist}/orders", headers=user["headers"], json={"vlog_job_id": vlog["id"]})
    assert order_response.status_code == 200, order_response.text
    order = order_response.json()
    prefix = f"/api/v1/scenic/deliveries/{order['delivery_token']}/variants/{portrait_id}"
    assert client.get(prefix + "/full").status_code == 402
    assert client.get(prefix + "/preview").status_code == 200
    assert client.get(prefix + "/draft-preview").status_code == 404
    marked = client.post(f"/api/v1/scenic/orders/{order['id']}/mark-paid", headers=user["headers"])
    assert marked.status_code == 200, marked.text
    assert client.get(prefix + "/full").content == b"portrait:full"
    assert client.get(prefix.replace(str(portrait_id), "999999") + "/full").status_code == 404
    page = client.get(order["delivery_page_url"])
    assert page.status_code == 200 and "&lt;测试&gt;" in page.text and "<测试>" not in page.text
    assert client.get(order["delivery_qr_url"]).headers["content-type"] == "image/png"


def test_source_change_and_foreign_draft_fail_closed(client, register_scenic_user):
    from app.services.storage import storage
    user, _, tourist = seed(client, register_scenic_user)
    draft = auto(client, user, tourist)
    other = register_scenic_user("outsider", "outsider@example.com")
    assert client.get(f"/api/v1/scenic/tourists/{tourist}/drafts", headers=other["headers"]).status_code == 403
    source = draft["data"]["variants"]["portrait"][0]
    storage.path_for(source["object_key"]).write_bytes(b"changed")
    result = client.post(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 1})
    assert result.status_code == 422


def test_checkout_reference_metadata_and_feature_flag(client, register_scenic_user, monkeypatch):
    from app.core.config import get_settings
    user, project, tourist = seed(client, register_scenic_user)
    path = f"/api/v1/scenic/tourists/{tourist}"
    checked = client.post(path + "/checkout", headers=user["headers"])
    assert checked.status_code == 200
    assert checked.json()["checked_out_at"] == client.post(path + "/checkout", headers=user["headers"]).json()["checked_out_at"]
    cropped = client.patch(path + "/reference", headers=user["headers"], json={"entry_target_box": [0.1, 0.1, 0.9, 0.9]})
    assert cropped.status_code == 200
    media_id = cropped.json()["segments"][0]["media_id"]
    metadata = f"/api/v1/scenic/projects/{project}/media/{media_id}/editing-metadata"
    assert client.patch(metadata, headers=user["headers"], json={"recorded_at": "2026-09-07T10:00:00"}).status_code == 422
    assert client.patch(metadata, headers=user["headers"], json={"recorded_at": "2026-09-07T10:00:00+08:00", "location": "湖畔"}).status_code == 200
    assert client.get(metadata, headers=user["headers"]).json()["location"] == "湖畔"
    monkeypatch.setattr(get_settings(), "scenic_editing_enabled", False)
    assert client.post(path + "/drafts/auto", headers=user["headers"], json={}).status_code == 409
