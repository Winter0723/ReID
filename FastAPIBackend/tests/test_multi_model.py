import copy
import importlib.util
import json
from types import SimpleNamespace

import pytest
import torch
from fastapi import HTTPException

from app.services import model_registry as registry
from app.services import project_models as runtime
from app.services import vector_store as vectors

pytestmark = pytest.mark.real_model_registry


def recipe(model_id="first", dimension=512):
    data = json.loads((registry.ROOT / "model/clipreid-msmt17.model.json").read_text())
    data.update(id=model_id, name=model_id, sha256=model_id * 10, dimension=dimension)
    data["embedding_space_id"] = registry.space_id(data)
    return data


def project(snapshot):
    return SimpleNamespace(reid_model_id=snapshot["id"], embedding_space_id=snapshot["embedding_space_id"],
                           model_snapshot_json=json.dumps(snapshot))


def test_space_identity_ignores_display_name_but_tracks_every_inference_rule():
    original = recipe()
    renamed = copy.deepcopy(original)
    renamed.update(name="renamed", weights="renamed.pth", id="renamed")
    assert registry.space_id(original) == registry.space_id(renamed)
    for key, value in [("sha256", "different"), ("adapter_version", "2"), ("dimension", 1280),
                       ("output", "other"), ("preprocess", {"size": [128, 64]}), ("options", {"precision": "int8"})]:
        changed = {**original, key: value}
        assert registry.space_id(changed) != registry.space_id(original)


def test_snapshot_rejects_replacement_and_unverified_history(monkeypatch):
    original, replacement = recipe(), recipe("new")
    monkeypatch.setattr(registry, "resolve_model", lambda _: replacement)
    with pytest.raises(HTTPException, match="已变化"):
        registry.project_snapshot(project(original))
    assert registry.project_snapshot(project(original), verify=False)["sha256"] == original["sha256"]
    with pytest.raises(HTTPException, match="待核验"):
        registry.project_snapshot(SimpleNamespace(embedding_space_id=""))


def test_discovery_missing_invalid_and_replaced_files(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "model_directory", lambda: tmp_path)
    monkeypatch.setattr(registry, "_inspect_checkpoint", lambda *args: None)
    descriptor = recipe()
    descriptor.pop("sha256")
    descriptor.pop("embedding_space_id")
    (tmp_path / "first.model.json").write_text(json.dumps(descriptor))
    assert not registry.discover_models()[0]["available"]
    (tmp_path / descriptor["weights"]).write_bytes(b"first")
    (tmp_path / "clip_weights").mkdir()
    (tmp_path / "clip_weights/ViT-B-16.pt").write_bytes(b"base")
    first = registry.resolve_model("first")
    (tmp_path / "clip_weights/ViT-B-16.pt").write_bytes(b"changed-base")
    assert first["embedding_space_id"] != registry.resolve_model("first")["embedding_space_id"]
    first = registry.resolve_model("first")
    (tmp_path / descriptor["weights"]).write_bytes(b"second")
    assert first["embedding_space_id"] != registry.resolve_model("first")["embedding_space_id"]
    descriptor["dimension"] = 1280
    (tmp_path / "first.model.json").write_text(json.dumps(descriptor))
    assert not registry.discover_models()[0]["available"]


def test_model_binding_cannot_be_reassigned(monkeypatch):
    first = recipe()
    monkeypatch.setattr(registry, "resolve_model", lambda _: recipe("replacement"))
    with pytest.raises(HTTPException, match="不可更换"):
        registry.bind_project(project(first), "replacement")


def test_same_dimension_spaces_and_domains_never_share_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "get_settings", lambda: SimpleNamespace(
        object_storage_dir=tmp_path, vector_backend="memory", embedding_dimension=2))
    snapshots = [recipe("a", 2), recipe("b", 2), recipe("c", 3)]
    stores = [vectors.store_for_snapshot(scope, snapshot) for scope in ("core", "scenic") for snapshot in snapshots]
    assert len({store.path for store in stores}) == 6
    for index, store in enumerate(stores):
        feature = torch.ones(store.dimension)
        store.upsert([vectors.VectorPoint(str(index), feature, "crop.jpg", {"project_id": 1, "media_id": 1})])
        assert [p.id for p in store.search(feature, top_k=10, project_id=1)] == [str(index)]
    stores[0].delete_by_project(1)
    assert stores[1].search(torch.ones(2), top_k=10, project_id=1)
    with pytest.raises(ValueError, match="dimension"):
        stores[1].upsert([vectors.VectorPoint("bad", torch.ones(3), "", {"project_id": 1})])
    with pytest.raises(ValueError, match="dimension"):
        stores[1].rebuild_project(1, [vectors.VectorPoint("bad", torch.ones(3), "", {"project_id": 1})])
    with pytest.raises(ValueError, match="dimension"):
        stores[1].search(torch.ones(3), top_k=10, project_id=1)


def test_cache_pins_active_models_and_only_evicts_idle(monkeypatch):
    runtime._cache.clear()
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(reid_model_cache_size=1))
    monkeypatch.setattr(runtime, "resolve_model", lambda key: recipe(key))
    monkeypatch.setattr(runtime, "_create_embedding", lambda _: object())
    first = runtime.Binding(1, "core", recipe("a"))
    second = runtime.Binding(2, "core", recipe("b"))
    try:
        loaded = first.embedding()
        assert first.embedding() is loaded
        with pytest.raises(HTTPException, match="缓存"):
            second.embedding()
        first.close()
        assert second.embedding() is not loaded
    finally:
        first.close()
        second.close()
        runtime._cache.clear()


def test_bound_text_capability_and_worker_space_are_enforced(monkeypatch):
    from app.services.model_runtime import clip_text_feature
    snapshot = recipe()
    snapshot["capabilities"]["text"] = False
    monkeypatch.setattr(runtime, "snapshot_for", lambda *a, **k: snapshot)
    with pytest.raises(HTTPException, match="不一致"):
        with runtime.project_context(1, expected_space="another-space"):
            pass
    with runtime.project_context(1):
        with pytest.raises(HTTPException, match="不支持文本"):
            clip_text_feature("person")
    assert runtime.current_binding() is None


def test_project_model_fields_are_not_editable():
    from app.schemas import ProjectUpdate
    from pydantic import ValidationError
    for field in ("reid_model_id", "embedding_space_id", "model_snapshot_json"):
        with pytest.raises(ValidationError):
            ProjectUpdate(**{field: "changed"})


def test_legacy_storage_mapping_requires_explicit_attestation(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "get_settings", lambda: SimpleNamespace(
        object_storage_dir=tmp_path, vector_backend="memory", embedding_dimension=512))
    snapshot = recipe()
    fresh = vectors.store_for_snapshot("core", snapshot)
    snapshot["legacy_vectors"] = True
    assert vectors.store_for_snapshot("core", snapshot).path == tmp_path / "vector_store.json"
    assert vectors.store_for_snapshot("scenic", snapshot).path == tmp_path / "scenic_vector_store.json"
    assert fresh.path != tmp_path / "vector_store.json"


def test_models_api_binds_both_domains_and_refuses_text_and_mutation(client, register_user, register_scenic_user, monkeypatch):
    first, second = recipe("clipreid-msmt17"), recipe("mgd", 1280)
    second.update(adapter="mgddpm", capabilities={"image": True, "text": False})
    second["embedding_space_id"] = registry.space_id(second)
    monkeypatch.setattr(registry, "discover_models", lambda: [dict(r, available=True, unavailable_reason="") for r in (first, second)])
    for prefix, register, name in [("/api/v1", register_user, "models_core"),
                                   ("/api/v1/scenic", register_scenic_user, "models_scenic")]:
        user = register(name, f"{name}@example.com")
        headers = {"Authorization": f"Bearer {user['access_token']}"}
        assert client.get(f"{prefix}/models").status_code == 401
        models = client.get(f"{prefix}/models", headers=headers)
        assert models.status_code == 200
        assert "weights" not in models.json()[0]
        created = client.post(f"{prefix}/projects", headers=headers, json={"name": "MGD", "reid_model_id": "mgd"})
        assert created.status_code == 200, created.text
        data = created.json()
        assert data["embedding_space_id"] == second["embedding_space_id"]
        assert data["reid_model"]["capabilities"]["text"] is False
        if prefix == "/api/v1":
            assert client.patch(f"{prefix}/projects/{data['id']}", headers=headers, json={"reid_model_id": first["id"]}).status_code == 422
            response = client.post(f"{prefix}/search/text", headers=headers, json={"project_id": data["id"], "query_text": "person"})
            assert response.status_code == 422, response.text
        before = data["embedding_space_id"]
        changed = copy.deepcopy(second)
        changed["sha256"] = "replaced"
        changed["embedding_space_id"] = registry.space_id(changed)
        with monkeypatch.context() as context:
            context.setattr(registry, "discover_models", lambda: [dict(changed, available=True)])
            rows = client.get(f"{prefix}/projects", headers=headers).json()
            bound = next(r for r in rows if r["id"] == data["id"])
            assert bound["embedding_space_id"] == before
            assert not bound["reid_model"]["available"]


def test_register_multiple_mgddpm_checkpoints_with_different_camera_counts(tmp_path, monkeypatch):
    """Real torch archives, tiny shared storage, full teacher key/shape contract."""
    from model.mgddpm import MGDTeacher
    spec = importlib.util.spec_from_file_location("register_reid_model", registry.ROOT / "scripts/register_reid_model.py")
    registration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registration)
    monkeypatch.setattr(registry, "model_directory", lambda: tmp_path)
    snapshots = []
    for cameras in (6, 8, 21):
        with torch.device("meta"):
            shapes = MGDTeacher(cameras).state_dict()
        state = {key: torch.full((), cameras / 100 if value.is_floating_point() else 0,
                                  dtype=value.dtype, device="cpu").expand(value.shape)
                 for key, value in shapes.items()}
        name = f"teacher-{cameras}"
        torch.save({"teacher": state}, tmp_path / f"{name}.pth")
        path, snapshot = registration.register_model(f"{name}.pth", name, f"MGD-DPM {cameras}")
        assert path.is_file()
        assert snapshot["dimension"] == 1280
        snapshots.append(snapshot)
    assert len({row["embedding_space_id"] for row in snapshots}) == 3
    assert len(registry.public_models()) == 3
    assert all(row["available"] for row in registry.public_models())
    assert all(not row["capabilities"]["text"] for row in registry.public_models())
    with pytest.raises(ValueError, match="ID 已存在"):
        registration.register_model("teacher-6.pth", "teacher-6", "replacement")
    with pytest.raises(ValueError, match="camera_index"):
        registration.register_model("teacher-6.pth", "bad-camera", "bad", overrides={"camera_index": 6})
    assert not (tmp_path / "bad-camera.model.json").exists()
    monkeypatch.setattr(vectors, "get_settings", lambda: SimpleNamespace(
        object_storage_dir=tmp_path, vector_backend="memory", embedding_dimension=1280))
    for scope in ("core", "scenic"):
        for index, snapshot in enumerate(snapshots):
            store = vectors.store_for_snapshot(scope, snapshot)
            feature = torch.ones(1280)
            store.upsert([vectors.VectorPoint(str(index), feature, "person.jpg", {"project_id": 1, "media_id": index + 1})])
            hits = store.search(feature, project_id=1, top_k=10)
            assert [hit.id for hit in hits] == [str(index)]
    # Replacing one of the same-dimension weights cannot change the other binding.
    original = snapshots[0]
    replacement = {"teacher": state}
    torch.save(replacement, tmp_path / "teacher-6.pth")
    with pytest.raises(HTTPException, match="已变化"):
        registry.project_snapshot(project(original))
    assert registry.project_snapshot(project(snapshots[1]))["embedding_space_id"] == snapshots[1]["embedding_space_id"]


def test_mgddpm_runtime_uses_the_registered_recipe(monkeypatch):
    from app.services import model_runtime
    from model import mgddpm
    captured = {}
    class FakeTeacher:
        def to(self, *args): return self
        def float(self): return self
        def eval(self): return self
    def load(path, **kwargs):
        captured.update(kwargs)
        return FakeTeacher()
    monkeypatch.setattr(mgddpm, "load_teacher", load)
    snapshot = {"preprocess": {"size": [320, 160]}, "options": {
        "stride_size": 16, "camera_index": 3, "sie_coefficient": 2.0, "precision": "fp32"}}
    service = model_runtime.MGDDPMEmbeddingService("second.pth", snapshot=snapshot)
    service.load()
    assert captured == {"image_size": [320, 160], "stride": 16, "camera_index": 3, "sie_coefficient": 2.0}
    assert service._preprocess.transforms[0].size == (320, 160)
