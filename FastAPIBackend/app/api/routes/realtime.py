"""JWT/project-scoped REST and single-use ticket WebSocket transport."""
import asyncio
import base64
import platform
import time
from typing import Literal
from uuid import uuid4
from urllib.parse import urlparse

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, model_validator

import database
from app.api.auth_dependencies import get_current_username
from app.api.routes.projects import _ensure_project_access
from app.core.config import get_settings
from app.services.realtime import Input, Session, TERMINAL, manager
from app.services.storage import storage

router = APIRouter(prefix="/api/v1/projects/{project_id}/realtime", tags=["realtime"])


class Source(BaseModel):
    media_id: int | None = Field(default=None, gt=0)
    input_id: str | None = None
    camera: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_source(self):
        if sum(value is not None for value in (self.media_id, self.input_id, self.camera)) != 1:
            raise ValueError("必须且只能选择一个视频来源")
        if self.camera is not None:
            self.camera = self.camera.strip()
            from app.services.camera_policy import validate_camera_source
            self.camera = validate_camera_source(self.camera)
            if any(ord(c) < 32 for c in self.camera):
                raise ValueError("相机地址无效")
        return self


class StartRequest(BaseModel):
    reference_id: str
    crop: tuple[float, float, float, float]
    source: Source
    show_others: bool = False

    @model_validator(mode="after")
    def validate_crop(self):
        x1, y1, x2, y2 = self.crop
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("裁剪区域必须为有效的归一化坐标")
        return self


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "stop"]


def owned_input(key, project, owner, kind):
    item = manager.inputs.get(key)
    if not item or item.project != project or item.owner != owner or item.kind != kind:
        raise HTTPException(404, "临时素材不存在或已过期")
    if item.claimed:
        raise HTTPException(409, "临时素材正在使用")
    return item


def owned_session(project_id, session_id, username, db):
    _ensure_project_access(project_id, username, db, permission="write")
    session = manager.sessions.get(session_id)
    if not session or session.project != project_id or session.owner != username:
        raise HTTPException(404, "识别会话不存在")
    return session


def camera_devices():
    from app.services import camera_bridge
    from app.services.camera_policy import public_camera_devices
    if camera_bridge.enabled():
        return public_camera_devices(camera_bridge.devices())
    # Enumerate device names only; never probe/open virtual or absent cameras.
    from app.services.camera_stream import _camera_names
    return public_camera_devices([{"source": str(index), "label": name} for index, name in enumerate(_camera_names())])


@router.get("/cameras")
def cameras(project_id: int, username=Depends(get_current_username), db=Depends(database.get_db)):
    _ensure_project_access(project_id, username, db, permission="write")
    return {"devices": camera_devices()}


def video_preview(path):
    cap = cv2.VideoCapture(str(path))
    try:
        ok, frame = cap.read()
        if not ok:
            raise HTTPException(422, "无法解码此视频")
        height, width = frame.shape[:2]
        scale = min(1.0, 1280 / width, 720 / height)
        if scale < 1:
            frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise HTTPException(422, "无法生成视频预览")
        return {"width": width, "height": height,
                "preview": "data:image/jpeg;base64," + base64.b64encode(encoded).decode()}
    finally:
        cap.release()


@router.get("/media/{media_id}/preview")
def preview_media(project_id: int, media_id: int, username=Depends(get_current_username), db=Depends(database.get_db)):
    _ensure_project_access(project_id, username, db, permission="write")
    media = db.query(database.MediaFile).filter(database.MediaFile.id == media_id,
                                               database.MediaFile.project_id == project_id).first()
    if not media or media.media_type not in {"video", "camera"}:
        raise HTTPException(404, "项目视频不存在")
    path = storage.path_for(media.object_key)
    if not path.is_file():
        raise HTTPException(404, "项目视频文件不可用")
    return video_preview(path)


@router.post("/inputs")
def upload_input(project_id: int, kind: Literal["image", "video"], file: UploadFile = File(...),
                 username=Depends(get_current_username), db=Depends(database.get_db)):
    _ensure_project_access(project_id, username, db, permission="write")
    manager.start()
    key = uuid4().hex
    path = manager.root / key
    limit = 20 * 1024 * 1024 if kind == "image" else 1024 * 1024 * 1024
    with manager.lock:
        if sum(item.owner == username for item in manager.inputs.values()) >= 8:
            raise HTTPException(429, "临时素材过多，请删除未使用素材或稍后重试")
        item = Input(key, project_id, username, path, kind, claimed=True)
        manager.inputs[key] = item
    try:
        size = 0
        with path.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "文件超过大小限制")
                output.write(chunk)
        result = {"id": key, "kind": kind}
        if kind == "image":
            with Image.open(path) as original:
                if original.width * original.height > 25_000_000:
                    raise HTTPException(413, "参考照片像素过大")
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((1920, 1920))
            image.save(path, format="JPEG", quality=92)
            from app.services.model_runtime import detect_people
            w, h = image.size
            detections = detect_people(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), min_confidence=0.25)
            result.update(width=w, height=h, preview="data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode(),
                          boxes=[{"box": [d.x1 / w, d.y1 / h, d.x2 / w, d.y2 / h],
                                  "confidence": d.confidence} for d in detections])
        else:
            result.update(video_preview(path))
        with manager.lock:
            item.claimed = False
        return result
    except Exception as exc:
        with manager.lock:
            manager.inputs.pop(key, None)
        path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, "素材解析失败，请检查文件和模型配置") from exc
    finally:
        file.file.close()


@router.delete("/inputs/{input_id}")
def delete_input(project_id: int, input_id: str, username=Depends(get_current_username), db=Depends(database.get_db)):
    _ensure_project_access(project_id, username, db, permission="write")
    with manager.lock:
        item = manager.inputs.get(input_id)
        if not item or item.owner != username or item.project != project_id:
            raise HTTPException(404, "素材不存在")
        if item.claimed:
            raise HTTPException(409, "素材正在使用")
        item.path.unlink(missing_ok=True)
        del manager.inputs[input_id]
    return {"status": "deleted"}


@router.post("/sessions")
def start_session(project_id: int, body: StartRequest, username=Depends(get_current_username), db=Depends(database.get_db)):
    _ensure_project_access(project_id, username, db, permission="write")
    manager.start()
    with manager.lock:
        if any(s.thread.is_alive() for s in manager.sessions.values()):
            raise HTTPException(409, "实时识别资源忙，当前只允许一个活动会话")
        reference = owned_input(body.reference_id, project_id, username, "image")
        inputs = [reference]
        if body.source.input_id is not None:
            video = owned_input(body.source.input_id, project_id, username, "video")
            inputs.append(video)
            source = video.path
        elif body.source.media_id is not None:
            media = db.query(database.MediaFile).filter(database.MediaFile.id == body.source.media_id,
                                                       database.MediaFile.project_id == project_id).first()
            if not media or media.media_type not in {"video", "camera"}:
                raise HTTPException(404, "项目视频不存在")
            source = storage.path_for(media.object_key)
            if not source.is_file():
                raise HTTPException(404, "项目视频文件不可用")
        else:
            source = body.source.camera
            if source.isdigit() and platform.system() == "Windows":
                available = {item["source"] for item in camera_devices()}
                if source not in available:
                    raise HTTPException(422, "USB 摄像头未接入或所选设备为 Iriun；请选择已连接的 USB 摄像头")
        def cleanup():
            with manager.lock:
                for item in inputs:
                    item.path.unlink(missing_ok=True)
                    manager.inputs.pop(item.id, None)
        session = Session(project_id, username, reference.path, body.crop, source,
                          body.source.camera is not None, body.show_others, cleanup)
        for item in inputs:
            item.claimed = True
        manager.sessions[session.id] = session
        session.thread.start()
    return session.snapshot()


@router.get("/sessions/{session_id}")
def session_status(project_id: int, session_id: str, username=Depends(get_current_username), db=Depends(database.get_db)):
    return owned_session(project_id, session_id, username, db).snapshot()


@router.post("/sessions/{session_id}/control")
def control(project_id: int, session_id: str, body: ControlRequest, username=Depends(get_current_username), db=Depends(database.get_db)):
    session = owned_session(project_id, session_id, username, db)
    session.control(body.action)
    return session.snapshot()


@router.post("/sessions/{session_id}/stream-ticket")
def ticket(project_id: int, session_id: str, username=Depends(get_current_username), db=Depends(database.get_db)):
    session = owned_session(project_id, session_id, username, db)
    if session.status in TERMINAL or session.stop_event.is_set():
        raise HTTPException(409, "识别会话已结束")
    return {"ticket": manager.ticket(session), "expires_in": 30}


@router.websocket("/sessions/{session_id}/stream")
async def stream(websocket: WebSocket, project_id: int, session_id: str, ticket: str = ""):
    allowed = {origin.strip() for origin in get_settings().realtime_origins.split(",")}
    if websocket.headers.get("origin") not in allowed:
        await websocket.close(code=1008)
        return
    with manager.lock:
        credential = manager.tickets.pop(ticket, None)
        session = manager.sessions.get(session_id)
        valid = credential and credential[0] == session_id and credential[1] > time.monotonic()
        if not valid or not session or session.project != project_id:
            session = None
        elif session.connected or session.status in TERMINAL or session.stop_event.is_set():
            session = None
        else:
            # Revalidate membership/user at connection time, not only at ticket issuance.
            with database.SessionLocal() as db:
                try:
                    _ensure_project_access(project_id, session.owner, db, permission="write")
                    user = db.query(database.User).filter(database.User.username == session.owner).first()
                    if not user or not user.is_active:
                        raise HTTPException(403)
                except HTTPException:
                    session = None
            if session:
                with session.lock:
                    session.connected = True
    if session is None:
        await websocket.close(code=1008)
        return
    try:
        await websocket.accept()
        last = 0
        while True:
            with session.lock:
                packet = session.packet
                status = session.status
            if status in TERMINAL:
                await websocket.send_json({"type": "status", **session.snapshot()})
                break
            if packet and packet["sequence"] > last:
                await asyncio.wait_for(websocket.send_json({"type": "frame", **packet}), timeout=5)
                ack = await asyncio.wait_for(websocket.receive_json(), timeout=10)
                if ack.get("action") == "stop":
                    session.control("stop")
                    break
                if ack.get("ack") != packet["sequence"]:
                    await websocket.close(code=1008)
                    break
                last = packet["sequence"]
                with session.lock:
                    session.acked = last
            else:
                # Heartbeat requires a response to detect disconnects even while paused.
                await websocket.send_json({"type": "status", **session.snapshot()})
                message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
                if message.get("action") == "stop":
                    session.control("stop")
                await asyncio.sleep(0.01 if session.camera else 0.1)
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, ValueError):
        pass
    finally:
        with session.lock:
            session.connected = False
            session.disconnected_at = time.monotonic()
        try:
            await websocket.close()
        except RuntimeError:
            pass
