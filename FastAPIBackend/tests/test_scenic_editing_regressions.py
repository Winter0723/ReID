"""Cross-service regressions found during the independent editing review."""

import pytest

from test_scenic_editing_api import auto, seed


def _fake_render(snapshot, orientation, output_dir, progress):
    result = {"duration_seconds": 8.0}
    for asset in ("full", "preview", "draft_preview", "cover"):
        path = output_dir / f"{asset}.{'jpg' if asset == 'cover' else 'mp4'}"
        path.write_bytes(f"{orientation}:{asset}".encode())
        result[f"{asset}_path"] = path
    return result


def test_partial_metadata_patch_preserves_other_field(client, register_scenic_user):
    import scenic_database as database

    user, project, tourist = seed(client, register_scenic_user)
    with database.SessionLocal() as db:
        media_id = db.query(database.TouristVisitSegment).filter_by(session_id=tourist).first().media_id
    url = f"/api/v1/scenic/projects/{project}/media/{media_id}/editing-metadata"
    initial = client.patch(url, headers=user["headers"], json={"recorded_at": "2026-09-07T10:00:00+08:00", "location": "湖畔"})
    assert initial.status_code == 200, initial.text
    changed = client.patch(url, headers=user["headers"], json={"location": "观景台"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["recorded_at"] == initial.json()["recorded_at"]
    changed_time = client.patch(url, headers=user["headers"], json={"recorded_at": "2026-09-08T10:00:00+08:00"})
    assert changed_time.json()["location"] == "观景台"
    cleared = client.patch(url, headers=user["headers"], json={"recorded_at": None})
    assert cleared.json() == {"recorded_at": None, "location": "观景台"}


def test_cleanup_failure_does_not_leave_render_running(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services import scenic_render
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, _, tourist = seed(client, register_scenic_user)
    draft = auto(client, user, tourist)
    submit = client.post(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 1})
    assert submit.status_code == 200, submit.text
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogJob, submit.json()["vlog"]["id"]).variants[0]
        variant_id, job_id = variant.id, variant.processing_job_id
    monkeypatch.setattr(scenic_render, "render_variant", _fake_render)
    original_save = scenic_editing.storage.save_path
    uploads = []

    def save_until_failure(*args, **kwargs):
        if uploads:
            raise RuntimeError("upload interrupted")
        saved = original_save(*args, **kwargs)
        uploads.append(saved.object_key)
        return saved

    def delete_failure(key):
        raise RuntimeError("cleanup interrupted")

    monkeypatch.setattr(scenic_editing.storage, "save_path", save_until_failure)
    monkeypatch.setattr(scenic_editing.storage, "delete", delete_failure)
    with pytest.raises(RuntimeError):
        scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogVariant, variant_id)
        assert current.status == "failed"
        assert "upload interrupted" in current.error
        assert db.get(database.ProcessingJob, job_id).status == "failed"


def test_generated_defaults_can_be_submitted_for_long_valid_names(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services.job_manager import job_manager

    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, project, tourist = seed(client, register_scenic_user)
    with database.SessionLocal() as db:
        db.get(database.Project, project).name = "山水自然文化旅游风景区" * 6
        db.get(database.TouristSession, tourist).visitor_label = "游客留念" * 24
        db.commit()
    created = client.post(f"/api/v1/scenic/tourists/{tourist}/drafts/auto", headers=user["headers"], json={"title": ""})
    assert created.status_code == 200, created.text
    draft = created.json()
    submit = client.post(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 1})
    assert submit.status_code == 200, submit.text


def test_variant_staff_assets_require_access_and_path_bound_signature(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services import scenic_render
    from app.services.job_manager import job_manager
    from app.workers.scenic_editing import render_scenic_variant

    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    monkeypatch.setattr(scenic_render, "render_variant", _fake_render)
    user, _, tourist = seed(client, register_scenic_user)
    draft = auto(client, user, tourist)
    submit = client.post(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 1})
    vlog_id = submit.json()["vlog"]["id"]
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogJob, vlog_id).variants[0]
        variant_id, job_id = variant.id, variant.processing_job_id
    render_scenic_variant(job_id, variant_id, user["username"])
    outsider = register_scenic_user("asset_outsider", "asset_outsider@example.com")
    prefix = f"/api/v1/scenic/vlogs/{vlog_id}/variants/{variant_id}"
    assert client.get(prefix + "/full").status_code == 401
    assert client.get(prefix + "/full", headers=outsider["headers"]).status_code == 403
    assert client.get(prefix + "/full", headers=user["headers"]).status_code == 200
    response = client.get(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"])
    selected = next(variant for variant in response.json()["vlog_jobs"][0]["variants"] if variant["id"] == variant_id)
    assert client.get(selected["preview_url"]).status_code == 200
    assert client.get(selected["preview_url"].replace("/preview?", "/full?")).status_code == 401


def _queued_variant(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services.job_manager import job_manager

    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, _, tourist = seed(client, register_scenic_user)
    draft = auto(client, user, tourist)
    submit = client.post(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 1})
    assert submit.status_code == 200, submit.text
    vlog_id = submit.json()["vlog"]["id"]
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogJob, vlog_id).variants[0]
        return user, vlog_id, variant.id, variant.processing_job_id


def test_audit_failure_does_not_publish_or_leave_asset_references(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.api.scenic import service_adapters
    from app.services import scenic_render
    from app.workers import scenic_editing

    user, vlog_id, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    monkeypatch.setattr(scenic_render, "render_variant", _fake_render)
    uploaded = []
    original_save = scenic_editing.storage.save_path

    def observed_save(*args, **kwargs):
        result = original_save(*args, **kwargs)
        uploaded.append(result.object_key)
        return result

    def audit_failure(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(scenic_editing.storage, "save_path", observed_save)
    monkeypatch.setattr(service_adapters, "audit", audit_failure)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        assert variant.status == "failed"
        assert all(not getattr(variant, asset + "_object_key") for asset in ("full", "preview", "draft_preview", "cover"))
        parent = db.get(database.ScenicVlogJob, vlog_id)
        assert parent.status != "succeeded" and not parent.full_object_key
    assert len(uploaded) == 4
    assert all(not scenic_editing.storage.path_for(key).is_file() for key in uploaded)


def test_cleanup_after_publication_keeps_assets_and_updates_mirror_and_replay_is_idempotent(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services import scenic_render
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    user, _, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    calls = []

    def render(*args):
        calls.append(True)
        return _fake_render(*args)

    original_temporary = scenic_editing.tempfile.TemporaryDirectory

    class CleanupFailure:
        def __init__(self, *args, **kwargs):
            self.directory = original_temporary(*args, **kwargs)

        def __enter__(self):
            return self.directory.__enter__()

        def __exit__(self, *args):
            self.directory.__exit__(*args)
            raise PermissionError("temporary cleanup interrupted")

    monkeypatch.setattr(scenic_render, "render_variant", render)
    monkeypatch.setattr(scenic_editing.tempfile, "TemporaryDirectory", CleanupFailure)
    scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        assert variant.status == "succeeded"
        key = variant.full_object_key
        assert scenic_editing.storage.path_for(key).is_file()
        assert db.get(database.ProcessingJob, job_id).status == "succeeded"
    assert job_manager.get(job_id).status == "succeeded"
    scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    assert len(calls) == 1
    with database.SessionLocal() as db:
        assert db.get(database.ScenicVlogVariant, variant_id).full_object_key == key


@pytest.mark.parametrize("render_fails", [False, True])
def test_unavailable_progress_mirror_cannot_change_render_outcome(client, register_scenic_user, monkeypatch, render_fails):
    import scenic_database as database
    from app.services import scenic_render
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    user, vlog_id, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    with database.SessionLocal() as db:
        companion = db.query(database.ScenicVlogVariant).filter(database.ScenicVlogVariant.vlog_job_id == vlog_id, database.ScenicVlogVariant.id != variant_id).first()
        companion.status = "failed"
        companion.error = "other variant unavailable"
        db.commit()

    def unavailable_mirror(*args, **kwargs):
        raise ValueError("progress mirror unavailable")

    def render(*args):
        args[-1](.5, "镜头处理中")
        if render_fails:
            raise RuntimeError("real codec failure")
        return _fake_render(*args)

    monkeypatch.setattr(job_manager, "update", unavailable_mirror)
    monkeypatch.setattr(scenic_render, "render_variant", render)
    if render_fails:
        with pytest.raises(RuntimeError, match="real codec failure"):
            scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    else:
        scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        expected = "failed" if render_fails else "succeeded"
        assert variant.status == expected
        assert db.get(database.ProcessingJob, job_id).status == expected
        assert db.get(database.ScenicVlogJob, vlog_id).status == ("failed" if render_fails else "partial")
        if render_fails:
            assert variant.error == "real codec failure"
        else:
            assert scenic_editing.storage.path_for(variant.full_object_key).is_file()
    response = client.get(f"/api/v1/scenic/jobs/{job_id}", headers=user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == ("failed" if render_fails else "succeeded")
    if render_fails:
        assert response.json()["error"] == "real codec failure"
