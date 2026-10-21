"""Render admission failures remain retryable and duplicate jobs stay harmless."""

from contextlib import nullcontext

import pytest
from filelock import FileLock, Timeout

from test_scenic_editing_api import auto, seed


def _queued_variant(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services.job_manager import job_manager

    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, _, tourist = seed(client, register_scenic_user)
    draft = auto(client, user, tourist)
    response = client.post(f"/api/v1/scenic/tourists/{tourist}/vlog", headers=user["headers"],
        json={"draft_id": draft["id"], "revision": 1})
    assert response.status_code == 200, response.text
    vlog_id = response.json()["vlog"]["id"]
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogJob, vlog_id).variants[0]
        return user, vlog_id, variant.id, variant.processing_job_id


@pytest.mark.parametrize("failure", ["busy", "permission"])
def test_render_slot_failure_marks_only_waiting_variant_failed_and_allows_retry(
    client, register_scenic_user, monkeypatch, failure,
):
    import scenic_database as database
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    user, vlog_id, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    with database.SessionLocal() as db:
        companion = db.query(database.ScenicVlogVariant).filter(
            database.ScenicVlogVariant.vlog_job_id == vlog_id,
            database.ScenicVlogVariant.id != variant_id,
        ).first()
        companion.status = "succeeded"
        companion.full_object_key = "already-rendered.mp4"
        companion_id = companion.id
        db.commit()

    render_path = scenic_editing.lock_path("render")

    def lock_factory(path, *, timeout):
        if path == render_path:
            if failure == "permission":
                raise PermissionError("render lock is not writable")
            return FileLock(path, timeout=0)
        return FileLock(path, timeout=timeout)

    # A real occupied OS lock reproduces admission failure without a six-hour wait.
    held_lock = FileLock(render_path, timeout=0) if failure == "busy" else nullcontext()
    with held_lock, monkeypatch.context() as patch:
        patch.setattr(scenic_editing, "FileLock", lock_factory)
        with pytest.raises(Timeout if failure == "busy" else PermissionError):
            scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])

    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogVariant, variant_id)
        assert current.status == "failed" and current.error
        assert db.get(database.ProcessingJob, job_id).status == "failed"
        assert db.get(database.ScenicVlogJob, vlog_id).status == "partial"
        companion = db.get(database.ScenicVlogVariant, companion_id)
        assert companion.status == "succeeded"
        assert companion.full_object_key == "already-rendered.mp4"
    assert job_manager.get(job_id).status == "failed"
    retried = client.post(f"/api/v1/scenic/vlogs/{vlog_id}/variants/{variant_id}/retry", headers=user["headers"])
    assert retried.status_code == 200, retried.text
    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogVariant, variant_id)
        assert current.status == "queued"
        assert current.processing_job_id != job_id


@pytest.mark.parametrize("status", ["queued", "running"])
def test_busy_variant_execution_is_a_noop_for_duplicate_delivery(
    client, register_scenic_user, monkeypatch, status,
):
    import scenic_database as database
    from app.services import scenic_render
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    user, _, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        variant.status, variant.progress, variant.message = status, .4, "original execution"
        db.get(database.ProcessingJob, job_id).status = status
        db.commit()
    job_manager.update(job_id, status=status, progress=.4, message="original execution")
    monkeypatch.setattr(scenic_render, "render_variant", lambda *args: pytest.fail("duplicate job rendered"))
    with FileLock(scenic_editing.execution_lock_path(variant_id, job_id), timeout=0):
        scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogVariant, variant_id)
        assert (current.status, current.progress, current.message, current.error) == (status, .4, "original execution", "")
        assert db.get(database.ProcessingJob, job_id).status == status
    assert job_manager.get(job_id).status == status


def test_retry_enters_render_while_failed_job_still_holds_its_execution_lock(
    client, register_scenic_user, monkeypatch,
):
    import scenic_database as database
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    user, vlog_id, variant_id, old_job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    rendered = []
    old_lock = FileLock(scenic_editing.execution_lock_path(variant_id, old_job_id), timeout=0)

    def render_retry(job_id, target_variant_id, username):
        # TestClient runs the API on another thread; inspect the OS lock rather
        # than FileLock.is_locked, whose state is thread-local by default.
        with pytest.raises(Timeout), FileLock(old_lock.lock_file, timeout=0):
            pass
        rendered.append((job_id, target_variant_id, username))
        scenic_editing.update_progress(target_variant_id, job_id, .02, "retry entered render")

    def enqueue(job_id, task_path, *args):
        assert task_path == "app.workers.scenic_editing.render_scenic_variant"
        assert args[0] == job_id
        scenic_editing.render_scenic_variant(*args)

    monkeypatch.setattr(scenic_editing, "_render_scenic_variant", render_retry)
    monkeypatch.setattr(job_manager, "enqueue", enqueue)
    # Reproduce the interval after failure has been published but before the
    # old worker's finally releases its execution lock. The retry uses the API.
    with old_lock:
        scenic_editing.update_progress(variant_id, old_job_id, 1, "failed before releasing lock",
            status="failed", error="first render failed")
        retried = client.post(f"/api/v1/scenic/vlogs/{vlog_id}/variants/{variant_id}/retry", headers=user["headers"])
        assert retried.status_code == 200, retried.text
        with database.SessionLocal() as db:
            current = db.get(database.ScenicVlogVariant, variant_id)
            new_job_id = current.processing_job_id
            assert new_job_id != old_job_id
            assert current.status == "running"
            assert current.message == "retry entered render" and not current.error
            assert db.get(database.ProcessingJob, new_job_id).status == "running"
            assert db.get(database.ProcessingJob, old_job_id).status == "failed"
        assert rendered == [(new_job_id, variant_id, user["username"])]
        assert job_manager.get(new_job_id).status == "running"


def test_lock_failure_from_replaced_job_cannot_fail_retry(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.workers import scenic_editing

    user, _, variant_id, old_job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    with database.SessionLocal() as db:
        variant = db.get(database.ScenicVlogVariant, variant_id)
        db.add(database.ProcessingJob(id="replacement-job", project_id=db.get(database.ProcessingJob, old_job_id).project_id,
            job_type="scenic_vlog_variant", status="queued"))
        db.flush()
        variant.processing_job_id = "replacement-job"
        db.commit()
    render_path = scenic_editing.lock_path("render")

    def lock_factory(path, *, timeout):
        return FileLock(path, timeout=0 if path == render_path else timeout)

    with FileLock(render_path, timeout=0), monkeypatch.context() as patch:
        patch.setattr(scenic_editing, "FileLock", lock_factory)
        with pytest.raises(Timeout):
            scenic_editing.render_scenic_variant(old_job_id, variant_id, user["username"])
    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogVariant, variant_id)
        assert current.status == "queued" and not current.error
        assert db.get(database.ProcessingJob, "replacement-job").status == "queued"


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("failure", ["directory", "construction", "acquisition"])
def test_variant_lock_errors_preserve_original_reason_and_do_not_fail_running_execution(
    client, register_scenic_user, monkeypatch, status, failure,
):
    import scenic_database as database
    from app.services.job_manager import job_manager
    from app.workers import scenic_editing

    user, _, variant_id, job_id = _queued_variant(client, register_scenic_user, monkeypatch)
    with database.SessionLocal() as db:
        db.get(database.ScenicVlogVariant, variant_id).status = status
        db.get(database.ProcessingJob, job_id).status = status
        db.commit()
    job_manager.update(job_id, status=status)
    original_lock_path = scenic_editing.lock_path
    original_reason = f"variant lock {failure} unavailable"

    class InaccessibleLock:
        def acquire(self):
            raise PermissionError(original_reason)

    def lock_path(name):
        if failure == "directory" and name.startswith("variant-"):
            raise PermissionError(original_reason)
        return original_lock_path(name)

    def lock_factory(path, *, timeout):
        if failure == "construction":
            raise PermissionError(original_reason)
        return InaccessibleLock()

    def inaccessible_parent(*args):
        raise PermissionError("parent aggregation also unavailable")

    monkeypatch.setattr(scenic_editing, "lock_path", lock_path)
    monkeypatch.setattr(scenic_editing, "FileLock", lock_factory)
    monkeypatch.setattr(scenic_editing, "refresh_parent", inaccessible_parent)
    with pytest.raises(PermissionError, match=original_reason):
        scenic_editing.render_scenic_variant(job_id, variant_id, user["username"])
    expected = "failed" if status == "queued" else "running"
    with database.SessionLocal() as db:
        current = db.get(database.ScenicVlogVariant, variant_id)
        assert current.status == expected
        assert current.error == (original_reason if status == "queued" else "")
        assert db.get(database.ProcessingJob, job_id).status == expected
    assert job_manager.get(job_id).status == expected
