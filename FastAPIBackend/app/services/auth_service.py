import datetime
import hashlib
import uuid

from fastapi import HTTPException

from app.core.config import PRODUCTION_LIKE_ENVIRONMENTS, get_settings
from app.core.security import create_token
from app.schemas import TokenResponse
from app.services.email_service import normalize_email
from app.services.time_utils import utc_now
from app.services.asset_delivery import signed_asset_url


GLOBAL_ROLES = {"admin", "operator", "viewer"}


def normalize_username(username: str) -> str:
    return (username or "").strip()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def claim_refresh_token(
    db,
    token_model,
    *,
    jti: str,
    username: str,
    token_hash_value: str,
    now: datetime.datetime,
) -> bool:
    """Atomically rotate one still-valid refresh token."""

    updated = (
        db.query(token_model)
        .filter(
            token_model.jti == jti,
            token_model.username == username,
            token_model.token_hash == token_hash_value,
            token_model.revoked_at.is_(None),
            token_model.expires_at > now,
        )
        .update({"revoked_at": now}, synchronize_session=False)
    )
    return updated == 1


def issue_token_pair(db, refresh_token_model, username: str, *, scope: str) -> TokenResponse:
    settings = get_settings()
    refresh_jti = str(uuid.uuid4())
    token_scope = None if scope == "core" else scope
    refresh_token = create_token(username, "refresh", jti=refresh_jti, scope=token_scope)
    db.add(
        refresh_token_model(
            jti=refresh_jti,
            username=username,
            token_hash=token_hash(refresh_token),
            expires_at=utc_now() + datetime.timedelta(minutes=settings.refresh_token_expire_minutes),
        )
    )
    return TokenResponse(
        access_token=create_token(username, "access", jti=refresh_jti, scope=token_scope),
        refresh_token=refresh_token,
    )


def ensure_registration_available(db, user_model, username: str, email: str) -> None:
    if not username:
        raise HTTPException(status_code=400, detail="请输入用户名")
    if db.query(user_model).filter(user_model.username == username).first():
        raise HTTPException(status_code=400, detail="用户名已被注册")
    if db.query(user_model).filter(user_model.email == email).first():
        raise HTTPException(status_code=400, detail="邮箱已被注册")


def registration_role(db, user_model, email: str) -> str:
    """Assign bootstrap admin only through an explicitly controlled email."""

    settings = get_settings()
    bootstrap_email = settings.bootstrap_admin_email
    if bootstrap_email:
        return "admin" if normalize_email(email) == bootstrap_email else settings.public_registration_role
    if settings.app_env in PRODUCTION_LIKE_ENVIRONMENTS:
        return settings.public_registration_role
    return "admin" if db.query(user_model).count() == 0 else settings.public_registration_role


def find_user_by_account(db, user_model, account: str):
    account = (account or "").strip()
    user = db.query(user_model).filter(user_model.username == account).first()
    if user:
        return user
    return db.query(user_model).filter(user_model.email == normalize_email(account)).first()


def get_email_login_user(db, user_model, email: str):
    user = db.query(user_model).filter(user_model.email == normalize_email(email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="该邮箱尚未注册，请先注册账号")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="用户已禁用")
    return user


def user_profile_payload(user) -> dict:
    created_at = user.created_at or utc_now()
    return {
        "id": user.public_id,
        "username": user.username,
        "email": user.email or "",
        "avatar_url": signed_asset_url(user.avatar_url) if user.avatar_url else "",
        "role": user.role,
        "is_active": user.is_active,
        "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
