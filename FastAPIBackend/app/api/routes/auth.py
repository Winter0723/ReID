import os
import uuid

from PIL import Image
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import database

from app.api.auth_dependencies import api_bearer, get_current_username
from app.core.config import get_settings
from app.core.security import decode_token, get_password_hash, verify_password
from app.schemas import (
    EmailCodeLoginRequest,
    EmailCodeRequest,
    LoginRequest,
    PasswordChangeRequest,
    TokenResponse,
    UserCreate as V1UserCreate,
)
from app.services.audit_service import add_audit_log
from app.services.auth_service import (
    claim_refresh_token,
    ensure_registration_available,
    find_user_by_account,
    get_email_login_user,
    issue_token_pair,
    normalize_username,
    registration_role,
    token_hash,
    user_profile_payload,
)
from app.services.email_service import (
    create_email_code,
    delete_email_code_record,
    ensure_email_code_request_allowed,
    normalize_email,
    send_email_code,
    verify_email_code,
)
from app.services.media_service import (
    BASE_GALLERY_DIR,
    MAX_AVATAR_BYTES,
    ImagePixelLimitError,
    delete_gallery_path,
    load_verified_rgb_image,
)
from app.services.rate_limit import RateLimitRule, rate_limiter, request_ip
from app.services.storage import UploadTooLargeError, read_upload_limited
from app.services.time_utils import utc_now
from app.services.permission_service import ensure_admin_not_orphaned

router = APIRouter()

@router.post("/api/v1/auth/register", response_model=TokenResponse)
def v1_register(user: V1UserCreate, request: Request, db: Session = Depends(database.get_db)):
    username = normalize_username(user.username)
    email = normalize_email(user.email)
    rate_limiter.enforce(
        "core-register",
        RateLimitRule("ip", request_ip(request), 10, 600),
        RateLimitRule("account", username, 5, 600),
        RateLimitRule("email", email, 5, 600),
    )
    ensure_registration_available(db, database.User, username, email)
    verify_email_code(db, database.EmailLoginCode, email, user.code, "register")
    role = registration_role(db, database.User, email)
    db.add(database.User(username=username, email=email, hashed_password=get_password_hash(user.password), role=role))
    token_pair = issue_token_pair(db, database.RefreshToken, username, scope="core")
    add_audit_log(db, database.AuditLog, username, "auth.register", "user", username, email)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="用户名或邮箱已被注册") from exc
    return token_pair


@router.post("/api/v1/auth/login", response_model=TokenResponse)
def v1_login(form: LoginRequest, request: Request, db: Session = Depends(database.get_db)):
    rate_limiter.enforce(
        "core-login",
        RateLimitRule("ip", request_ip(request), 20, 300),
        RateLimitRule("account", form.username, 10, 300),
    )
    user = find_user_by_account(db, database.User, form.username)
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="用户已禁用")
    token_pair = issue_token_pair(db, database.RefreshToken, user.username, scope="core")
    db.commit()
    return token_pair


@router.post("/api/v1/auth/email-code/request")
def v1_request_email_code(payload: EmailCodeRequest, request: Request, db: Session = Depends(database.get_db)):
    email = normalize_email(payload.email)
    purpose = payload.purpose
    rate_limiter.enforce(
        "core-email-code",
        RateLimitRule("ip", request_ip(request), 10, 600),
        RateLimitRule("account", payload.username or "", 5, 600),
        RateLimitRule("email", email, 5, 600),
    )
    ensure_email_code_request_allowed(db, database.User, email, purpose, payload.username)
    code, record = create_email_code(db, database.EmailLoginCode, email, purpose)
    record_id = record.id
    db.commit()
    try:
        delivery = send_email_code(email, code, purpose)
    except HTTPException:
        delete_email_code_record(db, database.EmailLoginCode, record_id)
        raise
    response = {
        "status": "success",
        "message": delivery["message"],
        "purpose": purpose,
        "expires_in_seconds": get_settings().email_code_ttl_minutes * 60,
    }
    if delivery.get("debug_code"):
        response["debug_code"] = delivery["debug_code"]
    return response


@router.post("/api/v1/auth/email-code/login", response_model=TokenResponse)
def v1_email_code_login(payload: EmailCodeLoginRequest, request: Request, db: Session = Depends(database.get_db)):
    email = normalize_email(payload.email)
    rate_limiter.enforce(
        "core-email-login",
        RateLimitRule("ip", request_ip(request), 20, 300),
        RateLimitRule("email", email, 10, 300),
    )
    verify_email_code(db, database.EmailLoginCode, email, payload.code, "login")
    user = get_email_login_user(db, database.User, email)
    token_pair = issue_token_pair(db, database.RefreshToken, user.username, scope="core")
    add_audit_log(db, database.AuditLog, user.username, "auth.email_code_login", "user", user.username, email)
    db.commit()
    return token_pair


@router.post("/api/v1/auth/refresh", response_model=TokenResponse)
def v1_refresh(credentials: HTTPAuthorizationCredentials = Depends(api_bearer), db: Session = Depends(database.get_db)):
    if not credentials:
        raise HTTPException(status_code=401, detail="缺少刷新令牌")
    try:
        payload = decode_token(credentials.credentials, expected_type="refresh")
    except Exception:
        raise HTTPException(status_code=401, detail="无效或过期的刷新令牌")
    if payload.get("scope") not in (None, "core"):
        raise HTTPException(status_code=401, detail="刷新令牌不属于基础系统")
    username = payload.get("sub")
    now = utc_now()
    user = db.query(database.User).filter(database.User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已撤销")
    if not claim_refresh_token(
        db,
        database.RefreshToken,
        jti=payload.get("jti"),
        username=username,
        token_hash_value=token_hash(credentials.credentials),
        now=now,
    ):
        db.rollback()
        raise HTTPException(status_code=401, detail="Invalid or revoked refresh token")
    token_pair = issue_token_pair(db, database.RefreshToken, username, scope="core")
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return token_pair


@router.get("/api/v1/auth/me")
def v1_me(username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    user = db.query(database.User).filter(database.User.username == username).first()
    return user_profile_payload(user)


@router.post("/api/v1/auth/avatar")
def v1_upload_avatar(
    file: UploadFile = File(...),
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    user = db.query(database.User).filter(database.User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件")

    try:
        content = read_upload_limited(file.file, MAX_AVATAR_BYTES)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail="头像图片不能超过 5MB") from exc

    try:
        image = load_verified_rgb_image(content, transpose_exif=True)
    except ImagePixelLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="头像文件不是有效图片") from exc

    image.thumbnail((512, 512))
    avatar_dir = os.path.join(BASE_GALLERY_DIR, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    filename = f"{user.public_id or username}_{uuid.uuid4().hex}.jpg"
    avatar_path = os.path.join(avatar_dir, filename)
    temporary_path = f"{avatar_path}.tmp"
    try:
        image.save(temporary_path, "JPEG", quality=90, optimize=True)
        os.replace(temporary_path, avatar_path)
    finally:
        delete_gallery_path(temporary_path)

    previous_avatar_url = user.avatar_url
    user.avatar_url = f"/gallery/avatars/{filename}"
    add_audit_log(db, database.AuditLog, username, "auth.avatar_updated", "user", username, "")
    try:
        db.commit()
    except Exception:
        db.rollback()
        delete_gallery_path(avatar_path)
        raise
    if previous_avatar_url and previous_avatar_url.startswith("/gallery/avatars/"):
        old_relative = previous_avatar_url.removeprefix("/gallery/")
        delete_gallery_path(os.path.join(BASE_GALLERY_DIR, old_relative))
    db.refresh(user)
    return user_profile_payload(user)


@router.post("/api/v1/auth/password")
def v1_change_password(payload: PasswordChangeRequest, username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    user = db.query(database.User).filter(database.User.username == username).first()
    if not user or not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    user.hashed_password = get_password_hash(payload.new_password)
    now = utc_now()
    db.query(database.RefreshToken).filter(
        database.RefreshToken.username == username,
        database.RefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": now})
    add_audit_log(db, database.AuditLog, username, "auth.password_changed", "user", username, "")
    db.commit()
    return {"status": "success"}


@router.delete("/api/v1/auth/me")
def v1_deactivate_me(username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    user = db.query(database.User).filter(database.User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    ensure_admin_not_orphaned(db, database.User, user, next_is_active=False)
    user.is_active = False
    now = utc_now()
    db.query(database.RefreshToken).filter(
        database.RefreshToken.username == username,
        database.RefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": now})
    add_audit_log(db, database.AuditLog, username, "auth.account_deactivated", "user", username, "")
    db.commit()
    return {"status": "success"}


@router.post("/api/v1/auth/logout")
def v1_logout(credentials: HTTPAuthorizationCredentials = Depends(api_bearer), db: Session = Depends(database.get_db)):
    if not credentials:
        raise HTTPException(status_code=401, detail="缺少刷新令牌")
    try:
        payload = decode_token(credentials.credentials, expected_type="refresh")
    except Exception:
        raise HTTPException(status_code=401, detail="无效或过期的刷新令牌")
    if payload.get("scope") not in (None, "core"):
        raise HTTPException(status_code=401, detail="刷新令牌不属于基础系统")
    token_record = db.query(database.RefreshToken).filter(database.RefreshToken.jti == payload.get("jti")).first()
    if token_record and not token_record.revoked_at:
        token_record.revoked_at = utc_now()
        add_audit_log(
            db,
            database.AuditLog,
            token_record.username,
            "auth.refresh_revoked",
            "refresh_token",
            token_record.jti,
            "",
        )
        db.commit()
    return {"status": "success"}
