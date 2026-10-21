from __future__ import annotations

import datetime
import hashlib
import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import Optional

from app.services.camera_policy import validate_camera_source, capture_source, safe_camera_name

import cv2
from app.services import camera_bridge

from app.core.config import get_settings
from app.services.storage import storage


logger = logging.getLogger("myreid.camera")
BASE_GALLERY_DIR = str(get_settings().gallery_dir)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _safe_source(source: str) -> str:
    return safe_camera_name(source)


def _normalize_source(source: str) -> str:
    normalized = validate_camera_source(camera_bridge.resolve(str(source or "0").strip() or "0"))
    if len(normalized) > 2048:
        raise ValueError("Camera source is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("Camera source contains control characters")
    return normalized


class CameraSourceBusyError(RuntimeError):
    """The previous capture still owns the device; callers may retry later."""


class CentralCameraService:
    """Owns one physical camera source for the whole backend process.

    Consumers of the same source share cached frames, while different sources
    use different service instances and can therefore run concurrently.
    """

    def __init__(self, default_source: str = "0", *, use_environment_source: bool = True) -> None:
        configured_source = os.getenv("MYREID_CAMERA_SOURCE") if use_environment_source else ""
        self.source = _normalize_source(configured_source or default_source)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.RLock()
        self._frame_available = threading.Condition(self._lock)
        self._frame = None
        self._frame_time: Optional[datetime.datetime] = None
        self._frame_index = 0
        self._preview_jpeg = b""
        self._preview_index = 0
        self._preview_fps = 0.0
        self._capture_fps = 0.0
        self._frame_seek_seconds = 0.0
        self._frame_recording_object_key = ""
        self._frame_recording_frame_index = 0
        self._latest_frame_url = ""
        self._latest_frame_version = 0
        self._running = False
        self._message = "统一相机服务未启动"
        self._error = ""
        self._width = 0
        self._height = 0
        self._input_width = 0
        self._input_height = 0
        self._recording_path: Optional[Path] = None
        self._recording_object_key = ""
        self._recording_filename = ""
        self._recording_started_at: Optional[datetime.datetime] = None
        self._recording_frame_size: tuple[int, int] | None = None
        self._recording_fps = float(get_settings().camera_preview_fps)
        self._recording_frame_index = 0
        self._last_announced = ""
        self._rollover_requested = False
        self._rollover_sequence = 0
        self._sealed_sequence = 0
        self._last_sealed_recording: dict = {}
        self._consumer_count = 0
        self._recording_consumers = 0
        self._preview_lock = threading.RLock()
        self._preview_timer: Optional[threading.Timer] = None
        self._preview_generation = 0
        self._persistent = False
        self._recording_writer_retry_at = 0.0
        self._last_accessed_monotonic = time.monotonic()

    def configure_source(self, source: str) -> None:
        source = _normalize_source(source)
        with self._lock:
            if self._running and source != self.source:
                self._message = f"统一相机已占用源 {self.source}，忽略项目请求源 {source}"
                self._announce_locked(self._message, level=logging.WARNING)
                return
            if source != self.source:
                self.source = source
                self._message = f"统一相机源已切换为 {source}，等待重新连接"
                self._announce_locked(self._message)

    def start(self) -> None:
        with self._lock:
            self._persistent = True
        self._start_thread()

    def acquire(self, *, record: bool = True) -> None:
        with self._lock:
            self._last_accessed_monotonic = time.monotonic()
            self._consumer_count += 1
            self._recording_consumers += int(record)
        try:
            self._start_thread()
        except Exception:
            with self._lock:
                self._consumer_count = max(0, self._consumer_count - 1)
                self._recording_consumers = max(0, self._recording_consumers - int(record))
            raise
        # A concurrent release may have removed the last lease while this
        # acquire was waiting for an older thread to stop. Reconcile demand so
        # a newly-started capture cannot remain orphaned.
        with self._lock:
            should_stop = self._consumer_count == 0 and not self._persistent
        if should_stop:
            self._stop_thread()

    def release(self, *, record: bool = True) -> None:
        with self._lock:
            self._last_accessed_monotonic = time.monotonic()
            self._consumer_count = max(0, self._consumer_count - 1)
            self._recording_consumers = max(0, self._recording_consumers - int(record))
            should_stop = self._consumer_count == 0 and not self._persistent
        if should_stop:
            self._stop_thread()

    def preview(self, lease_seconds: float = 15.0) -> None:
        """Keep one shared preview lease alive while a registration page polls.

        Expiry releases only this lease, leaving ingestion and other consumers
        of the same physical camera untouched.
        """
        with self._preview_lock:
            if self._preview_timer is None:
                self.acquire()
            else:
                # Renew demand after an unexpected capture-thread failure too.
                # Keep the existing expiry timer if restarting raises.
                self._start_thread()
                self._preview_timer.cancel()
            self._preview_generation += 1
            timer = threading.Timer(
                max(1.0, lease_seconds), self._expire_preview,
                args=(self._preview_generation,),
            )
            timer.daemon = True
            self._preview_timer = timer
            timer.start()

    def _expire_preview(self, generation: int) -> None:
        with self._preview_lock:
            if generation != self._preview_generation or self._preview_timer is None:
                return
            self._preview_timer = None
            self.release()

    def _start_thread(self) -> None:
        with self._lifecycle_lock:
            self._start_thread_serialized()

    def _start_thread_serialized(self) -> None:
        previous_thread = None
        with self._lock:
            if self._thread and self._thread.is_alive():
                if not self._stop_event.is_set():
                    return
                previous_thread = self._thread
        if previous_thread:
            previous_thread.join(timeout=5)
            if previous_thread.is_alive():
                raise CameraSourceBusyError(f"相机源 {self.source} 正在释放，请稍后重试")
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._frame = None
            self._preview_jpeg = b""
            self._frame_time = None
            self._latest_frame_url = ""
            self._running = False
            self._thread = threading.Thread(
                target=self._run,
                name=f"camera-monitor-{_safe_source(self.source)}",
                daemon=True,
            )
            self._thread.start()
            self._message = f"统一相机服务已启动，正在监测源 {self.source}"
            self._announce_locked(self._message)

    def stop(self) -> None:
        with self._preview_lock:
            if self._preview_timer is not None:
                self._preview_timer.cancel()
                self._preview_timer = None
            with self._lock:
                self._persistent = False
                self._consumer_count = 0
                self._recording_consumers = 0
            self._stop_thread()

    def _stop_thread(self) -> None:
        with self._lifecycle_lock:
            self._stop_thread_serialized()

    def _stop_thread_serialized(self) -> None:
        with self._frame_available:
            # A new system may acquire the source after the last consumer's
            # release decided to stop it. Do not stop that new consumer.
            if self._consumer_count > 0 or self._persistent:
                return
            self._stop_event.set()
            thread = self._thread
            self._frame_available.notify_all()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                logger.warning(
                    "Camera thread did not stop before timeout",
                    extra={"event": "camera.stop_timeout", "camera_source": self.source},
                )

    def latest_frame(self):
        with self._lock:
            if self._frame is None:
                return None, None, self._frame_index
            return self._frame.copy(), self._frame_time, self._frame_index

    def wait_for_frame(self, timeout: float = 1.0, *, after_index: int | None = None):
        with self._lock:
            has_demand = self._persistent or self._consumer_count > 0
            needs_restart = has_demand and (self._thread is None or not self._thread.is_alive())
        if needs_restart:
            try:
                self._start_thread()
            except RuntimeError as exc:
                logger.warning(
                    "Camera thread restart deferred: %s",
                    exc,
                    extra={"event": "camera.restart_deferred", "camera_source": self.source},
                )
        end_at = time.monotonic() + max(0.0, timeout)
        with self._frame_available:
            # Consumers can immediately use frames captured during inference;
            # otherwise retain the existing wait-for-next-frame behavior.
            current_index = self._frame_index if after_index is None else after_index
            while not self._stop_event.is_set():
                if self._frame is not None and self._frame_index != current_index:
                    return self._frame.copy(), self._frame_time, self._frame_index, self._frame_video_info_locked()
                remaining = end_at - time.monotonic()
                if remaining <= 0:
                    if self._frame is not None:
                        return self._frame.copy(), self._frame_time, self._frame_index, self._frame_video_info_locked()
                    return None, None, self._frame_index, {}
                self._frame_available.wait(timeout=min(remaining, 0.25))
        return None, None, current_index, {}

    def status(self) -> dict:
        with self._lock:
            recording = self._recording_info_locked()
            return {
                "running": self._running,
                "monitor_running": self._running,
                "source": self.source,
                "camera_source": self.source,
                "message": self._message,
                "error": self._error,
                "width": self._width,
                "height": self._height,
                "input_width": self._input_width,
                "input_height": self._input_height,
                "capture_fps": round(self._capture_fps, 1),
                "preview_fps": round(self._preview_fps, 1),
                "target_fps": get_settings().camera_preview_fps,
                "latest_frame_url": self._latest_frame_url,
                "latest_frame_version": self._latest_frame_version,
                "frame_index": self._frame_index,
                "recording_path": str(recording.get("path") or ""),
                "recording_object_key": recording.get("object_key") or "",
                "recording_url": recording.get("url") or "",
                "recording_filename": recording.get("filename") or "",
                "recording_ready": bool(recording.get("size_bytes")),
                "recording_size_bytes": int(recording.get("size_bytes") or 0),
                "recording_started_at": recording.get("started_at").isoformat() if recording.get("started_at") else "",
                "consumers": self._consumer_count,
            }

    def recording_info(self) -> dict:
        with self._lock:
            return dict(self._recording_info_locked())

    def registry_eviction_state(self) -> tuple[bool, float]:
        """Return whether the service is idle and its last lease activity."""

        with self._lock:
            idle = (
                self._consumer_count == 0
                and not self._persistent
                and (self._thread is None or not self._thread.is_alive())
            )
            return idle, self._last_accessed_monotonic

    def touch_registry_access(self) -> None:
        with self._lock:
            self._last_accessed_monotonic = time.monotonic()

    def seal_current_recording(self, timeout: float = 5.0) -> dict:
        """Finalize the current recording file and immediately roll over.

        The camera thread owns the VideoWriter, so callers ask it to seal the
        file instead of copying an AVI that may still have an incomplete header.
        """
        self.acquire()
        try:
            deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
            with self._frame_available:
                self._rollover_sequence += 1
                sequence = self._rollover_sequence
                self._rollover_requested = True
                self._frame_available.notify_all()
                while self._sealed_sequence < sequence and not self._stop_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._frame_available.wait(timeout=min(remaining, 0.25))
                if self._sealed_sequence >= sequence:
                    return dict(self._last_sealed_recording)
            raise RuntimeError("录像尚未完成封存，请稍后重试")
        finally:
            self.release()

    def recording_path_for_media(self, media_id: int = 0) -> Optional[Path]:
        info = self.recording_info()
        path = info.get("path")
        if isinstance(path, Path) and path.exists():
            return path
        return None

    def devices(self) -> list[dict]:
        status = self.status()
        return [
            {
                "source": status["source"],
                "index": int(status["source"]) if str(status["source"]).isdigit() else 0,
                "label": "统一监控摄像头" if status["running"] else "统一监控摄像头（等待接入）",
                "name": "统一监控摄像头",
                "type": "central",
                "width": int(status.get("width") or 0),
                "height": int(status.get("height") or 0),
                "running": bool(status.get("running")),
                "latest_frame_url": status.get("latest_frame_url") or "",
                "latest_frame_version": int(status.get("latest_frame_version") or 0),
                "message": status.get("message") or "",
            }
        ]

    def _run(self) -> None:
        cap = None
        writer = None
        opened_source = ""
        fatal_error = ""
        next_preview_at = 0.0
        last_snapshot_at = 0.0
        measured_at = time.monotonic()
        captured = previews = 0
        settings = get_settings()
        try:
            while not self._stop_event.is_set():
                if cap is None or not cap.isOpened():
                    source = self.source
                    self._set_state(running=False, message=f"正在等待相机源 {source}", error="")
                    cap = self._open_capture(source)
                    opened_source = source
                    if not cap.isOpened():
                        try:
                            cap.release()
                        except Exception:
                            pass
                        cap = None
                        self._stop_event.wait(2.0)
                        continue
                    self._set_state(running=True, message=f"统一相机源 {source} 已接入", error="")

                ok, frame = cap.read()
                if not ok or frame is None:
                    self._set_state(running=False, message="相机读取失败，正在重新连接", error="相机没有输出画面")
                    if cap is not None:
                        cap.release()
                        cap = None
                    self._stop_event.wait(1.0)
                    continue

                now = _utc_now()
                input_height, input_width = frame.shape[:2]
                # Bound processing/recording cost even when an IP camera ignores
                # OpenCV's resolution negotiation. Network sources should also
                # use a 640-wide substream to reduce transport/decode cost.
                if frame.shape[1] > settings.camera_capture_width:
                    frame = cv2.resize(frame, (settings.camera_capture_width,
                        max(1, round(frame.shape[0] * settings.camera_capture_width / frame.shape[1]))),
                        interpolation=cv2.INTER_AREA)
                height, width = frame.shape[:2]
                with self._lock:
                    record = self._persistent or self._recording_consumers > 0
                if not record and writer is not None:
                    writer.release()
                    writer = None
                    with self._lock:
                        self._seal_recording_locked()
                if record and (self._recording_path is None or opened_source != self.source):
                    self._prepare_recording_session(frame)
                if record and writer is None and time.monotonic() >= self._recording_writer_retry_at:
                    writer = self._open_recording_writer(frame, cap)
                    if writer is None:
                        self._recording_writer_retry_at = time.monotonic() + 5.0
                with self._frame_available:
                    fps = max(1.0, float(self._recording_fps or 20.0))
                    recording_frame_index = int(self._recording_frame_index)
                    seek_seconds = recording_frame_index / fps
                    recording_object_key = self._recording_object_key
                if writer is not None:
                    writer.write(self._fit_recording_frame(frame))
                with self._frame_available:
                    self._frame = frame.copy()
                    self._frame_time = now
                    self._frame_index += 1
                    self._frame_seek_seconds = float(seek_seconds)
                    self._frame_recording_object_key = recording_object_key
                    self._frame_recording_frame_index = recording_frame_index
                    self._recording_frame_index = recording_frame_index + int(record)
                    self._width = int(width)
                    self._height = int(height)
                    self._input_width = int(input_width)
                    self._input_height = int(input_height)
                    self._running = True
                    self._message = "统一相机采集中"
                    self._error = ""
                    self._frame_available.notify_all()

                tick = time.monotonic()
                captured += 1
                preview_interval = 1 / settings.camera_preview_fps
                # Deadline pacing tolerates camera jitter. Measuring every gap
                # against 33ms drops valid frames after a slightly early read.
                if tick >= next_preview_at - preview_interval / 2:
                    next_preview_at = max(next_preview_at + preview_interval, tick + preview_interval / 2)
                    image = frame
                    if width > settings.camera_preview_width:
                        image = cv2.resize(frame, (settings.camera_preview_width,
                            max(1, round(height * settings.camera_preview_width / width))), interpolation=cv2.INTER_AREA)
                    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, settings.camera_jpeg_quality])
                    if ok:
                        with self._lock:
                            self._preview_jpeg = encoded.tobytes()
                            self._preview_index += 1
                        previews += 1
                if tick - measured_at >= 1:
                    with self._lock:
                        self._capture_fps = captured / (tick - measured_at)
                        self._preview_fps = previews / (tick - measured_at)
                    captured = previews = 0
                    measured_at = tick
                if record and tick - last_snapshot_at >= 1:
                    last_snapshot_at = tick
                    try:
                        self._save_central_latest_frame(frame)
                    except Exception:
                        logger.exception(
                            "Could not persist the latest camera frame",
                            extra={"event": "camera.preview_write_failed", "camera_source": self.source},
                        )

                if self._rollover_requested:
                    if writer is not None:
                        writer.release()
                        writer = None
                    with self._frame_available:
                        self._seal_recording_locked()
                        self._sealed_sequence = self._rollover_sequence
                        self._rollover_requested = False
                        self._frame_available.notify_all()

                # cap.read() already paces a live device. A second timed wait
                # rounds up on Windows and can consume most of a 33ms budget.
        except Exception as exc:
            fatal_error = str(exc)
            logger.exception(
                "Central camera capture stopped unexpectedly",
                extra={"event": "camera.capture_failed", "camera_source": self.source},
            )
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    logger.exception("Could not release camera recording writer")
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    logger.exception("Could not release camera capture")
            with self._frame_available:
                try:
                    self._seal_recording_locked()
                except Exception:
                    logger.exception(
                        "Could not seal camera recording during shutdown",
                        extra={"event": "camera.recording_seal_failed", "camera_source": self.source},
                    )
                self._sealed_sequence = self._rollover_sequence
                self._rollover_requested = False
                self._frame_available.notify_all()
            self._set_state(
                running=False,
                message="统一相机服务异常停止" if fatal_error else "统一相机服务已停止",
                error=fatal_error,
            )
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    def _recording_info_locked(self) -> dict:
        path = self._recording_path
        object_key = self._recording_object_key
        filename = self._recording_filename
        started_at = self._recording_started_at
        if not path or not object_key:
            return dict(self._last_sealed_recording)
        size_bytes = path.stat().st_size if path.exists() else 0
        return {
            "path": path,
            "object_key": object_key,
            "filename": filename,
            "content_type": "video/x-msvideo",
            "size_bytes": int(size_bytes),
            "sha256": hashlib.sha256(object_key.encode("utf-8")).hexdigest(),
            "started_at": started_at,
            "url": storage.url_for(object_key),
        }

    def _seal_recording_locked(self) -> None:
        info = self._recording_info_locked()
        if info.get("path") and info.get("object_key"):
            self._last_sealed_recording = dict(info)
            self._announce_locked(f"统一相机录像文件已封存: {info['object_key']}")
        self._recording_path = None
        self._recording_object_key = ""
        self._recording_filename = ""
        self._recording_started_at = None
        self._recording_frame_size = None
        self._recording_frame_index = 0

    def _set_state(self, *, running: bool, message: str, error: str) -> None:
        with self._lock:
            self._running = running
            self._message = message
            self._error = error
            if not running:
                self._capture_fps = self._preview_fps = 0.0
            self._announce_locked(message, level=logging.ERROR if error else logging.INFO)

    def _announce_locked(self, message: str, *, level: int = logging.INFO) -> None:
        if message == self._last_announced:
            return
        self._last_announced = message
        logger.log(level, message, extra={"event": "camera.central", "camera_source": self.source})
        print(f"[MyReID Camera] {message}", flush=True)

    @staticmethod
    def _opencv_source(source: str):
        try:
            return int(source)
        except (TypeError, ValueError):
            return source

    @classmethod
    def _open_capture(cls, source: str):
        opencv_source = cls._opencv_source(capture_source(source))
        if isinstance(opencv_source, int):
            if platform.system() == "Windows":
                cap = cv2.VideoCapture(opencv_source, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    cap = cv2.VideoCapture(opencv_source, cv2.CAP_MSMF)
            elif platform.system() == "Darwin":
                cap = cv2.VideoCapture(opencv_source, cv2.CAP_AVFOUNDATION)
            else:
                cap = cv2.VideoCapture(opencv_source)
        elif str(opencv_source).lower().startswith(("rtsp://", "rtsps://")):
            cap = cv2.VideoCapture(opencv_source, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
            ])
        else:
            cap = cv2.VideoCapture(opencv_source)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if isinstance(opencv_source, int):
                settings = get_settings()
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.camera_capture_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.camera_capture_height)
                cap.set(cv2.CAP_PROP_FPS, settings.camera_preview_fps)
        except Exception:
            pass
        return cap

    def _prepare_recording_session(self, frame) -> None:
        started_at = _utc_now()
        safe_source = _safe_source(self.source)
        filename = (
            f"central_camera_{safe_source}_"
            f"{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{self._rollover_sequence}.avi"
        )
        object_key = f"camera_monitor/{safe_source}/{filename}"
        path = get_settings().object_storage_dir / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        height, width = frame.shape[:2]
        with self._lock:
            self._recording_path = path
            self._recording_object_key = object_key
            self._recording_filename = filename
            self._recording_started_at = started_at
            self._recording_frame_size = (int(width), int(height))
            self._recording_frame_index = 0
            self._recording_writer_retry_at = 0.0
        self._announce_locked(f"统一相机录像文件已创建: {object_key}")

    def _open_recording_writer(self, frame, cap):
        with self._lock:
            path = self._recording_path
            frame_size = self._recording_frame_size
        if path is None:
            return None
        height, width = frame.shape[:2]
        if frame_size is None:
            frame_size = (int(width), int(height))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps < 1.0 or fps > 60.0:
            fps = float(get_settings().camera_preview_fps)
        with self._lock:
            self._recording_fps = float(fps)
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, frame_size)
        if not writer.isOpened():
            writer.release()
            self._set_state(running=True, message="统一相机采集中，但录像文件暂不可写", error="")
            return None
        return writer

    def _fit_recording_frame(self, frame):
        with self._lock:
            frame_size = self._recording_frame_size
        if not frame_size:
            return frame
        target_width, target_height = frame_size
        if frame.shape[1] == target_width and frame.shape[0] == target_height:
            return frame
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    def _frame_video_info_locked(self) -> dict:
        return {
            "video_seek_seconds": float(self._frame_seek_seconds or 0.0),
            "recording_object_key": self._frame_recording_object_key,
            "recording_frame_index": int(self._frame_recording_frame_index or 0),
            "recording_fps": float(self._recording_fps or 20.0),
        }

    def _save_central_latest_frame(self, frame) -> None:
        frame_dir = Path(BASE_GALLERY_DIR) / "central_camera" / _safe_source(self.source)
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / "latest.jpg"
        temp_path = frame_dir / f"latest.{self._frame_index}.tmp.jpg"
        try:
            jpeg, _ = self.latest_preview()
            if not jpeg:
                return
            temp_path.write_bytes(jpeg)
            try:
                temp_path.replace(frame_path)
                with self._lock:
                    self._latest_frame_url = f"/gallery/central_camera/{_safe_source(self.source)}/latest.jpg"
                    self._latest_frame_version += 1
            except OSError:
                pass  # A busy snapshot must never stall live capture.
        finally:
            temp_path.unlink(missing_ok=True)

    def latest_preview(self) -> tuple[bytes, int]:
        """Immutable latest-only cache, encoded once for every viewer."""
        with self._lock:
            return self._preview_jpeg, self._preview_index


central_camera_service = CentralCameraService()
_CAMERA_SERVICES_LOCK = threading.RLock()
_MAX_CAMERA_SERVICES = 32
_CAMERA_SERVICES: dict[str, CentralCameraService] = {
    central_camera_service.source: central_camera_service,
}


def get_camera_service(source: str = "0") -> CentralCameraService:
    """Return the process-wide capture service dedicated to ``source``."""
    normalized_source = _normalize_source(source)
    with _CAMERA_SERVICES_LOCK:
        service = _CAMERA_SERVICES.get(normalized_source)
        if service is None:
            if len(_CAMERA_SERVICES) >= _MAX_CAMERA_SERVICES:
                idle_services = sorted(
                    (
                        (last_accessed, registered_source)
                        for registered_source, registered_service in _CAMERA_SERVICES.items()
                        if registered_service is not central_camera_service
                        for idle, last_accessed in [registered_service.registry_eviction_state()]
                        if idle
                    )
                )
                for _last_accessed, registered_source in idle_services:
                    _CAMERA_SERVICES.pop(registered_source, None)
                    if len(_CAMERA_SERVICES) < _MAX_CAMERA_SERVICES:
                        break
            if len(_CAMERA_SERVICES) >= _MAX_CAMERA_SERVICES:
                raise RuntimeError("Too many active camera sources")
            service = CentralCameraService(normalized_source, use_environment_source=False)
            _CAMERA_SERVICES[normalized_source] = service
        service.touch_registry_access()
        return service


def find_camera_service(source: str = "0") -> Optional[CentralCameraService]:
    """Return an existing source service without opening or registering it."""
    try:
        normalized_source = _normalize_source(source)
    except ValueError:
        return None
    with _CAMERA_SERVICES_LOCK:
        return _CAMERA_SERVICES.get(normalized_source)


def camera_services() -> list[CentralCameraService]:
    with _CAMERA_SERVICES_LOCK:
        return list(_CAMERA_SERVICES.values())


def stop_camera_services() -> None:
    services = camera_services()
    for service in services:
        service.stop()
    with _CAMERA_SERVICES_LOCK:
        _CAMERA_SERVICES.clear()
        _CAMERA_SERVICES[central_camera_service.source] = central_camera_service
