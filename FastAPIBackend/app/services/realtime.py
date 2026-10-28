from __future__ import annotations
from contextlib import ExitStack
from app.services.project_models import project_context
"""Ephemeral single-process real-time recognition. No index or business-table writes."""

import base64
import logging
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from app.core.config import get_settings
from app.services.person_tracking import IdentityGate, create_tracker, iou, update_tracker

log = logging.getLogger(__name__)
TERMINAL = {"stopped", "completed", "failed"}


@dataclass
class Input:
    id: str
    project: int
    owner: str
    path: Path
    kind: str
    created: float = field(default_factory=time.monotonic)
    claimed: bool = False


class Session:
    def __init__(self, project, owner, reference, crop, source, camera, show_others, cleanup):
        self.id = uuid4().hex
        self.project, self.owner = project, owner
        self.reference, self.crop, self.source, self.camera = reference, crop, source, camera
        self.show_others, self.cleanup = show_others, cleanup
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.status, self.error = "starting", ""
        self.paused = False
        self.reset = False
        self.connected = False
        self.disconnected_at = time.monotonic()
        self.finished_at = 0.0
        self.packet = None
        self.sequence = 0
        self.acked = 0
        self.epoch = 0
        self.thread = threading.Thread(target=self.run, name=f"realtime-{self.id}", daemon=True)

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "status": self.status, "error": self.error, "epoch": self.epoch,
                    "frame": {k: v for k, v in (self.packet or {}).items() if k != "jpeg"}}

    def control(self, action):
        with self.lock:
            if action == "stop":
                self.stop_event.set()
                self.status = "stopping" if self.thread.is_alive() else "stopped"
            elif self.status not in TERMINAL and not self.stop_event.is_set():
                self.paused = action == "pause"
                self.status = "paused" if self.paused else "running"
                self.epoch += 1
                self.packet = None
                self.acked = self.sequence
                if self.camera:
                    self.reset = True

    def run(self):
        cap = service = None
        model_context = ExitStack()
        try:
            model_context.enter_context(project_context(self.project, expected_space=getattr(self, "embedding_space_id", None)))
            from app.services.model_runtime import clip_image_feature, clip_image_features, detect_people
            settings = get_settings()
            inference_fps = settings.realtime_camera_inference_fps if self.camera else 10
            tracker = create_tracker(inference_fps)
            gate = IdentityGate(settings.realtime_enter_threshold, settings.realtime_exit_threshold, settings.realtime_margin)
            with Image.open(self.reference) as original:
                image = original.convert("RGB")
                w, h = image.size
                box = [int(v * (w if i % 2 == 0 else h)) for i, v in enumerate(self.crop)]
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise ValueError("目标裁剪区域太小")
                reference = clip_image_feature(image.crop(box)).detach().float().cpu().numpy().reshape(-1)
            if self.stop_event.is_set():
                return
            if self.camera:
                from app.services.central_camera_service import get_camera_service
                candidate = get_camera_service(self.source)
                candidate.acquire(record=False)
                service = candidate
            else:
                cap = cv2.VideoCapture(str(self.source))
                if not cap.isOpened():
                    raise ValueError("无法打开视频")
            native_fps = float(cap.get(cv2.CAP_PROP_FPS)) if cap else 10.0
            if not np.isfinite(native_fps) or native_fps <= 0:
                native_fps = 25.0
            step = max(1, math.ceil(native_fps / 10))
            interval = step / native_fps if cap else 1 / inference_fps
            index, last_camera_index, last_time = 0, -1, None
            camera_wait_started = time.monotonic()
            camera_stalled = False
            cache = {}
            previous_ids = set()
            with self.lock:
                if not self.paused:
                    self.status = "running"
            while not self.stop_event.is_set():
                began = time.monotonic()
                with self.lock:
                    waiting = self.paused or not self.connected or (cap is not None and self.acked < self.sequence)
                    epoch = self.epoch
                    reset = self.reset
                    self.reset = False
                if reset:
                    tracker = create_tracker(inference_fps)
                    gate = IdentityGate(settings.realtime_enter_threshold, settings.realtime_exit_threshold, settings.realtime_margin)
                    cache.clear()
                    previous_ids.clear()
                    if service:
                        last_camera_index = service.latest_frame()[2]
                        camera_wait_started = time.monotonic()
                        camera_stalled = False
                if waiting:
                    self.stop_event.wait(0.05)
                    continue
                if cap:
                    ok, frame = cap.read()
                    if not ok:
                        self.status = "completed"
                        break
                    seconds = index / native_fps
                    for _ in range(step - 1):
                        if not cap.grab():
                            break
                    index += step
                else:
                    frame, captured, frame_index, _ = service.wait_for_frame(
                        timeout=0.5, after_index=last_camera_index)
                    stale = captured is None or time.time() - captured.timestamp() > 1.5
                    if frame is None or frame_index == last_camera_index or stale:
                        # RTSP handshake/keyframe startup takes longer than a
                        # local read. Brief jitter must not repeatedly reset the
                        # tracker or discard the first frame after recovery.
                        grace = 10.0 if last_time is None else 3.0
                        if time.monotonic() - camera_wait_started >= grace:
                            with self.lock:
                                self.packet = None
                                self.error = "相机暂未输出新画面，等待重新连接"
                            if not camera_stalled:
                                tracker = create_tracker(inference_fps)
                                gate = IdentityGate(settings.realtime_enter_threshold, settings.realtime_exit_threshold, settings.realtime_margin)
                                cache.clear()
                                previous_ids.clear()
                                camera_stalled = True
                        if time.monotonic() - began < 0.02:
                            self.stop_event.wait(0.05)
                        continue
                    last_camera_index = frame_index
                    seconds = captured.timestamp()
                    camera_wait_started = time.monotonic()
                    camera_stalled = False
                    self.error = ""
                if last_time is not None and seconds - last_time > 1.5:
                    tracker = create_tracker(inference_fps)
                    gate.target = gate.candidate = None
                    gate.hits = 0
                    cache.clear()
                last_time = seconds
                height, width = frame.shape[:2]
                scale = min(1, settings.camera_preview_width / width) if self.camera else min(1, 1280 / width, 720 / height)
                if scale < 1:
                    frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
                height, width = frame.shape[:2]
                detections = detect_people(frame, min_confidence=0.1)
                tracked = update_tracker(tracker, detections, (height, width))
                rows = []
                for track in tracked:
                    # Use the current detection box, never a predicted/lost-track box.
                    detection_index = int(track[-1])
                    if not 0 <= detection_index < len(detections):
                        continue
                    detection = detections[detection_index]
                    box = list(detection.clamped_xyxy(width, height))
                    if box[2] <= box[0] or box[3] <= box[1]:
                        continue
                    rows.append({"track_id": int(track[4]), "box": box, "detection_confidence": detection.confidence})
                ids = {r["track_id"] for r in rows}
                cache = {key: value for key, value in cache.items() if key in ids and key in previous_ids}
                crowded = any(iou(a["box"], b["box"]) > 0.1 for i, a in enumerate(rows) for b in rows[i+1:])
                measured = crowded or ids != previous_ids or any(seconds - cache.get(t, (-1, -1e20))[1] >= 0.5 for t in ids)
                if measured and rows:
                    # Chunk a crowded frame to keep GPU memory bounded.
                    for start in range(0, len(rows), 16):
                        batch = rows[start:start+16]
                        crops = [Image.fromarray(cv2.cvtColor(frame[r["box"][1]:r["box"][3], r["box"][0]:r["box"][2]], cv2.COLOR_BGR2RGB)) for r in batch]
                        features = clip_image_features(crops).detach().float().cpu().numpy()
                        for row, feature in zip(batch, features):
                            cache[row["track_id"]] = (float(np.clip(feature @ reference, -1, 1)), seconds)
                scores = {key: value[0] for key, value in cache.items()}
                state, target = gate.update(scores, measured=measured, crowded=crowded)
                previous_ids = ids
                for row in rows:
                    tid, box = row["track_id"], row["box"]
                    score = scores.get(tid, -1)
                    row["similarity"] = score
                    row["target"] = tid == target
                    candidate = state == "pending" and score >= settings.realtime_exit_threshold
                    if tid == target or candidate or self.show_others:
                        color = (60, 220, 70) if tid == target else (0, 215, 255) if candidate else (150, 150, 150)
                        cv2.rectangle(frame, tuple(box[:2]), tuple(box[2:]), color, 2)
                        label = "TARGET" if tid == target else "PENDING" if candidate else f"#{tid}"
                        cv2.putText(frame, f"{label} R:{score:.3f} D:{row['detection_confidence']:.2f}",
                                    (box[0], max(16, box[1]-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, settings.camera_jpeg_quality if self.camera else 82])
                if not ok:
                    raise ValueError("画面编码失败")
                elapsed = time.monotonic() - began
                with self.lock:
                    if not self.paused and epoch == self.epoch and not self.stop_event.is_set():
                        self.sequence += 1
                        self.packet = {"session_id": self.id, "sequence": self.sequence, "epoch": epoch, "source_time": seconds,
                            "width": width, "height": height, "target_state": state, "people": rows,
                            "processing_ms": round(elapsed * 1000, 1), "fps": round(1 / max(elapsed, interval), 2),
                            "sent_at": time.time(), "jpeg": base64.b64encode(jpeg).decode("ascii")}
                self.stop_event.wait(max(0, interval - elapsed))
        except Exception:
            log.exception("Realtime recognition failed: %s", self.id)
            self.error = "识别失败，请检查模型、lap 依赖和视频来源；详细信息见后端日志"
            self.status = "failed"
        finally:
            model_context.close()
            if cap:
                cap.release()
            if service:
                service.release(record=False)
            try:
                self.cleanup()
            finally:
                with self.lock:
                    if self.status not in TERMINAL:
                        self.status = "stopped"
                    self.finished_at = time.monotonic()
                    self.packet = None


class RealtimeManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.inputs = {}
        self.sessions = {}
        self.tickets = {}
        self.closed = threading.Event()
        self.sweeper = None

    @property
    def root(self):
        # Outside publicly mounted object/gallery directories.
        return Path(__file__).resolve().parents[2] / ".tmp" / "realtime"

    def start(self):
        with self.lock:
            if self.sweeper and self.sweeper.is_alive():
                return
            self.root.mkdir(parents=True, exist_ok=True)
            self.closed.clear()
            self.sweeper = threading.Thread(target=self._sweep, daemon=True, name="realtime-cleanup")
            self.sweeper.start()

    def _sweep(self):
        while not self.closed.wait(1):
            try:
                self.collect_expired()
            except OSError:
                log.exception("Temporary realtime cleanup will retry")

    def collect_expired(self):
        with self.lock:
            now = time.monotonic()
            for key, item in list(self.inputs.items()):
                if not item.claimed and now - item.created > 3600:
                    item.path.unlink(missing_ok=True)
                    del self.inputs[key]
            active_paths = {item.path for item in self.inputs.values()}
            for path in self.root.glob("*"):
                if path.is_file() and path not in active_paths and time.time() - path.stat().st_mtime > 3600:
                    path.unlink(missing_ok=True)
            for key, session in list(self.sessions.items()):
                with session.lock:
                    if session.status not in TERMINAL and not session.connected and now - session.disconnected_at > 30:
                        session.control("stop")
                    if session.finished_at and now - session.finished_at > 60:
                        del self.sessions[key]
            self.tickets = {k: v for k, v in self.tickets.items() if v[1] > now}


    def ticket(self, session):
        with self.lock:
            self.tickets = {k: v for k, v in self.tickets.items() if v[0] != session.id and v[1] > time.monotonic()}
            token = secrets.token_urlsafe(32)
            self.tickets[token] = (session.id, time.monotonic() + 30)
            return token

    def close(self):
        self.closed.set()
        if self.sweeper and self.sweeper.ident is not None:
            self.sweeper.join(timeout=2)
        for session in list(self.sessions.values()):
            session.control("stop")
        for session in list(self.sessions.values()):
            if session.thread.ident is not None:
                session.thread.join(timeout=5)


manager = RealtimeManager()
