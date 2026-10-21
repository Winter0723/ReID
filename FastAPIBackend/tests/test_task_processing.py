from pathlib import Path
import os
import uuid

import pytest
import torch
from PIL import Image


def _test_runtime_path(filename: str) -> Path:
    root = Path(os.environ["MYREID_TEST_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


def test_image_index_task_persists_job_media_target_and_vector(monkeypatch):
    import database
    from app.api import common as main
    from app.workers import tasks

    monkeypatch.setattr(tasks, "clip_image_feature", lambda image: torch.tensor([[1.0, 0.0]], dtype=torch.float32))

    source_path = _test_runtime_path("myreid_task_source.jpg")
    Image.new("RGB", (64, 128), "blue").save(source_path)

    db = database.SessionLocal()
    try:
        user = database.User(username="worker_user", email="worker@example.com", hashed_password=main.get_password_hash("password"), role="admin")
        db.add(user)
        db.flush()
        project = database.Project(name="Worker Project", description="", owner_username=user.username)
        db.add(project)
        db.flush()
        db.add(database.ProjectMember(project_id=project.id, username=user.username, role="owner"))

        stored = main.storage.save_path(project.id, source_path, "source.jpg", category="originals")
        media = database.MediaFile(
            project_id=project.id,
            uploader_username=user.username,
            filename=stored.filename,
            object_key=stored.object_key,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            media_type="image",
            status="uploaded",
        )
        db.add(media)
        db.flush()

        job = main.job_manager.create("media_index", "queued")
        db.add(
            database.ProcessingJob(
                id=job.id,
                project_id=project.id,
                media_id=media.id,
                job_type=job.type,
                status=job.status,
                progress=job.progress,
                message=job.message,
            )
        )
        db.commit()
        project_id = project.id
        media_id = media.id
        job_id = job.id
    finally:
        db.close()

    tasks.process_media_index(job_id, media_id, project_id, stored.object_key, "image", "worker_user")

    db = database.SessionLocal()
    try:
        media = db.query(database.MediaFile).filter(database.MediaFile.id == media_id).one()
        target_count = db.query(database.DetectionTarget).filter(database.DetectionTarget.media_id == media_id).count()
        persisted_job = db.query(database.ProcessingJob).filter(database.ProcessingJob.id == job_id).one()
        assert media.status == "indexed"
        assert target_count == 1
        assert persisted_job.status == "succeeded"
        assert persisted_job.progress == 1.0
    finally:
        db.close()

    results = main.vector_store.search(torch.tensor([1.0, 0.0]), top_k=5, project_id=project_id)
    assert len(results) == 1
    assert results[0].metadata["media_id"] == media_id


def test_media_index_failure_rolls_back_targets_vectors_and_crops(monkeypatch):
    import database
    from app.api import common as main
    from app.workers import tasks

    monkeypatch.setattr(
        tasks,
        "clip_image_feature",
        lambda image: torch.tensor([[1.0, 0.0]], dtype=torch.float32),
    )
    monkeypatch.setattr(
        main.vector_store,
        "upsert",
        lambda points: (_ for _ in ()).throw(RuntimeError("vector backend unavailable")),
    )

    source_path = _test_runtime_path("myreid_task_failure_source.jpg")
    Image.new("RGB", (64, 128), "red").save(source_path)

    db = database.SessionLocal()
    try:
        user = database.User(
            username="worker_failure_user",
            email="worker-failure@example.com",
            hashed_password=main.get_password_hash("password"),
            role="admin",
        )
        db.add(user)
        db.flush()
        project = database.Project(name="Worker Failure Project", description="", owner_username=user.username)
        db.add(project)
        db.flush()
        db.add(database.ProjectMember(project_id=project.id, username=user.username, role="owner"))
        stored = main.storage.save_path(project.id, source_path, "source.jpg", category="originals")
        media = database.MediaFile(
            project_id=project.id,
            uploader_username=user.username,
            filename=stored.filename,
            object_key=stored.object_key,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            media_type="image",
            status="uploaded",
        )
        db.add(media)
        db.flush()
        job_id = f"db-only-{uuid.uuid4()}"
        db.add(database.ProcessingJob(
            id=job_id,
            project_id=project.id,
            media_id=media.id,
            job_type="media_index",
            status="queued",
            progress=0.0,
            message="queued",
        ))
        db.commit()
        project_id = project.id
        media_id = media.id
    finally:
        db.close()

    with pytest.raises(RuntimeError, match="vector backend unavailable"):
        tasks.process_media_index(
            job_id,
            media_id,
            project_id,
            stored.object_key,
            "image",
            "worker_failure_user",
        )

    db = database.SessionLocal()
    try:
        media = db.query(database.MediaFile).filter(database.MediaFile.id == media_id).one()
        persisted_job = db.query(database.ProcessingJob).filter(database.ProcessingJob.id == job_id).one()
        assert media.status == "failed"
        assert persisted_job.status == "failed"
        assert "vector backend unavailable" in persisted_job.error
        assert db.query(database.DetectionTarget).filter(database.DetectionTarget.media_id == media_id).count() == 0
    finally:
        db.close()

    assert main.vector_store.search(torch.tensor([1.0, 0.0]), top_k=5, project_id=project_id) == []
    crop_dir = main.get_settings().object_storage_dir / f"projects/{project_id}/media/{media_id}/crops"
    assert not crop_dir.exists() or list(crop_dir.iterdir()) == []
