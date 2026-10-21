"""Terminal jobs remain immutable and original render errors stay visible."""

import pytest
from filelock import FileLock

from test_scenic_editing_regressions import _queued_variant


@pytest.mark.parametrize("status", ["failed", "canceled", "succeeded"])
def test_terminal_delivery_does_not_restart_or_change_assets(client, register_scenic_user, monkeypatch, status):
    import scenic_database as database
    from app.services import scenic_render
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    user, _, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        variant.status, variant.error, variant.message = status, "preserved reason", "preserved state"
        variant.full_object_key = "preserved.mp4"
        db.get(database.ProcessingJob, job_id).status = status
        db.commit()
    job_manager.update(job_id, status=status)
    monkeypatch.setattr(scenic_render, "render_variant", lambda *args: pytest.fail("terminal delivery rendered"))
    scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        assert (variant.status, variant.error, variant.message, variant.full_object_key) == (
            status, "preserved reason", "preserved state", "preserved.mp4")
        assert db.get(database.ProcessingJob, job_id).status == status
    assert job_manager.get(job_id).status == status


def test_parent_failure_does_not_mask_codec_error_and_releases_locks(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services import scenic_render
    from app.workers import scenic_editing

    user, vlog_id, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    original_refresh = scenic_editing.refresh_parent
    rendering_failed = False

    def render(*args):
        nonlocal rendering_failed
        rendering_failed = True
        raise scenic_render.ScenicRenderError("source decode failed")

    def refresh(db, identifier):
        if rendering_failed:
            raise PermissionError("parent lock unavailable")
        original_refresh(db, identifier)

    monkeypatch.setattr(scenic_render, "render_variant", render)
    monkeypatch.setattr(scenic_editing, "refresh_parent", refresh)
    with pytest.raises(scenic_render.ScenicRenderError, match="source decode failed"):
        scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        assert variant.status == "failed" and variant.error == "source decode failed"
        assert db.get(database.ProcessingJob, job_id).error == "source decode failed"
        companion = db.query(database.ScenicVlogVariant).filter(
            database.ScenicVlogVariant.vlog_job_id == vlog_id, database.ScenicVlogVariant.id != variant_id).first()
        assert companion.status == "queued" and not companion.error
    with FileLock(scenic_editing.lock_path("render"), timeout=0):
        pass
    with FileLock(scenic_editing.execution_lock_path(variant_id, job_id), timeout=0):
        pass
