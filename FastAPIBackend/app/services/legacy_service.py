import datetime
import logging
import os
import shutil
from datetime import date

import cv2
import torch
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt.exceptions import InvalidTokenError as JWTError
from sqlalchemy.orm import Session

import database
from app.core.config import get_settings
from app.services.media_service import BASE_GALLERY_DIR, load_verified_rgb_image as _load_verified_rgb_image
from app.services.model_runtime import clip_image_feature, detect_people

GUEST_USAGE = {}
ACTIVE_SESSIONS = {}
logger = logging.getLogger("myreid.api.legacy")
SECRET_KEY = get_settings().secret_key
ALGORITHM = get_settings().algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = get_settings().access_token_expire_minutes
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

def get_request_context(request: Request, token: str = Depends(oauth2_scheme)):
    guest_id = request.headers.get("X-Guest-ID")
    session_id = request.headers.get("X-Session-ID")
    if not session_id: raise HTTPException(status_code=400, detail="缺失 Session ID")

    context = {"session_id": session_id}
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            context.update({"type": "user", "id": f"user_{payload.get('sub')}", "username": payload.get("sub")})
        except JWTError: raise HTTPException(status_code=401, detail="无效凭证")
    elif guest_id:
        context.update({"type": "guest", "id": f"guest_{guest_id}"})
    else: raise HTTPException(status_code=401, detail="缺少身份标识")
    return context

def create_access_token(data: dict):
    import datetime
    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def check_guest_limit(guest_identifier: str):
    today = str(date.today())
    usage = GUEST_USAGE.get(guest_identifier, {"date": today, "count": 0})
    if usage["date"] != today:
        usage = {"date": today, "count": 0}
    if usage["count"] >= 2:
        raise HTTPException(status_code=403, detail="访客每日只能进行2次检索，请注册登录获取无限制体验")
    return usage

def normalize_email(email: str) -> str:
    return (email or "").strip().lower()

def migrate_guest_data_to_user(guest_id: str, username: str, db: Session):
    guest_uid = f"guest_{guest_id}"
    user_uid = f"user_{username}"

    guest_dir = os.path.join(BASE_GALLERY_DIR, guest_uid)
    user_dir = os.path.join(BASE_GALLERY_DIR, user_uid)

    if os.path.exists(guest_dir):
        os.makedirs(user_dir, exist_ok=True)
        for session_folder in os.listdir(guest_dir):
            guest_session_path = os.path.join(guest_dir, session_folder)
            user_session_path = os.path.join(user_dir, session_folder)
            if os.path.isdir(guest_session_path):
                shutil.move(guest_session_path, user_session_path)
        shutil.rmtree(guest_dir)

    histories = db.query(database.SearchHistory).filter(
        database.SearchHistory.user_identifier == guest_uid
    ).all()

    for h in histories:
        h.user_identifier = user_uid
        h.results_json = h.results_json.replace(f"/{guest_uid}/", f"/{user_uid}/")
        ACTIVE_SESSIONS.pop(h.session_id, None)

    db.commit()
    GUEST_USAGE.pop(guest_uid, None)

def process_gallery_features(session_id: str, folder_path: str):
    global ACTIVE_SESSIONS
    ACTIVE_SESSIONS[session_id] = {"features": None, "paths": [], "status": "processing", "current": 0, "total": 0}

    features_list, paths_list = [], []
    image_files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    ACTIVE_SESSIONS[session_id]["total"] = len(image_files)

    if not image_files:
        ACTIVE_SESSIONS[session_id]["status"] = "idle"
        return

    for filename in image_files:
        img_path = os.path.join(folder_path, filename)
        try:
            image = _load_verified_rgb_image(img_path)
            img_features = clip_image_feature(image)
        except Exception:
            logger.warning("Skipping invalid gallery image %s", img_path, exc_info=True)
        else:
            features_list.append(img_features)
            paths_list.append(img_path)
        finally:
            ACTIVE_SESSIONS[session_id]["current"] += 1

    if not features_list:
        ACTIVE_SESSIONS[session_id]["status"] = "idle"
        ACTIVE_SESSIONS[session_id]["error"] = "图库中没有可处理的有效图片"
        return
    ACTIVE_SESSIONS[session_id]["features"] = torch.cat(features_list, dim=0)
    ACTIVE_SESSIONS[session_id]["paths"] = paths_list
    ACTIVE_SESSIONS[session_id]["status"] = "ready"

def process_video_and_extract_features(session_id: str, session_dir: str, video_path: str, original_filename: str):
    global ACTIVE_SESSIONS
    ACTIVE_SESSIONS[session_id]["status"] = "processing"

    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            raise ValueError("无法打开上传的视频")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 25

        frame_interval = max(1, int(fps))
        count = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if count % frame_interval == 0:
                sec = count // fps
                time_str = str(datetime.timedelta(seconds=int(sec)))
                time_safe = time_str.replace(":", "-")

                detections = detect_people(frame)
                for i, detection in enumerate(detections):
                    person_crop = detection.crop_from(frame)
                    if person_crop is None or person_crop.shape[0] < 60 or person_crop.shape[1] < 30:
                        continue

                    frame_filename = f"[VID]_{original_filename}_time_{time_safe}_p{i}.jpg"
                    frame_path = os.path.join(session_dir, frame_filename)
                    cv2.imwrite(frame_path, person_crop)
            count += 1
    except Exception as exc:
        ACTIVE_SESSIONS[session_id]["status"] = "failed"
        ACTIVE_SESSIONS[session_id]["error"] = str(exc)
        logger.exception("Failed to process legacy video %s", original_filename)
        return
    finally:
        cap.release()
        try:
            os.remove(video_path)
        except FileNotFoundError:
            pass

    process_gallery_features(session_id, session_dir)
