"""Scenic authentication endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import scenic_database as database
from app.api.auth_dependencies import api_bearer, get_scenic_current_username as get_current_username
from app.core.config import get_settings
from app.core.security import decode_token, get_password_hash, verify_password
from app.schemas import EmailCodeLoginRequest, EmailCodeRequest, LoginRequest, TokenResponse, UserCreate
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
from app.services.rate_limit import RateLimitRule, rate_limiter, request_ip
from app.services.time_utils import utc_now


router = APIRouter()


def _audit(db, username, action, resource_type, resource_id=None, detail=""):
    return add_audit_log(db, database.AuditLog, username, action, resource_type, resource_id, detail)


def _issue_token_pair(db, username):
    return issue_token_pair(db, database.RefreshToken, username, scope="scenic")


def _find_user_by_account(db, account):
    return find_user_by_account(db, database.User, account)


def _ensure_registration_available(db, username, email):
    return ensure_registration_available(db, database.User, username, email)


def _get_email_login_user(db, email):
    return get_email_login_user(db, database.User, email)


def _ensure_email_code_request_allowed(db, email, purpose, username=None):
    return ensure_email_code_request_allowed(db, database.User, email, purpose, username)


def _create_email_code(db, email, purpose):
    return create_email_code(db, database.EmailLoginCode, email, purpose)


def _delete_email_code_record(db, record_id):
    return delete_email_code_record(db, database.EmailLoginCode, record_id, domain="scenic")


def _verify_email_code(db, email, code, purpose):
    return verify_email_code(db, database.EmailLoginCode, email, code, purpose)


def _claim_refresh_token(db, token_model, *, jti, username, token_hash, now):
    return claim_refresh_token(
        db,
        token_model,
        jti=jti,
        username=username,
        token_hash_value=token_hash,
        now=now,
    )


_normalize_username = normalize_username
_registration_role = registration_role
_send_email_code = send_email_code
_token_hash = token_hash
_user_profile_payload = user_profile_payload
_utc_now = utc_now


def scenic_register(user: UserCreate, request: Request, db: Session = Depends(database.get_db)):
    username = _normalize_username(user.username)
    email = normalize_email(user.email)
    rate_limiter.enforce(
        "scenic-register",
        RateLimitRule("ip", request_ip(request), 10, 600),
        RateLimitRule("account", username, 5, 600),
        RateLimitRule("email", email, 5, 600),
    )
    _ensure_registration_available(db, username, email)
    _verify_email_code(db, email, user.code, "register")
    role = _registration_role(db, database.User, email)
    db.add(database.User(username=username, email=email, hashed_password=get_password_hash(user.password), role=role))
    token_pair = _issue_token_pair(db, username)
    _audit(db, username, "scenic.auth.register", "user", username, email)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="用户名或邮箱已被注册") from exc
    return token_pair


def scenic_login(form: LoginRequest, request: Request, db: Session = Depends(database.get_db)):
    rate_limiter.enforce(
        "scenic-login",
        RateLimitRule("ip", request_ip(request), 20, 300),
        RateLimitRule("account", form.username, 10, 300),
    )
    user = _find_user_by_account(db, form.username)
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="用户已禁用")
    token_pair = _issue_token_pair(db, user.username)
    db.commit()
    return token_pair


def scenic_request_email_code(payload: EmailCodeRequest, request: Request, db: Session = Depends(database.get_db)):
    email = normalize_email(payload.email)
    rate_limiter.enforce(
        "scenic-email-code",
        RateLimitRule("ip", request_ip(request), 10, 600),
        RateLimitRule("account", payload.username or "", 5, 600),
        RateLimitRule("email", email, 5, 600),
    )
    _ensure_email_code_request_allowed(db, email, payload.purpose, payload.username)
    code, record = _create_email_code(db, email, payload.purpose)
    record_id = record.id
    db.commit()
    try:
        delivery = _send_email_code(email, code, payload.purpose)
    except HTTPException:
        _delete_email_code_record(db, record_id)
        raise
    response = {
        "status": "success",
        "message": delivery["message"],
        "purpose": payload.purpose,
        "expires_in_seconds": get_settings().email_code_ttl_minutes * 60,
    }
    if delivery.get("debug_code"):
        response["debug_code"] = delivery["debug_code"]
    return response


def scenic_email_code_login(payload: EmailCodeLoginRequest, request: Request, db: Session = Depends(database.get_db)):
    email = normalize_email(payload.email)
    rate_limiter.enforce(
        "scenic-email-login",
        RateLimitRule("ip", request_ip(request), 20, 300),
        RateLimitRule("email", email, 10, 300),
    )
    _verify_email_code(db, email, payload.code, "login")
    user = _get_email_login_user(db, email)
    token_pair = _issue_token_pair(db, user.username)
    _audit(db, user.username, "scenic.auth.email_code_login", "user", user.username, email)
    db.commit()
    return token_pair


def scenic_refresh(credentials: HTTPAuthorizationCredentials = Depends(api_bearer), db: Session = Depends(database.get_db)):
    if not credentials:
        raise HTTPException(status_code=401, detail="缺少刷新令牌")
    try:
        payload = decode_token(credentials.credentials, expected_type="refresh")
    except Exception:
        raise HTTPException(status_code=401, detail="无效或过期的刷新令牌")
    if payload.get("scope") != "scenic":
        raise HTTPException(status_code=401, detail="刷新令牌不属于景区系统")
    username = payload.get("sub")
    now = _utc_now()
    user = db.query(database.User).filter(database.User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已撤销")
    if not _claim_refresh_token(
        db,
        database.RefreshToken,
        jti=payload.get("jti"),
        username=username,
        token_hash=_token_hash(credentials.credentials),
        now=now,
    ):
        db.rollback()
        raise HTTPException(status_code=401, detail="Invalid or revoked refresh token")
    token_pair = _issue_token_pair(db, username)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return token_pair


def scenic_me(username: str = Depends(get_current_username), db: Session = Depends(database.get_db)):
    user = db.query(database.User).filter(database.User.username == username).first()
    return _user_profile_payload(user)


def scenic_logout(credentials: HTTPAuthorizationCredentials = Depends(api_bearer), db: Session = Depends(database.get_db)):
    if credentials:
        try:
            payload = decode_token(credentials.credentials, expected_type="refresh")
        except Exception:
            payload = {}
        if payload.get("scope") != "scenic":
            payload = {}
        token_record = db.query(database.RefreshToken).filter(database.RefreshToken.jti == payload.get("jti")).first()
        if token_record and not token_record.revoked_at:
            token_record.revoked_at = _utc_now()
            _audit(db, token_record.username, "scenic.auth.refresh_revoked", "refresh_token", token_record.jti, "")
            db.commit()
    return {"status": "success"}

router.add_api_route(
    "/api/v1/scenic/auth/register",
    scenic_register,
    methods=["POST"],
    response_model=TokenResponse,
)
router.add_api_route(
    "/api/v1/scenic/auth/login",
    scenic_login,
    methods=["POST"],
    response_model=TokenResponse,
)
router.add_api_route(
    "/api/v1/scenic/auth/email-code/request",
    scenic_request_email_code,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/auth/email-code/login",
    scenic_email_code_login,
    methods=["POST"],
    response_model=TokenResponse,
)
router.add_api_route(
    "/api/v1/scenic/auth/refresh",
    scenic_refresh,
    methods=["POST"],
    response_model=TokenResponse,
)
router.add_api_route("/api/v1/scenic/auth/me", scenic_me, methods=["GET"])
router.add_api_route("/api/v1/scenic/auth/logout", scenic_logout, methods=["POST"])
