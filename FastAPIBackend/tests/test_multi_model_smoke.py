"""Opt-in real checkpoint smoke: MYREID_RUN_MODEL_SMOKE=1 pytest ..."""
import os
import json
import shutil

import pytest
import torch
from PIL import Image, ImageDraw

pytestmark = [pytest.mark.real_model_registry, pytest.mark.skipif(
    os.environ.get("MYREID_RUN_MODEL_SMOKE") != "1", reason="requires local real checkpoints")]


def test_real_checkpoints_index_and_search_both_domains(tmp_path, monkeypatch):
    import database
    import scenic_database
    from app.services import model_runtime, project_models
    from app.services.model_registry import bind_project
    from app.core.config import get_settings
    from app.services import model_registry
    from app.services.storage import storage
    from app.services.vector_store import vector_store, scenic_vector_store
    from app.workers import tasks
    torch.set_num_threads(2)
    monkeypatch.setattr(model_runtime, "DEVICE", torch.device("cuda" if torch.cuda.is_available() else "cpu"))
    project_models._cache.clear()
    # Exercise a third compatible checkpoint with a different camera table and
    # content hash. It is a synthetic variant, not a claim about another dataset.
    source_models = model_registry.model_directory()
    isolated_models = tmp_path / "models"
    (isolated_models / "clip_weights").mkdir(parents=True)
    for relative in ("ViT-MSMT17-60.pth", "Market2MSMT.pth", "clip_weights/ViT-B-16.pt"):
        try:
            os.link(source_models / relative, isolated_models / relative)
        except OSError:
            shutil.copyfile(source_models / relative, isolated_models / relative)
    for relative in ("clipreid-msmt17.model.json", "mgddpm-market2msmt.model.json"):
        shutil.copyfile(source_models / relative, isolated_models / relative)
    import numpy as np
    from numpy.core.multiarray import scalar
    with torch.serialization.safe_globals([(scalar, "numpy.core.multiarray.scalar"), np.dtype,
                                           type(np.dtype("float64")), type(np.dtype("float32"))]):
        teacher = torch.load(source_models / "Market2MSMT.pth", map_location="cpu", weights_only=True, mmap=True)["teacher"]
    teacher = {key.removeprefix("module."): value for key, value in teacher.items()}
    teacher["cv_embed"] = teacher["cv_embed"][:6].clone() + .01
    torch.save({"teacher": teacher}, isolated_models / "six-cameras.pth")
    del teacher
    recipe = json.loads((source_models / "mgddpm-market2msmt.model.json").read_text())
    recipe.update(id="mgddpm-six-cameras", name="MGD-DPM synthetic six cameras", weights="six-cameras.pth")
    (isolated_models / "six-cameras.model.json").write_text(json.dumps(recipe))
    monkeypatch.setattr(get_settings(), "reid_model_dir", str(isolated_models))
    registered = model_registry.public_models()
    assert len(registered) == 3 and all(row["available"] for row in registered)
    assert len({row["embedding_space_id"] for row in registered}) == 3
    image = Image.new("RGB", (128, 256), (32, 40, 60))
    draw = ImageDraw.Draw(image)
    draw.ellipse((42, 10, 86, 55), fill=(200, 160, 130))
    draw.rectangle((30, 55, 98, 155), fill=(100, 20, 40))
    draw.rectangle((38, 155, 90, 250), fill=(20, 30, 80))
    source = tmp_path / "person-fixture.jpg"
    image.save(source)
    for dbm, scope, router, worker in [(database, "core", vector_store, tasks.process_media_index),
                                      (scenic_database, "scenic", scenic_vector_store, tasks.process_scenic_media_index)]:
        with dbm.SessionLocal() as db:
            db.add(dbm.User(username="smoke", email="smoke@example.test", hashed_password="unused", role="admin"))
            db.commit()
        for model_id, dimension in [("clipreid-msmt17", 512), ("mgddpm-market2msmt", 1280), ("mgddpm-six-cameras", 1280)]:
            with dbm.SessionLocal() as db:
                project = dbm.Project(name=model_id, description="", owner_username="smoke")
                bind_project(project, model_id)
                db.add(project)
                db.flush()
                pid = project.id
                stored = storage.save_path(str(pid) if scope == "core" else f"scenic/{pid}", source, "person.jpg", category="originals")
                media = dbm.MediaFile(project_id=pid, uploader_username="smoke", filename=stored.filename,
                    object_key=stored.object_key, content_type="image/jpeg", size_bytes=stored.size_bytes,
                    sha256=stored.sha256, media_type="image", status="uploaded")
                db.add(media)
                db.commit()
                mid = media.id
            worker(f"smoke-{scope}-{model_id}", mid, pid, stored.object_key, "image", "smoke")
            with Image.open(source) as decoded, project_models.project_context(pid, scope):
                feature = model_runtime.clip_image_feature(decoded)
                assert tuple(feature.shape) == (1, dimension)
                hits = router.search(feature, top_k=5, project_id=pid)
                assert len(hits) == 1
                assert hits[0].metadata["media_id"] == mid
                assert hits[0].metadata["similarity"] > .99
                if model_id == "clipreid-msmt17":
                    previous = model_runtime.model_service.embed_image(decoded)
                    torch.testing.assert_close(feature, previous, rtol=1e-5, atol=1e-6)
            with dbm.SessionLocal() as db:
                assert db.get(dbm.MediaFile, mid).status == "indexed"
    project_models._cache.clear()
