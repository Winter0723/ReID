from contextlib import contextmanager
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Dict, Iterable, List, Optional

import torch
from filelock import FileLock

from app.core.config import get_settings


@contextmanager
def _exclusive_file_lock(lock_path: Path):
    """Serialize JSON store access across API and worker processes."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=60):
        yield


@dataclass
class VectorPoint:
    id: str
    vector: torch.Tensor
    path: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class InMemoryVectorStore:
    """Milvus-shaped local fallback for CLIP embeddings.

    The fallback is persisted on disk so API and RQ worker processes can share
    indexed vectors during local development.
    """

    def __init__(self, namespace: str = "default", dimension=None) -> None:
        self.dimension = dimension
        self._points: List[VectorPoint] = []
        self._lock = Lock()
        filename = "vector_store.json" if namespace == "default" else f"{namespace}_vector_store.json"
        self.path = get_settings().object_storage_dir / filename
        self._file_lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        self._file_signature: Optional[tuple[int, int]] = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def upsert(self, points: Iterable[VectorPoint]) -> int:
        items_by_id = {point.id: self._normalized_point(point) for point in points}
        items = list(items_by_id.values())
        if self.dimension and any(p.vector.numel() != self.dimension for p in items):
            raise ValueError("Vector dimension does not match embedding space")
        if not items:
            return 0
        ids = {p.id for p in items}
        with self._lock, _exclusive_file_lock(self._file_lock_path):
            self._load_unlocked()
            self._points = [point for point in self._points if point.id not in ids]
            self._points.extend(items)
            self._save_unlocked()
        return len(items)

    def delete_by_media(self, media_id: int) -> int:
        with self._lock, _exclusive_file_lock(self._file_lock_path):
            self._load_unlocked()
            before = len(self._points)
            self._points = [p for p in self._points if p.metadata.get("media_id") != media_id]
            deleted = before - len(self._points)
            if deleted:
                self._save_unlocked()
            return deleted

    def delete_by_project(self, project_id: int) -> int:
        with self._lock, _exclusive_file_lock(self._file_lock_path):
            self._load_unlocked()
            before = len(self._points)
            self._points = [p for p in self._points if p.metadata.get("project_id") != project_id]
            deleted = before - len(self._points)
            if deleted:
                self._save_unlocked()
            return deleted

    def delete_by_ids(self, vector_ids: Iterable[str]) -> int:
        ids = {str(vector_id) for vector_id in vector_ids if vector_id}
        if not ids:
            return 0
        with self._lock, _exclusive_file_lock(self._file_lock_path):
            self._load_unlocked()
            before = len(self._points)
            self._points = [point for point in self._points if point.id not in ids]
            deleted = before - len(self._points)
            if deleted:
                self._save_unlocked()
            return deleted

    def search(
        self,
        query_vector: torch.Tensor,
        *,
        top_k: int,
        project_id: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorPoint]:
        filters = filters or {}
        if top_k <= 0:
            return []
        query = query_vector.detach().to(device="cpu", dtype=torch.float32).reshape(-1)
        if self.dimension and query.numel() != self.dimension:
            raise ValueError("Query dimension does not match embedding space")
        if query.numel() == 0:
            return []
        candidates: List[VectorPoint] = []
        with self._lock, _exclusive_file_lock(self._file_lock_path):
            self._load_unlocked()
            for point in self._points:
                if point.metadata.get("project_id") != project_id:
                    continue
                if filters.get("camera_id") not in (None, "") and point.metadata.get("camera_id") != filters["camera_id"]:
                    continue
                if filters.get("media_id") is not None and point.metadata.get("media_id") != filters["media_id"]:
                    continue
                if not self._matches_time_filters(point.metadata.get("timestamp"), filters):
                    continue
                candidates.append(point)

        if not candidates:
            return []
        mismatched = [point.id for point in candidates if point.vector.numel() != query.numel()]
        if mismatched:
            raise ValueError(
                f"Vector dimension mismatch for {len(mismatched)} stored points: "
                f"query has {query.numel()} dimensions"
            )
        matrix = torch.stack([point.vector.reshape(-1) for point in candidates])
        scores = torch.mv(matrix, query)
        result_count = min(int(top_k), len(candidates))
        top_scores, top_indices = torch.topk(scores, k=result_count, largest=True, sorted=True)

        results: List[VectorPoint] = []
        min_similarity = filters.get("min_similarity")
        for score_tensor, point_index in zip(top_scores, top_indices):
            score = float(score_tensor.item())
            point = candidates[int(point_index.item())]
            enriched = VectorPoint(
                id=point.id,
                vector=point.vector,
                path=point.path,
                metadata={**point.metadata, "similarity": score},
            )
            if min_similarity is not None and score < min_similarity:
                continue
            results.append(enriched)
        return results

    def rebuild_project(self, project_id: int, points: Iterable[VectorPoint]) -> int:
        items_by_id = {point.id: self._normalized_point(point) for point in points}
        items = list(items_by_id.values())
        if self.dimension and any(p.vector.numel() != self.dimension for p in items):
            raise ValueError("Vector dimension does not match embedding space")
        if any(point.metadata.get("project_id") != project_id for point in items):
            raise ValueError("All rebuilt vectors must belong to the requested project")
        with self._lock, _exclusive_file_lock(self._file_lock_path):
            self._load_unlocked()
            self._points = [
                point
                for point in self._points
                if point.metadata.get("project_id") != project_id
            ]
            self._points.extend(items)
            self._save_unlocked()
        return len(items)

    def ensure_ready(self) -> None:
        self._load()

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "memory",
            "path": str(self.path),
            "dimension": self.dimension or get_settings().embedding_dimension,
            "ready": True,
        }

    def _matches_time_filters(self, timestamp: Any, filters: Dict[str, Any]) -> bool:
        if filters.get("start_time") in (None, "") and filters.get("end_time") in (None, ""):
            return True
        parsed = _timestamp_to_seconds(timestamp)
        if parsed is None:
            return False
        start = _timestamp_to_seconds(filters.get("start_time"))
        end = _timestamp_to_seconds(filters.get("end_time"))
        if start is not None and parsed < start:
            return False
        if end is not None and parsed > end:
            return False
        return True

    def _load(self) -> None:
        with self._lock, _exclusive_file_lock(self._file_lock_path):
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        if not self.path.exists():
            self._points = []
            self._file_signature = None
            return
        stat = self.path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._file_signature:
            return
        with self.path.open("r", encoding="utf-8") as f:
            raw_points = json.load(f)
        if not isinstance(raw_points, list):
            raise ValueError(f"Invalid vector store payload in {self.path}: expected a list")
        self._points = [
            VectorPoint(
                id=item["id"],
                vector=torch.tensor(item["vector"], dtype=torch.float32),
                path=item["path"],
                metadata=item.get("metadata", {}),
            )
            for item in raw_points
        ]
        self._file_signature = signature

    def _save_unlocked(self) -> None:
        serialized = [
            {
                "id": point.id,
                "vector": point.vector.detach().cpu().reshape(-1).tolist(),
                "path": point.path,
                "metadata": point.metadata,
            }
            for point in self._points
        ]
        tmp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{id(self)}.tmp"
        )
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(serialized, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(self.path)
            stat = self.path.stat()
            self._file_signature = (stat.st_mtime_ns, stat.st_size)
        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _normalized_point(point: VectorPoint) -> VectorPoint:
        if not point.id:
            raise ValueError("Vector point id cannot be empty")
        vector = point.vector.detach().to(device="cpu", dtype=torch.float32).reshape(-1).contiguous()
        if vector.numel() == 0:
            raise ValueError(f"Vector point {point.id} cannot have an empty embedding")
        return VectorPoint(
            id=str(point.id),
            vector=vector,
            path=str(point.path),
            metadata=dict(point.metadata or {}),
        )


class MilvusVectorStore:
    """Milvus-backed vector store for persistent, project-isolated retrieval."""

    VECTOR_FIELD = "embedding"
    PRIMARY_FIELD = "vector_id"
    DEFAULT_DIMENSION = 512

    def __init__(self, namespace: str = "default", dimension=None) -> None:
        settings = get_settings()
        self.uri = settings.milvus_uri
        self.collection_name = settings.milvus_collection if namespace == "default" else f"{settings.milvus_collection}_{namespace}"
        self.dimension = dimension or settings.embedding_dimension or self.DEFAULT_DIMENSION
        self._client = None
        self._lock = RLock()

    def ensure_ready(self) -> None:
        with self._lock:
            client = self._get_client_unlocked()
            if not client.has_collection(self.collection_name):
                self._create_collection_unlocked(client)
            else:
                description = client.describe_collection(self.collection_name)
                fields = description.get("fields", [])
                vector_field = next((f for f in fields if f["name"] == self.VECTOR_FIELD), None)
                if vector_field and int(vector_field.get("params", {}).get("dim", 0)) != self.dimension:
                    raise ValueError("Existing Milvus collection dimension does not match embedding space")
                self._ensure_index_unlocked(client)
            client.load_collection(self.collection_name)

    def upsert(self, points: Iterable[VectorPoint]) -> int:
        items = list({point.id: point for point in points}.values())
        if not items:
            return 0
        with self._lock:
            self.ensure_ready()
            rows = [self._point_to_row(point) for point in items]
            # Milvus persists writes through its WAL. Per-frame flush seals tiny
            # segments and stalls live ingestion; use read consistency instead.
            self._client.upsert(collection_name=self.collection_name, data=rows)
            return len(rows)

    def delete_by_media(self, media_id: int) -> int:
        return self._delete(f"media_id == {int(media_id)}")

    def delete_by_project(self, project_id: int) -> int:
        return self._delete(f"project_id == {int(project_id)}")

    def delete_by_ids(self, vector_ids: Iterable[str]) -> int:
        ids = list(dict.fromkeys(str(vector_id) for vector_id in vector_ids if vector_id))
        if not ids:
            return 0
        deleted = 0
        with self._lock:
            self.ensure_ready()
            for start in range(0, len(ids), 512):
                encoded_ids = json.dumps(ids[start:start + 512])
                result = self._client.delete(
                    collection_name=self.collection_name,
                    filter=f"{self.PRIMARY_FIELD} in {encoded_ids}",
                )
                deleted += int(result.get("delete_count", 0) or 0)
            self._client.flush(collection_name=self.collection_name)
        return deleted

    def rebuild_project(self, project_id: int, points: Iterable[VectorPoint]) -> int:
        items = list({point.id: point for point in points}.values())
        if self.dimension and any(p.vector.numel() != self.dimension for p in items):
            raise ValueError("Vector dimension does not match embedding space")
        if any(point.metadata.get("project_id") != project_id for point in items):
            raise ValueError("All rebuilt vectors must belong to the requested project")
        with self._lock:
            self.ensure_ready()
            existing_ids = self._project_vector_ids_unlocked(project_id)
            if items:
                self.upsert(items)
            stale_ids = existing_ids - {point.id for point in items}
            if stale_ids:
                self.delete_by_ids(stale_ids)
            return len(items)

    def search(
        self,
        query_vector: torch.Tensor,
        *,
        top_k: int,
        project_id: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorPoint]:
        filters = filters or {}
        if top_k <= 0:
            return []
        self.ensure_ready()
        query = query_vector.detach().cpu().reshape(-1).tolist()
        if len(query) != self.dimension:
            raise ValueError(f"Milvus query dimension mismatch: expected {self.dimension}, got {len(query)}")
        expr = self._build_filter_expression(project_id, filters)
        output_fields = [
            self.PRIMARY_FIELD,
            "target_id",
            "project_id",
            "media_id",
            "camera_id",
            "timestamp",
            "path",
            "source_info",
        ]
        raw_results = self._client.search(
            collection_name=self.collection_name,
            data=[query],
            anns_field=self.VECTOR_FIELD,
            limit=top_k,
            filter=expr,
            output_fields=output_fields,
            search_params={"metric_type": "IP", "params": {}},
            consistency_level="Strong",
        )
        min_similarity = filters.get("min_similarity")
        results: List[VectorPoint] = []
        for hit in raw_results[0]:
            entity = self._hit_entity(hit)
            score = float(hit.get("distance", hit.get("score", 0.0)))
            if min_similarity is not None and score < min_similarity:
                continue
            metadata = {
                "target_id": self._optional_int(entity.get("target_id")),
                "project_id": self._optional_int(entity.get("project_id")),
                "media_id": self._optional_int(entity.get("media_id")),
                "camera_id": self._empty_to_none(entity.get("camera_id")),
                "timestamp": self._empty_to_none(entity.get("timestamp")),
                "source_info": entity.get("source_info") or "",
                "similarity": score,
            }
            results.append(VectorPoint(
                id=entity.get(self.PRIMARY_FIELD) or hit.get("id"),
                vector=torch.empty(0),
                path=entity.get("path") or "",
                metadata=metadata,
            ))
        return results

    def status(self) -> Dict[str, Any]:
        try:
            self.ensure_ready()
            return {
                "backend": "milvus",
                "uri": self.uri,
                "collection": self.collection_name,
                "dimension": self.dimension,
                "ready": True,
            }
        except Exception as exc:
            return {
                "backend": "milvus",
                "uri": self.uri,
                "collection": self.collection_name,
                "dimension": self.dimension,
                "ready": False,
                "error": str(exc),
            }

    def _get_client_unlocked(self):
        if self._client is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:
                raise RuntimeError("VECTOR_BACKEND=milvus requires pymilvus to be installed") from exc
            self._client = MilvusClient(uri=self.uri)
        return self._client

    def _create_collection_unlocked(self, client) -> None:
        from pymilvus import DataType

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(self.PRIMARY_FIELD, DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("target_id", DataType.INT64)
        schema.add_field("project_id", DataType.INT64)
        schema.add_field("media_id", DataType.INT64)
        schema.add_field("camera_id", DataType.VARCHAR, max_length=128)
        schema.add_field("timestamp", DataType.VARCHAR, max_length=128)
        schema.add_field("timestamp_seconds", DataType.DOUBLE)
        schema.add_field("path", DataType.VARCHAR, max_length=2048)
        schema.add_field("source_info", DataType.VARCHAR, max_length=2048)
        schema.add_field(self.VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=self.dimension)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name=self.VECTOR_FIELD,
            index_type="HNSW",
            metric_type="IP",
            params={"M": 16, "efConstruction": 200},
        )
        index_params.add_index(field_name="project_id", index_type="STL_SORT")
        index_params.add_index(field_name="media_id", index_type="STL_SORT")
        client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )

    def _ensure_index_unlocked(self, client) -> None:
        indexes = client.list_indexes(collection_name=self.collection_name)
        if self.VECTOR_FIELD not in indexes:
            index_params = client.prepare_index_params()
            index_params.add_index(
                field_name=self.VECTOR_FIELD,
                index_type="HNSW",
                metric_type="IP",
                params={"M": 16, "efConstruction": 200},
            )
            client.create_index(collection_name=self.collection_name, index_params=index_params)

    def _delete(self, expr: str) -> int:
        with self._lock:
            self.ensure_ready()
            result = self._client.delete(collection_name=self.collection_name, filter=expr)
            self._client.flush(collection_name=self.collection_name)
            return int(result.get("delete_count", 0) or 0)

    def _project_vector_ids_unlocked(self, project_id: int) -> set[str]:
        expression = f"project_id == {int(project_id)}"
        query_iterator = getattr(self._client, "query_iterator", None)
        if query_iterator is not None:
            iterator = query_iterator(
                collection_name=self.collection_name,
                batch_size=1000,
                filter=expression,
                output_fields=[self.PRIMARY_FIELD],
                consistency_level="Strong",
            )
            vector_ids: set[str] = set()
            try:
                while True:
                    rows = iterator.next()
                    if not rows:
                        break
                    vector_ids.update(
                        str(row[self.PRIMARY_FIELD])
                        for row in rows
                        if row.get(self.PRIMARY_FIELD)
                    )
            finally:
                iterator.close()
            return vector_ids

        vector_ids = set()
        offset = 0
        while True:
            rows = self._client.query(
                collection_name=self.collection_name,
                filter=expression,
                output_fields=[self.PRIMARY_FIELD],
                consistency_level="Strong",
                limit=1000,
                offset=offset,
            )
            if not rows:
                break
            vector_ids.update(
                str(row[self.PRIMARY_FIELD])
                for row in rows
                if row.get(self.PRIMARY_FIELD)
            )
            if len(rows) < 1000:
                break
            offset += len(rows)
        return vector_ids

    def _point_to_row(self, point: VectorPoint) -> Dict[str, Any]:
        vector = point.vector.detach().cpu().reshape(-1).tolist()
        if len(vector) != self.dimension:
            raise ValueError(f"Milvus vector dimension mismatch: expected {self.dimension}, got {len(vector)}")
        metadata = point.metadata or {}
        return {
            self.PRIMARY_FIELD: point.id,
            "target_id": int(metadata.get("target_id") or 0),
            "project_id": int(metadata["project_id"]),
            "media_id": int(metadata["media_id"]),
            "camera_id": str(metadata.get("camera_id") or ""),
            "timestamp": str(metadata.get("timestamp") or ""),
            "timestamp_seconds": float(_timestamp_to_seconds(metadata.get("timestamp")) or -1.0),
            "path": point.path,
            "source_info": str(metadata.get("source_info") or ""),
            self.VECTOR_FIELD: vector,
        }

    def _build_filter_expression(self, project_id: int, filters: Dict[str, Any]) -> str:
        clauses = [f"project_id == {int(project_id)}"]
        if filters.get("media_id"):
            clauses.append(f"media_id == {int(filters['media_id'])}")
        if filters.get("camera_id"):
            clauses.append(f"camera_id == {json.dumps(str(filters['camera_id']))}")
        start = _timestamp_to_seconds(filters.get("start_time"))
        end = _timestamp_to_seconds(filters.get("end_time"))
        if start is not None:
            clauses.append(f"timestamp_seconds >= {float(start)}")
        if end is not None:
            clauses.append(f"timestamp_seconds <= {float(end)}")
        return " and ".join(clauses)

    def _hit_entity(self, hit: Dict[str, Any]) -> Dict[str, Any]:
        entity = hit.get("entity")
        if entity is not None:
            return entity
        return hit

    def _optional_int(self, value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        return int(value)

    def _empty_to_none(self, value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        return str(value)


def _timestamp_to_seconds(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    parts = text.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return None


def create_vector_store(namespace: str = "default", dimension=None):
    backend = get_settings().vector_backend.lower()
    if backend == "milvus":
        return MilvusVectorStore(namespace=namespace, dimension=dimension)
    if backend in {"memory", "inmemory", "local"}:
        return InMemoryVectorStore(namespace=namespace, dimension=dimension)
    raise RuntimeError(f"Unsupported VECTOR_BACKEND: {get_settings().vector_backend}")


_space_stores = {}
_space_lock = RLock()


def store_for_snapshot(scope, snapshot):
    namespace = ("default" if scope == "core" else "scenic") if snapshot.get("legacy_vectors") else f"{scope}_{snapshot['embedding_space_id']}"
    key = (scope, namespace, snapshot["dimension"], str(get_settings().object_storage_dir), get_settings().vector_backend)
    with _space_lock:
        if key not in _space_stores:
            _space_stores[key] = create_vector_store(namespace, snapshot["dimension"])
        return _space_stores[key]


class ProjectVectorRouter:
    """No default-space fallback for project operations, including deletion."""
    def __init__(self, scope):
        self.scope = scope

    def for_project(self, project_id, *, verify=False):
        from app.services.project_models import current_binding, snapshot_for
        binding = current_binding()
        if binding and binding.scope == self.scope and binding.project_id == project_id:
            snapshot = binding.snapshot
            if verify:
                binding.validate()
        else:
            snapshot = snapshot_for(project_id, self.scope, verify=verify)
        return store_for_snapshot(self.scope, snapshot)

    def upsert(self, points):
        points = list(points)
        groups = {}
        for point in points:
            project_id = point.metadata.get("project_id")
            if project_id is None:
                raise ValueError("Project is required for vector writes")
            from app.services.project_models import current_binding, snapshot_for
            binding = current_binding()
            snapshot = binding.snapshot if binding and binding.scope == self.scope and binding.project_id == project_id else snapshot_for(project_id, self.scope)
            supplied = point.metadata.get("embedding_space_id")
            if supplied and supplied != snapshot["embedding_space_id"]:
                raise ValueError("Vector belongs to another embedding space")
            point.metadata["embedding_space_id"] = snapshot["embedding_space_id"]
            groups.setdefault(project_id, []).append(point)
        return sum(self.for_project(pid, verify=True).upsert(items) for pid, items in groups.items())

    def search(self, query_vector, *, project_id, **kwargs):
        return self.for_project(project_id, verify=True).search(query_vector, project_id=project_id, **kwargs)

    def rebuild_project(self, project_id, points):
        return self.for_project(project_id, verify=True).rebuild_project(project_id, points)

    def delete_by_project(self, project_id):
        return self.for_project(project_id).delete_by_project(project_id)

    def delete_by_media(self, media_id):
        from app.services.project_models import database_for
        module = database_for(self.scope)
        with module.SessionLocal() as db:
            media = db.get(module.MediaFile, media_id)
            return self.for_project(media.project_id).delete_by_media(media_id) if media else 0

    def delete_by_ids(self, vector_ids):
        from app.services.project_models import current_binding, database_for
        ids = list(vector_ids)
        if not ids:
            return 0
        binding = current_binding()
        if binding and binding.scope == self.scope:
            return store_for_snapshot(self.scope, binding.snapshot).delete_by_ids(ids)
        module = database_for(self.scope)
        with module.SessionLocal() as db:
            rows = db.query(module.DetectionTarget.project_id, module.DetectionTarget.vector_id).filter(module.DetectionTarget.vector_id.in_(ids)).all()
        groups = {}
        for pid, vector_id in rows:
            groups.setdefault(pid, []).append(vector_id)
        return sum(self.for_project(pid).delete_by_ids(items) for pid, items in groups.items())

    def ensure_ready(self):
        from app.services.model_registry import discover_models
        for snapshot in discover_models():
            if snapshot["available"]:
                store_for_snapshot(self.scope, snapshot).ensure_ready()

    def status(self):
        self.ensure_ready()
        with _space_lock:
            rows = [store.status() for key, store in _space_stores.items() if key[0] == self.scope]
        return {"backend": get_settings().vector_backend, "ready": all(r.get("ready", True) for r in rows), "spaces": rows}


vector_store = ProjectVectorRouter("core")
scenic_vector_store = ProjectVectorRouter("scenic")
