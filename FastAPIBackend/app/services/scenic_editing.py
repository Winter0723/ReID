"""Media preflight and deterministic, editable Scenic story plans.

The planner never changes a visit segment or ORM row.  Its JSON is a snapshot:
the renderer consumes the frozen bytes fingerprint, not the current search result.
Identity similarity is deliberately not a component of visual quality ranking.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from threading import BoundedSemaphore

import cv2
import numpy as np
from filelock import FileLock
from PIL import Image, ImageOps

import scenic_database as database
from app.core.config import get_settings
from app.services.ffmpeg import ffmpeg_executable
from app.services.storage import storage


ANALYSIS_VERSION = 2
MAX_TIMELINE_CLIPS = 100
_ANALYSIS_SLOT = BoundedSemaphore(1)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


class EditingError(ValueError):
    """An actionable, safe-to-display draft/preflight failure."""


def source_sha256(path: Path) -> str:
    path = Path(path)
    marker = path.with_suffix(path.suffix + '.sealed')
    # Only capture-owned, sealed aliases can reuse a fingerprint. Include the
    # inode and change time so even same-size edits with restored mtime invalidate
    # it; ordinary uploaded sources continue to be hashed on every validation.
    # Windows st_ctime is creation time, so it cannot validate this cache.
    if os.name == 'posix' and marker.is_file():
        try:
            sealed = int(marker.read_text(encoding='utf-8')) == path.stat().st_size
        except (ValueError, OSError):
            sealed = False
        if sealed:
            cache = path.with_suffix(path.suffix + '.sha256.json')
            with FileLock(str(cache) + '.lock', timeout=600):
                before = path.stat()
                signature = [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns]
                cached = _cache_read(cache)
                if cached and cached.get('stat') == signature:
                    return cached['sha256']
                digest = _hash_source(path)
                after = path.stat()
                if signature != [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns]:
                    raise EditingError('源素材在校验期间发生变化，请重试')
                _cache_write(cache, {'stat': signature, 'sha256': digest})
                return digest
    return _hash_source(path)


def _hash_source(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_root() -> Path:
    directory = get_settings().object_storage_dir / ".scenic_analysis"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_read(path: Path) -> dict | None:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def _cache_write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _ffprobe() -> str:
    probe = shutil.which("ffprobe")
    if probe:
        return probe
    ffmpeg = ffmpeg_executable()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix == ".exe" else "ffprobe")
        if sibling.is_file():
            return str(sibling)
    raise EditingError("增强剪辑需要 FFprobe，请安装 FFmpeg 完整工具包并加入 PATH")


def _finite(value, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def frame_quality(frame: np.ndarray) -> dict:
    """Cheap optical measurements; they say nothing about a person's identity."""
    height, width = frame.shape[:2]
    thumb = cv2.resize(frame, (max(1, int(width * min(1.0, 640 / width))), max(1, int(height * min(1.0, 640 / width)))))
    gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    luminance = float(gray.mean()) / 255.0
    clipped = float(np.mean((gray < 8) | (gray > 247)))
    exposure = max(0.0, 1.0 - abs(luminance - 0.5) * 1.5 - clipped * 0.4)
    clarity = min(1.0, math.log1p(sharpness) / math.log1p(500.0))
    small = cv2.resize(gray, (9, 8))
    dhash = np.packbits(small[:, 1:] > small[:, :-1]).tobytes().hex()
    return {"sharpness": round(sharpness, 3), "exposure": round(exposure, 4),
            "luminance": round(luminance, 4), "score": round(clarity * 0.65 + exposure * 0.35, 4),
            "perceptual_hash": dhash}


def _video_frames(path: Path, times: list[float]) -> list[tuple[float, np.ndarray]]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise EditingError("视频无法解码")
    frames = []
    try:
        for seconds in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise EditingError(f"视频在 {seconds:.2f} 秒附近无法解码")
            frames.append((seconds, frame))
    finally:
        capture.release()
    return frames


def analyze_media(path: Path, kind: str | None = None, expected_sha256: str | None = None) -> dict:
    """Probe actual media bytes and cache by SHA256 and analysis version.

    Ordinary cache hits still hash the source. Sealed capture aliases use a
    capture-owned fingerprint tied to inode, size, modification and change time.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise EditingError("源素材不存在或为空")
    fingerprint = source_sha256(path)
    if expected_sha256 and fingerprint != expected_sha256:
        raise EditingError("源素材内容已改变，请重新分析；锁定或已提交版本不能替换素材")
    kind = "image" if kind == "image" or path.suffix.lower() in _IMAGE_SUFFIXES else "video"
    cache_path = _cache_root() / f"media-v{ANALYSIS_VERSION}-{fingerprint}-{kind}.json"
    with FileLock(str(cache_path) + ".lock", timeout=120):
        cached = _cache_read(cache_path)
        if cached:
            return cached
        with _ANALYSIS_SLOT, FileLock(str(_cache_root() / "analysis.slot.lock"), timeout=600):
            try:
                if kind == "image":
                    with Image.open(path) as original:
                        if original.width * original.height > 20_000_000:
                            raise EditingError("图片分辨率不能超过 20 百万像素")
                        original.verify()
                    with Image.open(path) as original:
                        image = ImageOps.exif_transpose(original).convert("RGB")
                        width, height = image.size
                        frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
                    result = {"kind": kind, "duration_seconds": 0.0, "width": width, "height": height,
                              "rotation": 0, "fps": 0.0, "has_audio": False, "quality": frame_quality(frame)}
                else:
                    command = [_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
                    probe = subprocess.run(command, capture_output=True, timeout=60, check=False)
                    if probe.returncode:
                        raise EditingError("FFprobe 无法识别素材，文件可能损坏")
                    metadata = json.loads(probe.stdout)
                    streams = metadata.get("streams", [])
                    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
                    if video is None:
                        raise EditingError("素材没有视频画面")
                    duration = _finite(video.get("duration"), _finite(metadata.get("format", {}).get("duration")))
                    if duration <= 0:
                        raise EditingError("无法读取视频真实时长")
                    try:
                        fps = float(Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"))
                    except (ValueError, ZeroDivisionError):
                        fps = 0.0
                    try:
                        pixel_aspect = float(Fraction(str(video.get("sample_aspect_ratio") or "1:1").replace(":", "/")))
                        if not math.isfinite(pixel_aspect) or pixel_aspect <= 0:
                            pixel_aspect = 1.0
                    except (ValueError, ZeroDivisionError):
                        pixel_aspect = 1.0
                    rotation = _finite(video.get("tags", {}).get("rotate"))
                    for side in video.get("side_data_list", []):
                        if "rotation" in side:
                            rotation = _finite(side["rotation"])
                    times = sorted(set([0.0, duration * 0.5, max(0.0, duration - max(0.15, 2 / max(1.0, fps)))]))
                    frames = _video_frames(path, times)
                    qualities = [(seconds, frame_quality(frame)) for seconds, frame in frames]
                    peak, best = max(qualities, key=lambda item: item[1]["score"])
                    height, width = frames[0][1].shape[:2]
                    result = {"kind": kind, "duration_seconds": round(duration, 4), "width": width, "height": height,
                              "encoded_width": int(video.get("width") or width), "encoded_height": int(video.get("height") or height),
                              "rotation": rotation, "fps": round(fps, 5), "sample_aspect_ratio": pixel_aspect,
                              "has_audio": any(s.get("codec_type") == "audio" for s in streams),
                              "quality": {**best, "peak_seconds": round(peak, 4)}}
                result.update({"source_sha256": fingerprint, "analysis_version": ANALYSIS_VERSION, "decodable": True})
            except EditingError:
                raise
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                raise EditingError(f"素材预检失败：{exc}") from exc
            _cache_write(cache_path, result)
            return result


def analyze_window(path: Path, analysis: dict, start: float, end: float) -> dict:
    """Score only the candidate window, not unrelated portions of a long recording."""
    if analysis["kind"] == "image":
        return dict(analysis["quality"])
    cache_path = _cache_root() / f"window-v{ANALYSIS_VERSION}-{analysis['source_sha256']}-{start:.3f}-{end:.3f}.json"
    with FileLock(str(cache_path) + ".lock", timeout=120):
        cached = _cache_read(cache_path)
        if cached:
            return cached
        with _ANALYSIS_SLOT, FileLock(str(_cache_root() / "analysis.slot.lock"), timeout=600):
            times = sorted(set([start, (start + end) / 2, max(start, end - min(0.15, (end - start) / 2))]))
            frames = _video_frames(path, times)
            qualities = [(seconds, frame_quality(frame)) for seconds, frame in frames]
            peak, best = max(qualities, key=lambda item: item[1]["score"])
            result = {**best, "peak_seconds": round(peak, 4), "sample_count": len(qualities)}
            frame = next(frame for seconds, frame in frames if seconds == peak)
            _write_thumbnail(frame, cache_path.with_suffix(".jpg"))
            _cache_write(cache_path, result)
            return result


def _write_thumbnail(frame: np.ndarray, path: Path) -> None:
    height, width = frame.shape[:2]
    scale = min(1.0, 960 / max(height, width))
    if scale < 1:
        frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise EditingError("无法生成素材完整画面预览")
    temporary = path.with_suffix(".jpg.tmp")
    temporary.write_bytes(encoded.tobytes())
    temporary.replace(path)


def candidate_thumbnail(path: Path, analysis: dict, start: float, end: float, quality: dict,
                        project_id: int, media_id: int, original_key: str) -> str:
    """Persist a full-frame staff thumbnail so normalized focus uses source coordinates.

    Match thumbnails remain the identity reference. This separate asset survives
    rematching and shares the media's Scenic storage namespace and retention.
    """
    if analysis["kind"] == "image":
        return original_key
    stem = f"window-v{ANALYSIS_VERSION}-{analysis['source_sha256']}-{start:.3f}-{end:.3f}"
    image_path = _cache_root() / f"{stem}.jpg"
    manifest_path = _cache_root() / f"thumbnail-{project_id}-{media_id}-{stem}.json"
    with FileLock(str(manifest_path) + ".lock", timeout=120):
        cached = _cache_read(manifest_path)
        if cached and storage.path_for(cached["object_key"]).is_file():
            return cached["object_key"]
        if not image_path.is_file():
            peak = min(end - 0.05, max(start, _finite(quality.get("peak_seconds"), (start + end) / 2)))
            _write_thumbnail(_video_frames(path, [peak])[0][1], image_path)
        stored = storage.save_path(f"scenic/{project_id}", image_path, "editing-frame.jpg", category=f"media/{media_id}/editing-thumbnails")
        _cache_write(manifest_path, {"object_key": stored.object_key})
        return stored.object_key


def verify_sources(sources: list[dict]) -> None:
    """Fail closed when an immutable render snapshot no longer resolves."""
    verified = set()
    for clip in sources:
        key, expected = clip.get("object_key", ""), clip.get("source_sha256", "")
        if not key or not expected:
            raise EditingError("剪辑快照缺少源素材指纹，请重新生成草稿")
        if (key, expected) in verified:
            continue
        try:
            path = storage.path_for(key)
            if not path.is_file() or source_sha256(path) != expected:
                raise EditingError(f"素材 {clip.get('id', '')} 丢失或内容改变，请重新生成草稿")
        except OSError as exc:
            raise EditingError(f"素材 {clip.get('id', '')} 无法读取") from exc
        verified.add((key, expected))


def _date(value) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        date = value
    elif isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        try:
            date = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return date.replace(tzinfo=dt.UTC) if date.tzinfo is None else date.astimezone(dt.UTC)


def _recorded_at(segment, media, metadata, start: float) -> tuple[str | None, str]:
    annotated = _date(getattr(metadata, "recorded_at", None))
    if annotated:
        return (annotated + dt.timedelta(seconds=start if media.media_type != "image" else 0)).isoformat(), "user"
    if media.media_type == "camera":
        # Live recordings can run at a different effective frame rate from
        # their encoded FPS. Anchor video offsets to the actual captured hit,
        # rather than treating video seconds as elapsed wall-clock seconds.
        captured = _date(getattr(segment, "timestamp", None))
        target = getattr(segment, "target", None) or segment
        info = str(getattr(target, "source_info", "") or "")
        seek = re.search(r"video_seek_seconds=([0-9]+(?:\.[0-9]+)?)", info)
        if captured and seek:
            return (captured + dt.timedelta(seconds=start - float(seek.group(1)))).isoformat(), "camera_hit"
        # Media.created_at is set to the central recording start when sealed.
        recorded = _date(media.created_at)
        if recorded:
            return (recorded + dt.timedelta(seconds=start)).isoformat(), "camera"
        # A real capture timestamp is preferable to inventing a date from seek time.
        recorded = _date(getattr(segment, "timestamp", None))
        if recorded:
            return recorded.isoformat(), "camera_hit"
    return None, "unknown"


def _overlap(first: dict, second: dict) -> bool:
    if first["source_sha256"] != second["source_sha256"]:
        return False
    return first["kind"] == "image" or min(first["end_seconds"], second["end_seconds"]) > max(first["start_seconds"], second["start_seconds"])


def merge_candidates(candidates: list[dict]) -> list[dict]:
    """Union overlapping intervals of identical media, never change locked edits."""
    result: list[dict] = []
    for candidate in sorted(copy.deepcopy(candidates), key=lambda clip: (not clip.get("locked", False), clip.get("sort_order", 0), clip["start_seconds"])):
        overlaps = [clip for clip in result if _overlap(clip, candidate)]
        if overlaps and any(clip.get("locked") for clip in overlaps):
            continue
        if overlaps:
            base = min(overlaps, key=lambda clip: clip.get("sort_order", 0))
            all_clips = overlaps + [candidate]
            earliest = min(all_clips, key=lambda clip: clip["start_seconds"])
            base["start_seconds"] = min(clip["start_seconds"] for clip in all_clips)
            base["end_seconds"] = max(clip["end_seconds"] for clip in all_clips)
            base["available_start_seconds"] = base["start_seconds"]
            base["available_end_seconds"] = base["end_seconds"]
            base["recorded_at"] = earliest.get("recorded_at")
            base["quality"] = max(all_clips, key=lambda clip: clip["quality"]["score"])["quality"]
            base["segment_ids"] = sorted({identifier for clip in all_clips for identifier in clip.get("segment_ids", [])})
            # An old trajectory does not cover a newly unioned interval.
            base.pop("trajectory", None)
            base.pop("tracking_start_seconds", None)
            base.pop("tracking_end_seconds", None)
            if base.get("crop_mode") == "track":
                base["crop_mode"] = "fit"
            result = [clip for clip in result if clip is base or clip not in overlaps]
        else:
            result.append(candidate)
    # Sort known-time slots chronologically; unknown slots keep manual order.
    result.sort(key=lambda clip: (clip.get("sort_order", 0), clip["id"]))
    known = iter(sorted([clip for clip in result if clip.get("recorded_at")], key=lambda clip: clip["recorded_at"]))
    return [next(known) if clip.get("recorded_at") else clip for clip in result]


def _clip_duration(clip: dict) -> float:
    return max(0.0, _finite(clip.get("end_seconds")) - _finite(clip.get("start_seconds")))


def _exclusion_source(clip: dict) -> dict:
    """Use the candidate's original coverage, not its later automatic trim."""
    return {"source_sha256": clip["source_sha256"], "kind": clip["kind"],
            "start_seconds": clip.get("available_start_seconds", clip["start_seconds"]),
            "end_seconds": clip.get("available_end_seconds", clip["end_seconds"])}


def _variant_exclusions(previous: dict | None, sources: dict[str, dict], variant: str) -> list[dict]:
    """Persist explicit omissions across rematches and absent candidate rounds.

    Unselected candidates are not omissions. Byte identity and source coverage
    survive recreated segment IDs, while each format retains its own decisions.
    Included timeline entries explicitly restore a previously omitted candidate.
    """
    previous = previous or {}
    exclusions = copy.deepcopy(previous.get("exclusions", {}).get(variant, []))
    clips = previous.get("variants", {}).get(variant, [])
    restored = [_exclusion_source(sources.get(clip["id"], clip)) for clip in clips if clip.get("included", True)]
    exclusions = [item for item in exclusions if not any(_overlap(item, source) for source in restored)]
    for clip in clips:
        if clip.get("included", True):
            continue
        source = _exclusion_source(sources.get(clip["id"], clip))
        # Keep the original exclusion anchor when a rematch creates a larger
        # overlapping candidate, instead of expanding that anchor every round.
        if not any(_overlap(source, item) for item in exclusions):
            exclusions.append(source)
    return exclusions


def plan_variant(sources: list[dict], template: dict, target_seconds: float, variant: str,
                 locked_clips: list[dict] | None = None, beat_seconds: list[float] | None = None) -> list[dict]:
    from app.services.scenic_templates import timeline_duration
    pins = {c["id"]: c for c in (locked_clips or []) if c.get("included", True)}
    pins.update({c["id"]: c for c in sources if c.get("locked") and c.get("included", True) and c["id"] not in pins})
    if timeline_duration(list(pins.values()), template) > target_seconds + 0.02:
        raise EditingError(f"{variant} 锁定镜头超过目标时长，请调整目标或解除锁定")
    budget = max(target_seconds, sum(_clip_duration(c) for c in pins.values()))
    previous = None
    for _ in range(len(sources) + 3):
        result = _plan_variant_raw(sources, template, budget, variant, locked_clips, beat_seconds)
        actual = timeline_duration(result, template)
        if abs(target_seconds - actual) <= 0.02 or result == previous:
            return result
        if actual > target_seconds:
            # Beat alignment can grow discontinuously; keep the final tail in budget.
            for clip in reversed(result):
                if clip["id"] in pins:
                    continue
                excess = timeline_duration(result, template) - target_seconds
                if excess <= 0.02:
                    break
                reduction = min(_clip_duration(clip) - 0.3, excess * 1.5)
                clip["end_seconds"] -= max(0, reduction)
            return result
        previous = result
        budget += target_seconds - actual
    return result


def _plan_variant_raw(sources: list[dict], template: dict, target_seconds: float, variant: str,
                 locked_clips: list[dict] | None = None, beat_seconds: list[float] | None = None) -> list[dict]:
    """Select without replacement, reserve pinned durations, then compile a story."""
    locked = {clip["id"]: copy.deepcopy(clip) for clip in (locked_clips or []) if clip.get("included", True)}
    for clip in sources:
        if clip.get("locked") and clip.get("included", True) and clip["id"] not in locked:
            locked[clip["id"]] = copy.deepcopy(clip)
    if len(locked) > MAX_TIMELINE_CLIPS:
        raise EditingError(f"{variant} 锁定镜头超过 {MAX_TIMELINE_CLIPS} 个，请解除部分锁定后重新编排")
    reserved = sum(_clip_duration(clip) for clip in locked.values())
    if reserved > target_seconds + 0.02:
        raise EditingError(f"{variant} 锁定镜头共 {reserved:.1f} 秒，超过目标 {target_seconds:.0f} 秒，请调整目标或解除锁定")
    minimum, maximum = float(template["min_clip_seconds"]), float(template["max_clip_seconds"])
    if variant == "portrait":
        maximum = max(minimum, maximum * 0.78)
    pool = [clip for clip in sources if clip.get("included", True) and clip["id"] not in locked and _clip_duration(clip) >= 0.3]
    chosen = list(locked.values())
    capacities = {clip["id"]: min(maximum, _clip_duration(clip)) for clip in pool}
    budget = max(0.0, target_seconds - reserved)
    coverage: dict[str, int] = {}
    for clip in chosen:
        place = clip.get("camera_name") or str(clip.get("media_id"))
        coverage[place] = coverage.get(place, 0) + 1
    selected_capacity = 0.0
    while pool and selected_capacity < budget - 0.03 and len(chosen) < MAX_TIMELINE_CLIPS:
        def score(clip):
            place = clip.get("camera_name") or str(clip.get("media_id"))
            novelty = 0.25 / (1 + coverage.get(place, 0))
            memory_photo = 0.16 if template["id"] == "memory" and clip["kind"] == "image" else 0.0
            return _finite(clip["quality"].get("score")) + novelty + memory_photo
        # Take one usable view per location before stacking that same view.
        # Very poor frames do not win solely because their camera is new.
        novel = [clip for clip in pool if not coverage.get(clip.get("camera_name") or str(clip.get("media_id")), 0)
                 and _finite(clip["quality"].get("score")) >= 0.25]
        best = max(novel or pool, key=score)
        pool.remove(best)
        if budget - selected_capacity < 0.3 and chosen:
            break
        chosen.append(copy.deepcopy(best))
        selected_capacity += capacities[best["id"]]
        place = best.get("camera_name") or str(best.get("media_id"))
        coverage[place] = coverage.get(place, 0) + 1
    if not chosen:
        raise EditingError("没有可用于成片的有效片段")
    order = {clip["id"]: index for index, clip in enumerate(sources)}
    chosen.sort(key=lambda clip: order.get(clip["id"], len(order)))
    # An opening highlight is used once; the rest remain in visit/manual order.
    highlight_candidates = [clip for clip in chosen if clip["id"] not in locked]
    if len(chosen) > 1 and highlight_candidates:
        highlight = max(highlight_candidates, key=lambda clip: _finite(clip["quality"].get("score")))
        chosen.remove(highlight)
        chosen.insert(0, highlight)
    automatic = [clip for clip in chosen if clip["id"] not in locked]
    # Allocate whole budget among chosen clips before applying optional beat cuts.
    # Reserve a renderable minimum before the preferred holds: source order
    # can differ from quality ranking, leaving the final cut too short.
    allocations = {clip["id"]: min(0.3, capacities[clip["id"]]) for clip in automatic}
    for clip in automatic:
        desired = min(minimum, capacities[clip["id"]]) - allocations[clip["id"]]
        allocations[clip["id"]] += min(desired, max(0, budget - sum(allocations.values())))
    remaining = budget - sum(allocations.values())
    for _ in range(len(automatic) + 1):
        growable = [clip for clip in automatic if capacities[clip["id"]] - allocations[clip["id"]] > 0.001]
        if remaining <= 0.001 or not growable:
            break
        share = remaining / len(growable)
        for clip in growable:
            addition = min(share, capacities[clip["id"]] - allocations[clip["id"]])
            allocations[clip["id"]] += addition
            remaining -= addition
    elapsed = 0.0
    for clip in chosen:
        if clip["id"] not in locked:
            duration = allocations[clip["id"]]
            if template["id"] == "upbeat" and beat_seconds:
                nearby = [beat - elapsed for beat in beat_seconds if minimum <= beat - elapsed <= duration and duration - (beat - elapsed) <= 0.45]
                if nearby:
                    duration = max(nearby)
            available_start, available_end = clip["start_seconds"], clip["end_seconds"]
            peak = _finite(clip["quality"].get("peak_seconds"), (available_start + available_end) / 2)
            clip["start_seconds"] = round(max(available_start, min(peak - duration / 2, available_end - duration)), 4)
            clip["end_seconds"] = round(clip["start_seconds"] + duration, 4)
            if clip["kind"] == "image":
                clip["start_seconds"], clip["end_seconds"] = 0.0, round(duration, 4)
            clip["locked"] = False
        if variant == "landscape" and clip.get("crop_mode") == "track" and clip["id"] not in locked:
            clip["crop_mode"] = "fit"
        clip["included"] = True
        elapsed += _clip_duration(clip)
    return chosen


def _bounded_config(config: dict, name: str, default: float, low: float, high: float) -> float:
    value = _finite(config.get(name, default), -1)
    if not low <= value <= high:
        raise EditingError(f"{name} 需在 {low:g}–{high:g} 之间")
    return value


def build_draft(session, db, config: dict, previous: dict | None = None) -> dict:
    """Build a dual-format story from included, authorized visit segments."""
    from app.services.scenic_templates import TEMPLATES

    settings = {**{key: value for key, value in (previous or {}).items() if key not in {"sources", "variants", "warnings"}}, **config}
    template_id = settings.get("template_id", "documentary")
    if template_id not in TEMPLATES:
        raise EditingError("未知剪辑模板")
    template = TEMPLATES[template_id]
    landscape = _bounded_config(settings, "landscape_seconds", 75, 60, 90)
    portrait = _bounded_config(settings, "portrait_seconds", 45, 30, 60)
    music_volume = _bounded_config(settings, "music_volume", 0.25, 0, 1)
    original_volume = _bounded_config(settings, "original_volume", 1, 0, 1)
    warnings: list[str] = []
    candidates: list[dict] = []
    previous_sources = {clip["id"]: clip for clip in (previous or {}).get("sources", [])}
    pins_by_variant = {name: [clip for clip in (previous or {}).get("variants", {}).get(name, []) if clip.get("locked") and clip.get("included", True)]
                       for name in ("landscape", "portrait")}
    exclusions = {name: _variant_exclusions(previous, previous_sources, name) for name in ("landscape", "portrait")}
    pinned_ids = {clip["id"] for clips in pins_by_variant.values() for clip in clips}
    pinned_ids.update(clip["id"] for clip in previous_sources.values() if clip.get("locked") and clip.get("included", True))
    # A rematch may delete old segment IDs; retained locks resolve the saved media.
    for clip_id in sorted(pinned_ids):
        original = previous_sources.get(clip_id)
        if not original:
            raise EditingError("锁定镜头缺少原始素材记录，请重新选择素材")
        media = db.get(database.MediaFile, original.get("media_id"))
        if media is None or media.project_id != session.project_id:
            raise EditingError(f"锁定镜头 {clip_id} 的源素材已移除")
        verify_sources([original])
        retained = copy.deepcopy(original)
        retained["_pinned_source"] = True
        candidates.append(retained)
    segments = db.query(database.TouristVisitSegment).filter(
        database.TouristVisitSegment.session_id == session.id,
        database.TouristVisitSegment.project_id == session.project_id,
        database.TouristVisitSegment.included.is_(True),
    ).order_by(database.TouristVisitSegment.sort_order, database.TouristVisitSegment.id).all()
    metadata_type = getattr(database, "ScenicMediaMetadata", None)
    metadata_by_id = {}
    analyzed_sources: dict[tuple[str, str], dict] = {}
    if metadata_type is not None:
        media_ids = [segment.media_id for segment in segments if segment.media_id is not None]
        if media_ids:
            metadata_by_id = {row.media_id: row for row in db.query(metadata_type).filter(metadata_type.media_id.in_(media_ids)).all()}
    for segment in segments:
        media = db.get(database.MediaFile, segment.media_id) if segment.media_id else None
        try:
            if not media or media.project_id != session.project_id:
                raise EditingError("源素材已移除")
            key = segment.original_media_object_key or media.object_key
            if key != media.object_key:
                raise EditingError("素材引用已改变，请重新匹配")
            path = storage.path_for(key)
            media_kind = "image" if media.media_type == "image" else "video"
            cache_key = (key, media_kind)
            if cache_key not in analyzed_sources:
                analyzed_sources[cache_key] = analyze_media(path, media_kind)
            analysis = analyzed_sources[cache_key]
            kind = analysis["kind"]
            start = 0.0 if kind == "image" else max(0.0, _finite(segment.start_seconds))
            end = float(template["max_clip_seconds"]) if kind == "image" else min(analysis["duration_seconds"], _finite(segment.end_seconds))
            if end - start < 0.3:
                raise EditingError("片段时间超出素材范围或有效时长不足")
            metadata = metadata_by_id.get(media.id)
            recorded_at, time_source = _recorded_at(segment, media, metadata, start)
            quality = analyze_window(path, analysis, start, end)
            thumbnail_key = candidate_thumbnail(path, analysis, start, end, quality, session.project_id, media.id, key)
            # SQLite may reuse segment IDs after rematching deletes old rows.
            # Include media bytes and time bounds so a retained lock cannot hide
            # a new, unrelated candidate that happened to reuse the row ID.
            clip_id = f"s-{segment.id}-{analysis['source_sha256'][:12]}-{round(start * 1000)}-{round(end * 1000)}"
            clip = {"id": clip_id, "segment_id": segment.id, "segment_ids": [segment.id],
                    "media_id": media.id, "project_id": session.project_id, "object_key": key, "source_sha256": analysis["source_sha256"],
                    "kind": kind, "start_seconds": start, "end_seconds": end,
                    "available_start_seconds": start, "available_end_seconds": end,
                    "duration_seconds": analysis["duration_seconds"], "width": analysis["width"], "height": analysis["height"],
                    "rotation": analysis["rotation"], "fps": analysis["fps"], "has_audio": analysis["has_audio"],
                    "sample_aspect_ratio": analysis.get("sample_aspect_ratio", 1.0),
                    "camera_name": (getattr(metadata, "location", "") or segment.camera_name or ""),
                    "location_name": getattr(metadata, "location", "") or "",
                    "recorded_at": recorded_at, "recorded_at_source": time_source,
                    "thumbnail_object_key": thumbnail_key,
                    "identity_thumbnail_object_key": segment.thumbnail_object_key or "",
                    "thumbnail_url": f"/api/v1/scenic/segments/{segment.id}/thumbnail",
                    "quality": quality, "identity_similarity": _finite(segment.similarity),
                    "locked": False, "included": True, "keep_audio": media.media_type != "camera" and analysis["has_audio"],
                    "focus_x": 0.5, "focus_y": 0.5, "crop_mode": "fit", "title": "",
                    "sort_order": segment.sort_order, "analysis_version": ANALYSIS_VERSION}
            if any(_overlap(clip, old) for old in candidates if old.get("_pinned_source")):
                continue
            if any(old["id"] == clip["id"] for old in candidates):
                continue
            candidates.append(clip)
        except (EditingError, OSError) as exc:
            warnings.append(f"已排除片段 {segment.id}：{exc}")
    sources = merge_candidates(candidates)
    for source in sources:
        source.pop("_pinned_source", None)
    if not sources:
        raise EditingError("没有有效素材可生成草稿。" + "；".join(warnings[:5]))
    if any(not clip.get("recorded_at") for clip in sources):
        warnings.append("部分上传素材没有拍摄时间，已保留其人工顺序；请检查游览顺序或补充拍摄时间")
    requested_tracking = bool(settings.get("use_tracking", False))
    if requested_tracking:
        from app.services.scenic_tracking import analyze_candidate, tracking_enabled
        if not tracking_enabled():
            warnings.append("本地人物跟踪尚未启用，使用保留完整画面的安全构图")
        else:
            segment_by_id = {segment.id: segment for segment in segments}
            for clip in sources:
                if clip["kind"] != "video" or clip["id"] in pinned_ids:
                    continue
                segment = segment_by_id.get(clip["segment_id"])
                try:
                    # Overlap unions can span minutes of one recording. Refine
                    # only the intended shot neighbourhood, not that entire union.
                    tracking_input = copy.deepcopy(clip)
                    window_seconds = max(12.0, float(template["max_clip_seconds"]) * 2)
                    if _clip_duration(clip) > window_seconds:
                        center = _finite(clip["quality"].get("peak_seconds"), (clip["start_seconds"] + clip["end_seconds"]) / 2)
                        tracking_input["start_seconds"] = max(clip["start_seconds"], min(center - window_seconds / 2, clip["end_seconds"] - window_seconds))
                        tracking_input["end_seconds"] = tracking_input["start_seconds"] + window_seconds
                    tracking = analyze_candidate(tracking_input, session, segment)
                    if tracking.get("reliable"):
                        clip.update({"trajectory": tracking["trajectory"], "crop_mode": "track"})
                        clip["quality"].update({key: value for key, value in tracking.items() if key in {"visible_fraction", "person_area", "continuous_seconds"}})
                        clip["quality"]["score"] = round(0.75 * clip["quality"]["score"] + 0.25 * tracking["visible_fraction"], 4)
                        clip["start_seconds"] = max(clip["start_seconds"], tracking["start_seconds"])
                        clip["end_seconds"] = min(clip["end_seconds"], tracking["end_seconds"])
                        # Confirmed track coverage is narrower than the original
                        # candidate range available for manual full-frame edits.
                        clip["tracking_start_seconds"] = clip["start_seconds"]
                        clip["tracking_end_seconds"] = clip["end_seconds"]
                    else:
                        warnings.append(f"片段 {clip['segment_id']}：{tracking.get('reason', '轨迹不可靠')}，使用完整画面")
                except EditingError as exc:
                    warnings.append(f"片段 {clip['segment_id']}：{exc}，使用完整画面")
                except Exception as exc:
                    warnings.append(f"片段 {clip['segment_id']} 人物跟踪未完成（{type(exc).__name__}），使用完整画面")
    music_id = settings.get("music_id", template.get("music_id", ""))
    if music_id == "auto" or music_id is None:
        music_id = template.get("music_id", "")
    if music_id == "none":
        music_id = ""
    if "music_id" not in config and previous and previous.get("template_id") != template_id:
        music_id = template.get("music_id", "")
    beats = None
    if template_id == "upbeat" and music_id:
        from app.services.scenic_tracking import analyze_beats
        from app.services.scenic_templates import music_path
        beat_result = analyze_beats(music_path(music_id))
        beats = beat_result.get("beats")
        if beat_result.get("warning"):
            warnings.append(beat_result["warning"])
    variants = {}
    for name, target in (("landscape", landscape), ("portrait", portrait)):
        pins = {clip["id"] for clip in pins_by_variant[name]}
        excluded = [clip for clip in sources if clip["id"] not in pins and not clip.get("locked")
                    and any(_overlap(_exclusion_source(clip), item) for item in exclusions[name])]
        excluded_ids = {clip["id"] for clip in excluded}
        eligible = [clip for clip in sources if clip["id"] not in excluded_ids]
        label = "横版" if name == "landscape" else "竖版"
        if not eligible and not pins_by_variant[name]:
            raise EditingError(f"{label}素材已全部排除，请恢复至少一个镜头或重新匹配新素材")
        variants[name] = plan_variant(eligible, template, target, name, pins_by_variant[name], beats)
        # Keep display entries within the same clip/count limits as saved edits.
        # Exclusion anchors retain the complete source coverage independently.
        visible_excluded = excluded[:max(0, MAX_TIMELINE_CLIPS - len(variants[name]))]
        for clip in visible_excluded:
            display = plan_variant([clip], template, 60, name)[0]
            display.update(included=False, locked=False)
            variants[name].append(display)
        if excluded:
            warnings.append(f"{label}已保留 {len(excluded)} 个镜头的人工排除设置")
        if len(visible_excluded) < len(excluded):
            warnings.append(f"{label}时间线最多展示 100 个镜头，其余 {len(excluded) - len(visible_excluded)} 个镜头的排除设置仍已保留")
        if len(eligible) > MAX_TIMELINE_CLIPS and len([clip for clip in variants[name] if clip.get("included", True)]) == MAX_TIMELINE_CLIPS:
            warnings.append(f"{label}已精选 {MAX_TIMELINE_CLIPS} 个镜头达到时间线上限，其余素材保留在素材区")
    for name, clips in variants.items():
        from app.services.scenic_templates import timeline_duration
        actual = timeline_duration(clips, template)
        target = landscape if name == "landscape" else portrait
        if actual < target - 0.5:
            warnings.append(f"{'横版' if name == 'landscape' else '竖版'}可用镜头编排约 {actual:.1f} 秒，素材不足时不重复填充")
    all_photos = all(clip["kind"] == "image" for clip in sources)
    title = str(settings.get("title") or f"{session.visitor_label or '我的'} · 游览回忆")[:80]
    cover = settings.get("cover_clip_id")
    if cover not in {clip["id"] for clip in sources}:
        cover = variants["landscape"][0]["id"]
    return {"schema_version": 1, "analysis_version": ANALYSIS_VERSION, "template_id": template_id,
            "template_version": template["version"], "title": title,
            "brand": str(settings.get("brand") or getattr(session.project, "name", ""))[:40],
            "music_id": music_id, "music_volume": music_volume, "original_volume": original_volume,
            "landscape_seconds": landscape, "portrait_seconds": portrait, "use_tracking": requested_tracking,
            "sources": sources, "variants": variants, "exclusions": exclusions, "warnings": list(dict.fromkeys(warnings)),
            "cover_clip_id": cover, "result_type": "photo_memory" if all_photos else "travel_film"}
