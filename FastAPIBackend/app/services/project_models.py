"""Explicit project scope, shared by synchronous requests and worker threads."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import RLock

from fastapi import HTTPException

from app.core.config import get_settings
from app.services.model_registry import project_snapshot, model_directory, resolve_model

_current = ContextVar("project_embedding_context", default=None)
_lock = RLock()
_cache = OrderedDict()


def database_for(scope):
    if scope == "core":
        import database
    elif scope == "scenic":
        import scenic_database as database
    else:
        raise ValueError(f"Invalid project domain: {scope}")
    return database


def snapshot_for(project_id, scope="core", *, verify=True):
    module = database_for(scope)
    with module.SessionLocal() as db:
        project = db.get(module.Project, project_id)
        if project is None:
            raise HTTPException(404, detail="项目不存在")
        return project_snapshot(project, verify=verify)


def current_binding():
    return _current.get()


def _create_embedding(snapshot):
    from app.services.model_runtime import CLIPReIDEmbeddingService, MGDDPMEmbeddingService
    if snapshot["adapter"] == "mgddpm":
        return MGDDPMEmbeddingService(model_directory() / snapshot["weights"], snapshot=snapshot)
    options = snapshot["options"]
    settings = get_settings().model_copy(update={
        **{f"clipreid_{k}": v for k, v in options.items()},
        "clipreid_model_path": str(model_directory() / snapshot["weights"]),
        "clipreid_image_height": snapshot["preprocess"]["size"][0],
        "clipreid_image_width": snapshot["preprocess"]["size"][1],
    })
    return CLIPReIDEmbeddingService(settings=settings, base_model_path=model_directory() / "clip_weights" / "ViT-B-16.pt")


class Binding:
    def __init__(self, project_id, scope, snapshot):
        self.project_id, self.scope, self.snapshot = project_id, scope, snapshot
        self._entry = None

    def validate(self):
        current = resolve_model(self.snapshot["id"])
        if current["embedding_space_id"] != self.snapshot["embedding_space_id"]:
            raise HTTPException(409, detail="模型文件或配置发生变化，已停止推理")

    def embedding(self):
        self.validate()
        with _lock:
            if self._entry is None:
                key = self.snapshot["embedding_space_id"]
                if key not in _cache:
                    while len(_cache) >= get_settings().reid_model_cache_size:
                        idle = next((k for k, v in _cache.items() if v[1] == 0), None)
                        if idle is None:
                            raise HTTPException(503, detail="模型缓存已被活动任务占满，请停止闲置相机或增加缓存容量")
                        del _cache[idle]
                    _cache[key] = [_create_embedding(self.snapshot), 0]
                _cache.move_to_end(key)
                self._entry = _cache[key]
                self._entry[1] += 1
            return self._entry[0]

    def close(self):
        with _lock:
            if self._entry is not None:
                self._entry[1] -= 1
                self._entry = None


@contextmanager
def project_context(project_id, scope="core", *, expected_space=None):
    previous = _current.get()
    if previous and previous.project_id == project_id and previous.scope == scope:
        if expected_space and previous.snapshot["embedding_space_id"] != expected_space:
            raise HTTPException(409, detail="任务模型绑定与项目不一致")
        yield previous
        return
    snapshot = snapshot_for(project_id, scope)
    if expected_space and snapshot["embedding_space_id"] != expected_space:
        raise HTTPException(409, detail="任务模型绑定与项目不一致")
    binding = Binding(project_id, scope, snapshot)
    token = _current.set(binding)
    try:
        yield binding
    finally:
        _current.reset(token)
        binding.close()


def project_bound(selector):
    """Bind the whole operation, including all nested frame/batch extraction."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            project_id, scope = selector(*args, **kwargs)
            with project_context(project_id, scope):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def cache_status():
    with _lock:
        return [{"embedding_space_id": key, "active_leases": value[1], "loaded": value[0].is_loaded()}
                for key, value in _cache.items()]
