"""Optional local candidate-only ByteTrack analysis and conservative framing.

No model is downloaded here. Every candidate owns its tracker state; identities
are matched with the existing ReID embedder, never inferred from a track ID.
Ambiguous identity, crowd intersections and small subjects retain the full frame.
"""
from __future__ import annotations
from app.services.project_models import project_bound, current_binding



import json
import math
import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from filelock import FileLock
from PIL import Image, ImageOps

from app.core.config import get_settings
from app.services.scenic_editing import (
    EditingError, _ANALYSIS_SLOT, _cache_read, _cache_root, _cache_write, source_sha256,
)
from app.services.storage import storage


TRACKING_VERSION = 2
_SAMPLE_FPS = 5.0


def tracking_enabled() -> bool:
    configured = getattr(get_settings(), "scenic_tracking_enabled", None)
    if configured is not None:
        return bool(configured)
    return os.getenv("SCENIC_TRACKING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _weights_path() -> Path:
    configured = Path(get_settings().yolo_model_path)
    if not configured.is_absolute():
        backend = Path(__file__).resolve().parents[2]
        configured = next((path for path in (configured, backend / configured, backend.parent / configured) if path.is_file()), configured)
    if not configured.is_file():
        raise EditingError("本地 YOLO 权重不存在；精分析不会自动下载模型")
    return configured.resolve()


def _embedding(image: Image.Image) -> np.ndarray:
    from app.services.model_runtime import clip_image_feature
    feature = clip_image_feature(image).detach().float().cpu().numpy().reshape(-1)
    return feature / max(float(np.linalg.norm(feature)), 1e-8)


@lru_cache(maxsize=8)
def _weight_fingerprint(path: str, size: int, modified_ns: int) -> str:
    # Model assets are immutable between deployments. Stat changes invalidate
    # the process cache without rehashing two large checkpoints for every clip.
    return source_sha256(Path(path))


def _model_fingerprint(path: Path) -> str:
    metadata = path.stat()
    return _weight_fingerprint(str(path), metadata.st_size, metadata.st_mtime_ns)


def _local_embedding_signature() -> str:
    """Preflight CLIP-ReID and its base checkpoint before any lazy loader runs.

    The existing loader downloads a missing/corrupt CLIP base model. Optional
    Scenic analysis must instead report missing local resources and keep fit.
    """
    from app.services.model_runtime import CLIPReIDEmbeddingService
    settings = get_settings()
    checkpoint = CLIPReIDEmbeddingService._resolve_model_path(settings.clipreid_model_path or settings.custom_reid_model_path)
    if not checkpoint.is_file():
        raise EditingError("本地 CLIP-ReID 权重缺失；精分析不会自动下载模型")
    CLIPReIDEmbeddingService._ensure_project_root_importable()
    from model.clip import clip
    url = clip._MODELS[settings.clipreid_model_name]
    directory = Path(os.environ.get("CLIP_CACHE_DIR") or CLIPReIDEmbeddingService._project_root() / "model" / "clip_weights")
    base = directory / url.rsplit("/", 1)[-1]
    if not base.is_file() or _model_fingerprint(base) != url.split("/")[-2]:
        raise EditingError("本地 CLIP 基础权重缺失或校验失败；精分析不会自动下载模型")
    return _model_fingerprint(checkpoint) + ":" + _model_fingerprint(base)


def _entry_image(session) -> Image.Image:
    with Image.open(storage.path_for(session.entry_image_object_key)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    box_text = getattr(session, "entry_target_box_json", "")
    if box_text:
        box = json.loads(box_text)
        if not isinstance(box, list) or len(box) != 4 or not all(isinstance(value, (float, int)) and 0 <= value <= 1 for value in box):
            raise EditingError("入园参照目标框无效")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise EditingError("入园参照目标框无效")
        width, height = image.size
        image = image.crop((int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height)))
    else:
        from app.services.model_runtime import detect_people
        frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        detections = detect_people(frame, min_confidence=0.25)
        if len(detections) > 1:
            raise EditingError("入园照有多个人物，请先指定游客目标")
        if len(detections) == 1:
            crop = detections[0].crop_from(frame)
            if crop is not None and crop.size:
                image = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    return image


def _iou(first: list[float], second: list[float]) -> float:
    x1, y1 = max(first[0], second[0]), max(first[1], second[1])
    x2, y2 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_first = (first[2] - first[0]) * (first[3] - first[1])
    area_second = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(area_first + area_second - intersection, 1e-8)


def smooth_trajectory(points: list[dict], max_speed: float = 0.18) -> list[dict]:
    """EMA plus a strict normalized-frame motion speed limit, preserving times."""
    result = []
    for point in sorted(points, key=lambda item: item["time_seconds"]):
        x, y = float(point["x"]), float(point["y"])
        if result:
            previous = result[-1]
            elapsed = max(0.0, point["time_seconds"] - previous["time_seconds"])
            limit = max_speed * elapsed
            x = previous["x"] + max(-limit, min(limit, (x - previous["x"]) * 0.35))
            y = previous["y"] + max(-limit, min(limit, (y - previous["y"]) * 0.35))
        result.append({"time_seconds": round(float(point["time_seconds"]), 4),
                       "x": round(min(1.0, max(0.0, x)), 5), "y": round(min(1.0, max(0.0, y)), 5)})
    return result


def assess_track(observations: list[dict], sample_count: int, start: float, end: float, width: int, height: int) -> dict:
    """Apply conservative gates independently of the expensive model execution."""
    if not observations:
        return {"reliable": False, "reason": "未找到可确认的连续人物轨迹"}
    if width <= 0 or height <= 0 or sample_count <= 0 or not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return {"reliable": False, "reason": "人物轨迹分析范围无效"}
    # Bad detector/cache coordinates cannot authorize an automatic subject crop.
    try:
        for item in observations:
            x1, y1, x2, y2 = item["box"]
            values = [x1, y1, x2, y2, item["time_seconds"], item["identity_score"], item["reference_score"]]
            if not all(math.isfinite(float(value)) for value in values) or not (
                0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1 and start <= item["time_seconds"] < end
            ):
                return {"reliable": False, "reason": "人物轨迹坐标或时间无效"}
    except (KeyError, TypeError, ValueError):
        return {"reliable": False, "reason": "人物轨迹数据不完整"}
    by_track: dict[int, list[dict]] = {}
    for item in observations:
        by_track.setdefault(item["track_id"], []).append(item)
    # Require consistent reference agreement over time; a single lucky score is not enough.
    summaries = []
    for track_id, items in by_track.items():
        confirmed = [item for item in items if item["identity_score"] >= 0.5 and item["reference_score"] >= 0.6]
        if len(confirmed) >= 3:
            summaries.append((float(np.median([item["reference_score"] for item in confirmed])), track_id, items))
    summaries.sort(reverse=True, key=lambda item: item[0])
    if not summaries:
        return {"reliable": False, "reason": "游客身份与轨迹的连续对应不可靠"}
    if len(summaries) > 1 and summaries[0][0] - summaries[1][0] < 0.08:
        return {"reliable": False, "reason": "多人外观接近，无法可靠确定跟随目标"}
    _, _, items = summaries[0]
    items.sort(key=lambda item: item["time_seconds"])
    visible_fraction = len(items) / max(1, sample_count)
    if visible_fraction < 0.65:
        return {"reliable": False, "reason": "人物持续出镜不足"}
    if any(item.get("crowded") for item in items):
        return {"reliable": False, "reason": "人物发生交错或遮挡，保留完整画面供人工复核"}
    if any(item["identity_score"] < 0.4 or item["reference_score"] < 0.48 for item in items):
        return {"reliable": False, "reason": "跟踪过程中人物身份一致性下降"}
    # Use the longest continuous run and do not bridge invisible intervals.
    runs: list[list[dict]] = [[]]
    for item in items:
        if runs[-1] and item["time_seconds"] - runs[-1][-1]["time_seconds"] > 0.65:
            runs.append([])
        runs[-1].append(item)
    run = max(runs, key=len)
    continuous = min(end, run[-1]["time_seconds"] + 1 / _SAMPLE_FPS) - run[0]["time_seconds"]
    if continuous < min(1.5, (end - start) * 0.65):
        return {"reliable": False, "reason": "可确认的人物轨迹过短"}
    areas = [(item["box"][2] - item["box"][0]) * (item["box"][3] - item["box"][1]) for item in run]
    if min(areas) < 0.015 or min(item["box"][3] - item["box"][1] for item in run) < 0.18:
        return {"reliable": False, "reason": "人物过小，自动裁切会过度放大"}
    crop_width = min(1.0, (height * 9 / 16) / width)
    crop_height = min(1.0, (width * 16 / 9) / height)
    # A narrow portrait source crops vertically, so both axes must contain the
    # full subject. Axes that retain the entire source do not need crop margins.
    if any((crop_width < 1 and item["box"][2] - item["box"][0] > crop_width * 0.84)
           or (crop_height < 1 and item["box"][3] - item["box"][1] > crop_height * 0.84) for item in run):
        return {"reliable": False, "reason": "竖版裁切无法完整容纳人物"}
    points = [{"time_seconds": item["time_seconds"], "x": (item["box"][0] + item["box"][2]) / 2,
               "y": (item["box"][1] + item["box"][3]) / 2} for item in run]
    trajectory = smooth_trajectory(points)
    for point, item in zip(trajectory, run):
        for axis, extent, lower, upper in (("x", crop_width, 0, 2), ("y", crop_height, 1, 3)):
            if extent >= 1:
                continue
            center = min(1 - extent / 2, max(extent / 2, point[axis]))
            if item["box"][lower] < center - extent / 2 + 0.015 or item["box"][upper] > center + extent / 2 - 0.015:
                return {"reliable": False, "reason": "人物移动过快，平滑裁切可能丢失主体"}
    return {"reliable": True, "trajectory": trajectory, "visible_fraction": round(visible_fraction, 4),
            "person_area": round(float(np.median(areas)), 4), "continuous_seconds": round(continuous, 4),
            "start_seconds": max(start, run[0]["time_seconds"]),
            "end_seconds": min(end, run[-1]["time_seconds"] + 1 / _SAMPLE_FPS)}


def analyze_candidate(clip: dict, session, segment) -> dict:
    if not tracking_enabled():
        return {"reliable": False, "reason": "本地人物跟踪未启用"}
    if abs(float(clip.get("sample_aspect_ratio", 1.0)) - 1.0) > 1e-6:
        # OpenCV boxes use decoded pixel coordinates; the renderer corrects SAR
        # before framing, so these boxes cannot safely authorize a portrait crop.
        return {"reliable": False, "reason": "素材使用非方形像素，保留完整画面以避免裁掉人物"}
    if segment is None or not segment.thumbnail_object_key:
        return {"reliable": False, "reason": "缺少命中人物参照图"}
    import importlib.util
    if importlib.util.find_spec("lap") is None:
        return {"reliable": False, "reason": "本地 ByteTrack 缺少 lap 依赖，请配置可选跟踪环境"}
    return _analyze_bound_candidate(clip, session, segment)


@project_bound(lambda clip, session, segment: (session.project_id, "scenic"))
def _analyze_bound_candidate(clip, session, segment):
    weights = _weights_path()
    embedding_signature = current_binding().snapshot["embedding_space_id"]
    path = storage.path_for(clip["object_key"])
    reference_path = storage.path_for(segment.thumbnail_object_key)
    entry_path = storage.path_for(session.entry_image_object_key)
    if not reference_path.is_file() or not entry_path.is_file():
        return {"reliable": False, "reason": "参照图已移除"}
    import hashlib
    key = hashlib.sha256(json.dumps({
        "version": TRACKING_VERSION, "source": clip["source_sha256"], "start": clip["start_seconds"], "end": clip["end_seconds"],
        "entry": source_sha256(entry_path), "box": getattr(session, "entry_target_box_json", ""),
        "reference": source_sha256(reference_path), "weights": _model_fingerprint(weights),
        "embedding": embedding_signature,
    }, sort_keys=True).encode()).hexdigest()
    cache_path = _cache_root() / f"track-{key}.json"
    with FileLock(str(cache_path) + ".lock", timeout=600):
        cached = _cache_read(cache_path)
        if cached:
            return cached
        with _ANALYSIS_SLOT, FileLock(str(_cache_root() / "analysis.slot.lock"), timeout=600):
            # Disable Ultralytics dependency auto-install as well as weight downloads.
            os.environ.setdefault("YOLO_AUTOINSTALL", "false")
            from ultralytics import YOLO
            entry_feature = _embedding(_entry_image(session))
            with Image.open(reference_path) as image:
                reference_feature = _embedding(image.convert("RGB"))
            if float(entry_feature @ reference_feature) < 0.45:
                return {"reliable": False, "reason": "命中人物与入园参照一致性不足，请人工复核"}
            tracker_model = YOLO(str(weights))
            start, end = float(clip["start_seconds"]), float(clip["end_seconds"])
            capture = cv2.VideoCapture(str(path))
            observations: list[dict] = []
            count = 0
            width, height = int(clip["width"]), int(clip["height"])
            try:
                for seconds in np.arange(start, end, 1 / _SAMPLE_FPS):
                    capture.set(cv2.CAP_PROP_POS_MSEC, float(seconds) * 1000)
                    ok, frame = capture.read()
                    if not ok:
                        return {"reliable": False, "reason": "候选区间解码失败"}
                    count += 1
                    height, width = frame.shape[:2]
                    results = tracker_model.track(frame, persist=True, tracker="bytetrack.yaml", classes=[0],
                                                  conf=0.25, verbose=False, device=get_settings().model_device if get_settings().model_device != "auto" else None)
                    if not results or results[0].boxes is None or results[0].boxes.id is None:
                        continue
                    boxes = results[0].boxes.xyxy.detach().cpu().numpy()
                    identifiers = results[0].boxes.id.detach().cpu().numpy()
                    normalized = [[float(box[0]) / width, float(box[1]) / height, float(box[2]) / width, float(box[3]) / height] for box in boxes]
                    for index, (box, track_id) in enumerate(zip(boxes, identifiers)):
                        x1, y1, x2, y2 = [int(value) for value in box]
                        crop = frame[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
                        if crop.size == 0 or crop.shape[0] < 30 or crop.shape[1] < 15:
                            continue
                        feature = _embedding(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
                        observations.append({"time_seconds": float(seconds), "track_id": int(track_id), "box": normalized[index],
                                             "identity_score": float(feature @ entry_feature), "reference_score": float(feature @ reference_feature),
                                             "crowded": any(index != other and _iou(normalized[index], other_box) > 0.1 for other, other_box in enumerate(normalized))})
            finally:
                capture.release()
                del tracker_model
            result = assess_track(observations, count, start, end, width, height)
            _cache_write(cache_path, result)
            return result


def analyze_beats(path: Path) -> dict:
    """Use a supplied composition grid or optional librosa; failure is explicit."""
    path = Path(path)
    fingerprint = source_sha256(path)
    cache_path = _cache_root() / f"beats-v1-{fingerprint}.json"
    with FileLock(str(cache_path) + ".lock", timeout=120):
        cached = _cache_read(cache_path)
        if cached:
            return cached
        # Generated music has a known score. Use the exact grid, avoiding false
        # beat detections in soft musical passages or an unnecessary dependency.
        from app.services.scenic_templates import MUSIC
        track = MUSIC.get(path.stem)
        if track:
            beat = 60.0 / track["bpm"]
            result = {"beats": [round(index * beat, 6) for index in range(int(100 / beat) + 1)],
                      "bpm": track["bpm"], "source": "composition_score", "source_sha256": fingerprint}
        else:
            try:
                import librosa
                audio, sample_rate = librosa.load(str(path), sr=22050, mono=True)
                tempo, beats = librosa.beat.beat_track(y=audio, sr=sample_rate, units="time")
                result = {"beats": [float(value) for value in beats], "bpm": float(np.asarray(tempo).reshape(-1)[0]),
                          "source": "librosa", "source_sha256": fingerprint}
            except Exception:
                result = {"beats": [], "source": "fixed_rhythm", "source_sha256": fingerprint,
                          "warning": "配乐节拍分析不可用，已使用模板固定节奏"}
        _cache_write(cache_path, result)
        return result
