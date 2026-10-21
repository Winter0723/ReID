"""Frozen pre-v1 API source; never import or mount in the active application.

See ``legacy/api/README.md`` for the compatibility baseline and support policy.
"""

import datetime
import io
import json
import os
import random
import shutil
import tempfile
import uuid
from pathlib import PurePosixPath, Path
from typing import List

import torch
from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import database
from app.api.common import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ACTIVE_SESSIONS,
    ALGORITHM,
    BASE_GALLERY_DIR,
    GUEST_USAGE,
    IMAGE_UPLOAD_EXTENSIONS,
    MAX_BATCH_UPLOAD_FILES,
    SECRET_KEY,
    VIDEO_UPLOAD_EXTENSIONS,
    ImagePixelLimitError,
    _load_verified_rgb_image,
    _public_gallery_path,
    check_guest_limit,
    clip_image_feature,
    clip_text_feature,
    copy_upload_limited,
    get_password_hash,
    get_request_context,
    get_settings,
    migrate_guest_data_to_user,
    process_gallery_features,
    process_video_and_extract_features,
    read_upload_limited,
    verify_password,
)
from app.services.auth_service import ensure_registration_available
from app.services.email_service import normalize_email, verify_email_code
from app.services.storage import UploadTooLargeError

router = APIRouter()

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=4, max_length=12)

def require_legacy_api_enabled():
    if not get_settings().enable_legacy_api:
        raise HTTPException(status_code=404, detail="旧版 Demo 接口已关闭，请使用 /api/v1")


LEGACY_ROUTE_OPTIONS = {
    "include_in_schema": get_settings().enable_legacy_api,
    "dependencies": [Depends(require_legacy_api_enabled)],
}


@router.post("/register", **LEGACY_ROUTE_OPTIONS)
def register(user: UserCreate, db: Session = Depends(database.get_db), x_guest_id: str = Header(None)):
    username = user.username.strip()
    email = normalize_email(user.email)
    ensure_registration_available(db, database.User, username, email)
    verify_email_code(db, database.EmailLoginCode, email, user.code, "register")

    db.add(database.User(username=username, email=email, hashed_password=get_password_hash(user.password)))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="用户名或邮箱已被注册") from exc

    if x_guest_id:
        migrate_guest_data_to_user(x_guest_id, username, db)

    return {"status": "success", "message": "注册成功"}

@router.post("/login", **LEGACY_ROUTE_OPTIONS)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(database.get_db), x_guest_id: str = Header(None)):
    account = form_data.username.strip()
    user = db.query(database.User).filter(database.User.username == account).first()
    if not user:
        user = db.query(database.User).filter(database.User.email == normalize_email(account)).first()
    if user and not user.is_active:
        raise HTTPException(status_code=403, detail="用户已禁用")
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if x_guest_id:
        migrate_guest_data_to_user(x_guest_id, user.username, db)

    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": user.username, "exp": expire}

    return {"access_token": jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM), "token_type": "bearer"}

@router.get("/gallery/status", **LEGACY_ROUTE_OPTIONS)
def get_gallery_status(background_tasks: BackgroundTasks, ctx: dict = Depends(get_request_context)):
    s_id, u_id = ctx["session_id"], ctx["id"]
    session_dir = os.path.join(BASE_GALLERY_DIR, u_id, s_id)
    
    if s_id not in ACTIVE_SESSIONS:
        if os.path.exists(session_dir) and any(f.lower().endswith(('.png', '.jpg')) for f in os.listdir(session_dir)):
            ACTIVE_SESSIONS[s_id] = {"status": "processing", "current": 0, "total": 0}
            background_tasks.add_task(process_gallery_features, s_id, session_dir)
            return ACTIVE_SESSIONS[s_id]
        return {"status": "idle", "current": 0, "total": 0}
    return {"status": ACTIVE_SESSIONS[s_id]["status"], "current": ACTIVE_SESSIONS[s_id]["current"], "total": ACTIVE_SESSIONS[s_id]["total"]}

@router.get("/gallery/preview", **LEGACY_ROUTE_OPTIONS)
def get_gallery_preview(ctx: dict = Depends(get_request_context)):
    session_dir = os.path.join(BASE_GALLERY_DIR, ctx["id"], ctx["session_id"])
    if not os.path.exists(session_dir): return {"previews": []}
    files = [f for f in os.listdir(session_dir) if f.lower().endswith(('.png', '.jpg'))]
    selected = random.sample(files, min(4, len(files)))
    urls = [f"/{session_dir.strip('./')}/{f}" for f in selected]
    return {"previews": urls}

@router.delete("/history/{history_id}", **LEGACY_ROUTE_OPTIONS)
def delete_history(history_id: int, ctx: dict = Depends(get_request_context), db: Session = Depends(database.get_db)):
    h = db.query(database.SearchHistory).filter(database.SearchHistory.id == history_id, database.SearchHistory.user_identifier == ctx["id"]).first()
    if h:
        db.delete(h)
        db.commit()
    return {"status": "success"}

@router.post("/upload/video", **LEGACY_ROUTE_OPTIONS)
def upload_video(background_tasks: BackgroundTasks, file: UploadFile = File(...), is_append: bool = False, ctx: dict = Depends(get_request_context)):
    safe_filename = PurePosixPath((file.filename or "upload-video").replace("\\", "/")).name
    if Path(safe_filename).suffix.lower() not in VIDEO_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的视频类型: {safe_filename}")
    s_id, u_id = ctx["session_id"], ctx["id"]
    session_dir = os.path.join(BASE_GALLERY_DIR, u_id, s_id)
    os.makedirs(session_dir, exist_ok=True)

    temp_video_path = os.path.join(session_dir, f"temp_{uuid.uuid4().hex}_{safe_filename}")
    try:
        with tempfile.TemporaryDirectory(dir=Path(session_dir).parent) as staging_name:
            staged_path = Path(staging_name) / safe_filename
            copy_upload_limited(
                file.file,
                staged_path,
                get_settings().max_upload_mb * 1024 * 1024,
            )
            session_path = Path(session_dir)
            if not is_append:
                for child in session_path.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
            os.replace(staged_path, temp_video_path)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    ACTIVE_SESSIONS[s_id] = {"features": None, "paths": [], "status": "processing", "current": 0, "total": 0}
    background_tasks.add_task(process_video_and_extract_features, s_id, session_dir, temp_video_path, safe_filename)

    mode_text = "追加" if is_append else "覆盖"
    return {"status": "success", "message": f"视频已接收，正在后台抽帧抠图并{mode_text}图库"}

@router.post("/upload/gallery", **LEGACY_ROUTE_OPTIONS)
def upload_gallery(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...), is_append: bool = False, ctx: dict = Depends(get_request_context)):
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一张图片")
    if len(files) > MAX_BATCH_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"单次最多上传 {MAX_BATCH_UPLOAD_FILES} 张图片")
    file_specs = []
    seen_names = set()
    for file in files:
        safe_filename = PurePosixPath((file.filename or "upload-image").replace("\\", "/")).name
        if Path(safe_filename).suffix.lower() not in IMAGE_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不支持的图片类型: {safe_filename}")
        if safe_filename in seen_names:
            raise HTTPException(status_code=400, detail=f"批量上传包含重复文件名: {safe_filename}")
        seen_names.add(safe_filename)
        file_specs.append((file, safe_filename))
    s_id, u_id = ctx["session_id"], ctx["id"]
    session_dir = os.path.join(BASE_GALLERY_DIR, u_id, s_id)
    os.makedirs(session_dir, exist_ok=True)

    max_total_bytes = get_settings().max_upload_mb * 1024 * 1024
    total_bytes = 0
    try:
        with tempfile.TemporaryDirectory(dir=Path(session_dir).parent) as staging_name:
            staging_dir = Path(staging_name)
            for file, safe_filename in file_specs:
                staged_path = staging_dir / safe_filename
                total_bytes += copy_upload_limited(file.file, staged_path, max_total_bytes)
                if total_bytes > max_total_bytes:
                    raise UploadTooLargeError(
                        f"批量图片总大小超过最大限制 {get_settings().max_upload_mb} MB"
                    )
                _load_verified_rgb_image(staged_path)

            session_path = Path(session_dir)
            if not is_append:
                for child in session_path.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
            for staged_path in staging_dir.iterdir():
                os.replace(staged_path, session_path / staged_path.name)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ImagePixelLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="上传文件不是有效图片") from exc

    ACTIVE_SESSIONS[s_id] = {"features": None, "paths": [], "status": "idle", "current": 0, "total": 0}
    background_tasks.add_task(process_gallery_features, s_id, session_dir)
    mode_text = "追加" if is_append else "覆盖"
    return {"status": "success", "message": f"开始{mode_text}处理 {len(files)} 张图片"}

def execute_search(features_tensor, ctx: dict, db: Session, query_type: str, query_content: str, top_k: int):
    s_id, u_id = ctx["session_id"], ctx["id"]
    gallery = ACTIVE_SESSIONS.get(s_id)
    
    if not gallery or gallery["status"] != "ready" or gallery["features"] is None:
        raise HTTPException(status_code=400, detail="图集未就绪")

    similarities = torch.matmul(features_tensor, gallery["features"].T).squeeze(0)
    top_k = min(top_k, len(gallery["paths"]))
    top_scores, top_indices = torch.topk(similarities, top_k)
    
    results = []
    for score, idx in zip(top_scores, top_indices):
        file_path = gallery["paths"][idx.item()]
        filename = os.path.basename(file_path)

        if filename.startswith("[VID]_"):
            parts = filename.replace("[VID]_", "").split("_time_")
            vid_name = parts[0]
            time_and_id = parts[1].split("_p")
            time_str = time_and_id[0].replace("-", ":")
            person_id = time_and_id[1].replace(".jpg", "")
            source_info = f"🎬 {vid_name}\n⏱️ {time_str} (目标 {person_id})"
        else:
            source_info = f"🖼️ {filename}"

        results.append({
            "path": _public_gallery_path(file_path),
            "similarity": round(score.item(), 4),
            "source_info": source_info
        })

    history = database.SearchHistory(user_identifier=u_id, session_id=s_id, query_type=query_type,
                                     query_content=query_content, results_json=json.dumps(results))
    db.add(history)
    db.commit()

    if ctx["type"] == "guest":
        usage = check_guest_limit(u_id)
        GUEST_USAGE[u_id] = {"date": usage["date"], "count": usage["count"] + 1}

    return {"status": "success", "results": results}

@router.post("/search/image", **LEGACY_ROUTE_OPTIONS)
def search_by_image(file: UploadFile = File(...), top_k: int = 5, ctx: dict = Depends(get_request_context), db: Session = Depends(database.get_db)):
    if ctx["type"] == "guest":
        check_guest_limit(ctx["id"])
    try:
        image_bytes = read_upload_limited(file.file, 25 * 1024 * 1024)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail="查询图片不能超过 25MB") from exc
    try:
        query_image = _load_verified_rgb_image(image_bytes)
    except ImagePixelLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="查询文件不是有效图片") from exc
    query_features = clip_image_feature(query_image)
    return execute_search(query_features, ctx, db, "image", file.filename, top_k)

@router.get("/search/text", **LEGACY_ROUTE_OPTIONS)
def search_by_text(query_text: str, top_k: int = 5, ctx: dict = Depends(get_request_context), db: Session = Depends(database.get_db)):
    if ctx["type"] == "guest":
        check_guest_limit(ctx["id"])
    text_features = clip_text_feature(query_text)
    return execute_search(text_features, ctx, db, "text", query_text, top_k)

@router.get("/history", **LEGACY_ROUTE_OPTIONS)
def get_history(ctx: dict = Depends(get_request_context), db: Session = Depends(database.get_db)):
    histories = db.query(database.SearchHistory).filter(database.SearchHistory.user_identifier == ctx["id"]).order_by(database.SearchHistory.created_at.desc()).all()
    results = []
    for h in histories:
        results.append({
            "id": h.id,
            "session_id": h.session_id,
            "type": h.query_type,
            "content": h.query_content,
            "created_at": h.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "results": json.loads(h.results_json)
        })
    return {"status": "success", "history": results}

@router.post("/system/cleanup", **LEGACY_ROUTE_OPTIONS)
def cleanup(ctx: dict = Depends(get_request_context), db: Session = Depends(database.get_db)):
    s_id, u_id = ctx["session_id"], ctx["id"]
    if ctx["type"] == "guest":
        db.query(database.SearchHistory).filter(database.SearchHistory.user_identifier == u_id).delete()
        db.commit()
        guest_dir = os.path.join(BASE_GALLERY_DIR, u_id)
        if os.path.exists(guest_dir):
            shutil.rmtree(guest_dir)
        ACTIVE_SESSIONS.pop(s_id, None)
        GUEST_USAGE.pop(u_id, None)
        return {"status": "success", "message": "游客数据已彻底销毁"}

    ACTIVE_SESSIONS.pop(s_id, None)
    return {"status": "success", "message": "用户会话已清理"}

