import csv
import datetime
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import secrets
import shutil
import smtplib
import subprocess
import time
import uuid
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import List

import cv2
import torch
from PIL import Image, ImageOps
from filelock import FileLock, Timeout
from fastapi import Depends, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer, OAuth2PasswordRequestForm
import jwt
from jwt.exceptions import InvalidTokenError as JWTError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
import database
from app.core.config import PRODUCTION_LIKE_ENVIRONMENTS, get_settings
from app.core.security import create_token, decode_token, get_password_hash, verify_password
from app.schemas import (
    CameraStartRequest,
    EmailCodeLoginRequest,
    EmailCodeRequest,
    JobOut,
    LoginRequest,
    PasswordChangeRequest,
    ProjectMemberOut,
    ProjectMemberUpsert,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    SearchResponse,
    ScenicCameraPointCreate,
    ScenicCameraPointUpdate,
    ScenicOrderCreate,
    ScenicOrderPaymentResult,
    TextSearchRequest,
    TokenResponse,
    TouristMatchRequest,
    TouristSegmentBulkUpdate,
    TouristSegmentUpdate,
    UserAdminCreate,
    UserAdminUpdate,
    UserCreate as V1UserCreate,
    UserOut,
    UserPasswordReset,
)
from app.services.camera_stream import (
    camera_stream_manager,
    list_camera_devices,
    solidify_project_camera_recordings,
)
from app.services.audit_service import add_audit_log
from app.services.persistence_service import persist_hit_result
from app.services.central_camera_service import find_camera_service
from app.services.job_manager import LocalJobFallbackDisabled, job_manager
from app.services.ffmpeg import ffmpeg_executable
from app.services.asset_delivery import has_valid_asset_signature, signed_asset_url
from app.services.model_runtime import clip_image_feature, clip_text_feature, detect_people
from app.services.storage import (
    copy_upload_limited,
    InvalidObjectKeyError,
    UploadTooLargeError,
    read_upload_limited,
    storage,
)
from app.services.vector_store import VectorPoint, vector_store

GUEST_USAGE = {}
ACTIVE_SESSIONS = {}
BASE_GALLERY_DIR = str(get_settings().gallery_dir)
logger = logging.getLogger("myreid.api")

SECRET_KEY = get_settings().secret_key
ALGORITHM = get_settings().algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = get_settings().access_token_expire_minutes
_VIDEO_PREVIEW_ENCODER_SUPPORT = {}
_VIDEO_SEEK_SECONDS_RE = re.compile(r"video_seek_seconds\s*=\s*([0-9]+(?:\.[0-9]+)?)")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)
api_bearer = HTTPBearer(auto_error=False)


def _sign_camera_frames(payload):
    from app.services.asset_delivery import signed_asset_url
    from app.services.camera_policy import public_camera_source
    for key in ("latest_frame_url",):
        url = payload.get(key, "")
        if url and url.startswith("/gallery/"):
            payload[key] = signed_asset_url(url.split("?", 1)[0])
    for key in ("camera_source", "monitor_source", "source", "camera_id"):
        if key in payload:
            payload[key] = public_camera_source(payload[key])
    return payload


def _merge_central_camera_status(payload: dict, *, camera_source: str = "0") -> dict:
    service = find_camera_service(camera_source)
    if service is None:
        payload["monitor_running"] = False
        return _sign_camera_frames(payload)
    monitor = service.status()
    public_monitor = {
        key: value
        for key, value in monitor.items()
        if key not in {"recording_path", "recording_object_key", "recording_url"}
    }
    payload["monitor_running"] = bool(monitor.get("running"))
    payload["monitor_status"] = _sign_camera_frames(public_monitor)
    payload["monitor_source"] = monitor.get("source") or camera_source or "0"
    payload["camera_source"] = payload.get("camera_source") or camera_source or monitor.get("camera_source") or "0"

    if not payload.get("latest_frame_url") and monitor.get("latest_frame_url"):
        payload["latest_frame_url"] = monitor.get("latest_frame_url")
        payload["latest_frame_version"] = monitor.get("latest_frame_version") or int(time.time())
    if not payload.get("recording_url") and payload.get("media_id") and monitor.get("recording_url"):
        payload["recording_url"] = monitor.get("recording_url")
        payload["recording_filename"] = monitor.get("recording_filename") or ""
        payload["recording_ready"] = bool(monitor.get("recording_ready"))
    if not payload.get("message"):
        payload["message"] = monitor.get("message") or "统一相机服务正在监测"
    if monitor.get("error") and not payload.get("error"):
        payload["monitor_error"] = monitor.get("error")
    return _sign_camera_frames(payload)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)

GLOBAL_ROLES = {"admin", "operator", "viewer"}
PROJECT_ROLES = {"owner", "operator", "viewer"}
PROJECT_PERMISSION_LEVELS = {"viewer": 1, "operator": 2, "owner": 3}
PROJECT_PERMISSION_MINIMUMS = {"read": 1, "write": 2, "manage": 3}
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_MIN_INTERVAL_SECONDS = 60
EMAIL_CODE_PURPOSES = {"login", "register"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_DECODED_IMAGE_PIXELS = 20_000_000
MAX_BATCH_UPLOAD_FILES = 100
IMAGE_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_UPLOAD_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}


class ImagePixelLimitError(ValueError):
    pass


def _load_verified_rgb_image(
    source,
    *,
    max_pixels: int = MAX_DECODED_IMAGE_PIXELS,
    transpose_exif: bool = True,
) -> Image.Image:
    """Verify an image before decoding and cap its uncompressed pixel count."""

    def open_image():
        if isinstance(source, (bytes, bytearray, memoryview)):
            return Image.open(io.BytesIO(bytes(source)))
        return Image.open(source)

    with open_image() as image:
        image.verify()
    with open_image() as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise ImagePixelLimitError(f"图片分辨率不能超过 {max_pixels // 1_000_000} 百万像素")
        if transpose_exif:
            image = ImageOps.exif_transpose(image)
        return image.convert("RGB")


def _media_type_for_filename(filename: str) -> str:
    extension = Path(filename or "").suffix.lower()
    if extension in VIDEO_UPLOAD_EXTENSIONS:
        return "video"
    if extension in IMAGE_UPLOAD_EXTENSIONS:
        return "image"
    raise HTTPException(status_code=400, detail=f"不支持的文件类型: {filename or '未命名文件'}")


def _delete_storage_objects(object_keys) -> None:
    for object_key in set(filter(None, object_keys)):
        try:
            storage.delete(object_key)
        except Exception:
            logger.warning("Failed to clean up object %s", object_key, exc_info=True)


def _delete_gallery_path(path: str):
    if not path:
        return
    gallery_root = os.path.abspath(BASE_GALLERY_DIR)
    absolute_path = os.path.abspath(path)
    try:
        relative_path = os.path.relpath(absolute_path, gallery_root)
    except ValueError:
        return
    if relative_path.startswith(".."):
        return
    if os.path.isfile(absolute_path):
        os.remove(absolute_path)


def _delete_media_assets(db: Session, media_rows: List[database.MediaFile]):
    media_ids = [media.id for media in media_rows]
    if not media_ids:
        return
    targets = db.query(database.DetectionTarget).filter(database.DetectionTarget.media_id.in_(media_ids)).all()
    for target in targets:
        if target.crop_object_key:
            storage.delete(target.crop_object_key)
        _delete_gallery_path(target.crop_path)
    for media in media_rows:
        storage.delete(media.object_key)
        crop_dir = os.path.join(BASE_GALLERY_DIR, f"project_{media.project_id}", f"media_{media.id}")
        if os.path.isdir(crop_dir):
            shutil.rmtree(crop_dir)
    db.query(database.DetectionTarget).filter(database.DetectionTarget.media_id.in_(media_ids)).delete(synchronize_session=False)
    db.query(database.MediaFile).filter(database.MediaFile.duplicate_of_media_id.in_(media_ids)).update(
        {"duplicate_of_media_id": None},
        synchronize_session=False,
    )
    db.query(database.ProcessingJob).filter(database.ProcessingJob.media_id.in_(media_ids)).update(
        {"media_id": None},
        synchronize_session=False,
    )


def _safe_delete_vectors_by_project(project_id: int):
    try:
        return vector_store.delete_by_project(project_id)
    except Exception as exc:
        print(f"删除项目向量失败，已跳过 project={project_id}: {exc}")
        return 0


def _safe_delete_vectors_by_media(media_id: int):
    try:
        return vector_store.delete_by_media(media_id)
    except Exception as exc:
        print(f"删除媒体向量失败，已跳过 media={media_id}: {exc}")
        return 0


def _encode_image_path_as_source(filename: str) -> str:
    if filename.startswith("[VID]_"):
        parts = filename.replace("[VID]_", "").split("_time_")
        if len(parts) == 2:
            vid_name = parts[0]
            time_and_id = parts[1].split("_p")
            time_str = time_and_id[0].replace("-", ":")
            person_id = time_and_id[1].replace(".jpg", "") if len(time_and_id) > 1 else "0"
            return f"🎬 {vid_name}\n⏱️ {time_str} (目标 {person_id})"
    if filename.startswith("[CAM]_"):
        parts = filename.replace("[CAM]_", "").split("_p")
        person_id = parts[1].replace(".jpg", "") if len(parts) > 1 else "0"
        return f"📷 Camera\n⏱️ 实时采集 (目标 {person_id})"
    return f"🖼️ {filename}"


def _public_gallery_path(path: str) -> str:
    if path.startswith("projects/"):
        return storage.url_for(path)
    gallery_root = os.path.abspath(BASE_GALLERY_DIR)
    absolute_path = os.path.abspath(path)
    try:
        relative_path = os.path.relpath(absolute_path, gallery_root)
    except ValueError:
        return path
    if relative_path.startswith(".."):
        return path
    from app.services.asset_delivery import signed_asset_url
    return signed_asset_url(f"/gallery/{relative_path.replace(os.sep, '/')}")


def _public_object_path(object_key: str = None, fallback_path: str = "") -> str:
    if object_key:
        return storage.url_for(object_key)
    return _public_gallery_path(fallback_path)


MAX_SEARCH_WINDOW = 500


def _schema_dict(model, **kwargs) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _compact_filters(filters: dict) -> dict:
    compacted = {}
    for key, value in (filters or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        compacted[key] = value.strip() if isinstance(value, str) else value
    return compacted


def _timestamp_to_seconds_for_payload(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
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


def _datetime_from_payload(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def _media_download_url(media: database.MediaFile = None, scope: str = "core"):
    if media is None:
        return None
    if media.media_type == "camera" and (not media.size_bytes or not str(media.content_type or "").startswith("video/")):
        return None
    prefix = "/api/v1/scenic" if scope == "scenic" else "/api/v1"
    return signed_asset_url(f"{prefix}/projects/{media.project_id}/media/{media.id}/content")


def _media_video_seek_seconds(media: database.MediaFile = None, timestamp=None):
    if media is None:
        return None
    media_type = str(media.media_type or "")
    content_type = str(media.content_type or "").lower()
    if media_type not in {"video", "camera"} and not content_type.startswith("video/"):
        return None
    raw_seconds = _timestamp_to_seconds_for_payload(timestamp)
    if media_type == "video":
        if raw_seconds is None:
            return None
        return raw_seconds if raw_seconds < 24 * 3600 else 0.0
    if not media.size_bytes and not content_type.startswith("video/"):
        return None
    captured_at = _datetime_from_payload(timestamp)
    started_at = _datetime_from_payload(media.created_at)
    if captured_at is not None and started_at is not None:
        return max(0.0, (captured_at - started_at).total_seconds())
    if raw_seconds is None:
        return None
    return raw_seconds if raw_seconds < 24 * 3600 else 0.0


def _source_info_video_seek_seconds(source_info: str = None):
    if not source_info:
        return None
    match = _VIDEO_SEEK_SECONDS_RE.search(str(source_info))
    if not match:
        return None
    try:
        return max(0.0, float(match.group(1)))
    except ValueError:
        return None


def _target_video_seek_seconds(target=None, media=None, metadata: dict = None):
    metadata = metadata or {}
    for value in (
        metadata.get("video_seek_seconds"),
        _source_info_video_seek_seconds(getattr(target, "source_info", None) if target else metadata.get("source_info")),
    ):
        if value is None:
            continue
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    timestamp = getattr(target, "timestamp", None) if target else metadata.get("timestamp")
    return _media_video_seek_seconds(media, timestamp)


def _browser_can_play_video(media) -> bool:
    if media is None:
        return False
    ext = Path(media.filename or media.object_key or "").suffix.lower()
    content_type = str(media.content_type or "").lower()
    return ext in {".mp4", ".webm"} or content_type in {"video/mp4", "video/webm"}


def _ffmpeg_supports_preview_encoder(ffmpeg: str, encoder: str) -> bool:
    cache_key = (ffmpeg, encoder)
    if cache_key in _VIDEO_PREVIEW_ENCODER_SUPPORT:
        return _VIDEO_PREVIEW_ENCODER_SUPPORT[cache_key]
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
        supported = completed.returncode == 0 and encoder in f"{completed.stdout}\n{completed.stderr}"
    except Exception:
        supported = False
    _VIDEO_PREVIEW_ENCODER_SUPPORT[cache_key] = supported
    return supported


def _video_preview_encoder_arg_sets(ffmpeg: str) -> list[list[str]]:
    encoders: list[list[str]] = []
    if _ffmpeg_supports_preview_encoder(ffmpeg, "h264_nvenc"):
        encoders.append(["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"])
    encoders.append(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
    return encoders


def _safe_preview_filename(media, suffix: str) -> str:
    stem = Path(media.filename or f"media_{media.id}").stem or f"media_{media.id}"
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in stem)[:80] or f"media_{media.id}"
    return f"{safe_stem}_{suffix}.mp4"


def _video_preview_payload(
    media,
    storage_project_id,
    *,
    start_seconds: float = 0.0,
    duration_seconds: float = None,
    full: bool = False,
    stream_manager=None,
    minimum_duration_seconds: float = 1.0,
) -> dict:
    if media is None or not media.object_key:
        raise HTTPException(status_code=404, detail="媒体不存在")
    content_type = str(media.content_type or "").lower()
    if media.media_type not in {"video", "camera"} and not content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="该媒体不是视频")

    original_url = storage.url_for(media.object_key)
    start = max(0.0, float(start_seconds or 0.0))
    duration = max(minimum_duration_seconds, min(60.0, float(duration_seconds or 8.0)))
    manager = stream_manager or camera_stream_manager
    if full and _browser_can_play_video(media):
        return {
            "status": "success",
            "preview_url": original_url,
            "original_url": original_url,
            "generated": False,
            "start_seconds": 0,
            "duration_seconds": None,
            "media_id": media.id,
            "preview_object_key": "",
        }

    ffmpeg = ffmpeg_executable()
    mode = "full" if full else f"clip_{start:.3f}_{duration:.3f}"
    fingerprint = hashlib.sha256(f"v3:{media.object_key}:{media.size_bytes}:{mode}".encode("utf-8")).hexdigest()[:20]
    filename = _safe_preview_filename(media, f"{'full' if full else 'clip'}_{fingerprint}")
    object_key = f"projects/{storage_project_id}/media/{media.id}/previews/{filename}"
    target_path = get_settings().object_storage_dir / object_key
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(target_path) + ".lock", timeout=120):
            if not target_path.exists():
                source_path = storage.path_for(media.object_key)
                if not source_path.exists():
                    active_recording_path = manager.recording_path_for_media(media.id) if manager else None
                    if active_recording_path and active_recording_path.exists():
                        source_path = active_recording_path
                    else:
                        raise HTTPException(status_code=404, detail="视频文件不存在")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path = target_path.with_name(f".{target_path.stem}.{uuid.uuid4().hex}.tmp.mp4")
                errors = []
                if ffmpeg:
                    for encoder_args in _video_preview_encoder_arg_sets(ffmpeg):
                        cmd = [ffmpeg, "-y", "-hide_banner"]
                        if not full and start > 0:
                            cmd.extend(["-ss", f"{start:.3f}"])
                        cmd.extend(["-i", str(source_path)])
                        if not full:
                            cmd.extend(["-t", f"{duration:.3f}"])
                        cmd.extend([
                            "-an",
                            "-vf",
                            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                            "-pix_fmt",
                            "yuv420p",
                            "-movflags",
                            "+faststart",
                        ])
                        cmd.extend(encoder_args)
                        cmd.append(str(staged_path))
                        try:
                            completed = subprocess.run(
                                cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                timeout=300 if full else 90,
                                check=False,
                            )
                            if completed.returncode == 0 and staged_path.exists() and staged_path.stat().st_size > 0:
                                staged_path.replace(target_path)
                                break
                            errors.append(completed.stderr[-1000:])
                            staged_path.unlink(missing_ok=True)
                        except Exception as exc:
                            errors.append(str(exc))
                            staged_path.unlink(missing_ok=True)
                if not target_path.exists() and _write_opencv_video_preview(source_path, target_path, start=start, duration=duration, full=full):
                    pass
                if not target_path.exists():
                    if not ffmpeg:
                        errors.append("ffmpeg unavailable and OpenCV preview fallback failed")
                    raise HTTPException(status_code=500, detail="视频预览生成失败: " + " | ".join(errors[-2:]))
    except Timeout as exc:
        raise HTTPException(503, "视频预览正在生成，请稍后重试") from exc

    return {
        "status": "success",
        "preview_url": storage.url_for(object_key),
        "original_url": original_url,
        "generated": True,
        "start_seconds": 0.0,
        "duration_seconds": None if full else duration,
        "media_id": media.id,
        "preview_object_key": object_key,
    }


def _write_opencv_video_preview(source_path: Path, target_path: Path, *, start: float, duration: float, full: bool) -> bool:
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        return False
    temp_path = target_path.with_name(f"{target_path.stem}.tmp{target_path.suffix}")
    writer = None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps < 1.0 or fps > 60.0:
            fps = 24.0
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        source_duration = frame_count / fps if frame_count > 0 else 0.0
        end_seconds = source_duration if full and source_duration > 0 else (float("inf") if full else start + duration)
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start) * 1000.0)
        while True:
            current_seconds = max(0.0, float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0)
            if current_seconds > end_seconds + 0.05:
                break
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frame = _fit_preview_video_frame(frame)
            if writer is None:
                height, width = frame.shape[:2]
                writer = _open_preview_video_writer(temp_path, width, height, fps)
                if writer is None:
                    return False
            writer.write(frame)
        if writer is not None:
            writer.release()
            writer = None
        if temp_path.exists() and temp_path.stat().st_size > 0:
            temp_path.replace(target_path)
            return True
        return False
    finally:
        if writer is not None:
            writer.release()
        cap.release()
        temp_path.unlink(missing_ok=True)


def _fit_preview_video_frame(frame):
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return frame
    max_width = 960
    if width > max_width:
        scale = max_width / width
        width = max(2, int(width * scale))
        height = max(2, int(height * scale))
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    height, width = frame.shape[:2]
    even_width = max(2, width - (width % 2))
    even_height = max(2, height - (height % 2))
    if even_width != width or even_height != height:
        frame = frame[:even_height, :even_width]
    return frame


def _open_preview_video_writer(path: Path, width: int, height: int, fps: float):
    # MPEG-4 Part 2 (mp4v) creates valid MP4 files that Chromium cannot play.
    for codec in ("avc1", "H264"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, (width, height))
        if writer.isOpened():
            return writer
        writer.release()
    return None



load_verified_rgb_image = _load_verified_rgb_image
media_type_for_filename = _media_type_for_filename
delete_storage_objects = _delete_storage_objects
delete_gallery_path = _delete_gallery_path
delete_media_assets = _delete_media_assets
safe_delete_vectors_by_project = _safe_delete_vectors_by_project
safe_delete_vectors_by_media = _safe_delete_vectors_by_media
public_gallery_path = _public_gallery_path
media_download_url = _media_download_url
media_video_seek_seconds = _media_video_seek_seconds
source_info_video_seek_seconds = _source_info_video_seek_seconds
target_video_seek_seconds = _target_video_seek_seconds
timestamp_to_seconds_for_payload = _timestamp_to_seconds_for_payload
merge_central_camera_status = _merge_central_camera_status
video_preview_payload = _video_preview_payload
