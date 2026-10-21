import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import database

from app.api.auth_dependencies import require_roles
from app.core.security import get_password_hash
from app.schemas import UserAdminCreate, UserAdminUpdate, UserOut, UserPasswordReset
from app.services.audit_service import add_audit_log
from app.services.auth_service import GLOBAL_ROLES, normalize_username
from app.services.email_service import normalize_email
from app.services.permission_service import ensure_admin_not_orphaned, user_admin_payload
from app.services.schema_service import schema_dict
from app.services.time_utils import utc_now

router = APIRouter()

@router.get("/api/v1/admin/users", response_model=List[UserOut])
def v1_admin_list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    admin: database.User = Depends(require_roles("admin")),
    db: Session = Depends(database.get_db),
):
    users = (
        db.query(database.User)
        .order_by(database.User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    usernames = [user.username for user in users]
    if not usernames:
        return []
    now = utc_now()
    refresh_counts = dict(
        db.query(database.RefreshToken.username, func.count(database.RefreshToken.id))
        .filter(
            database.RefreshToken.username.in_(usernames),
            database.RefreshToken.revoked_at.is_(None),
            database.RefreshToken.expires_at > now,
        )
        .group_by(database.RefreshToken.username)
        .all()
    )
    project_counts = dict(
        db.query(database.ProjectMember.username, func.count(database.ProjectMember.id))
        .filter(database.ProjectMember.username.in_(usernames))
        .group_by(database.ProjectMember.username)
        .all()
    )
    return [
        user_admin_payload(
            db,
            user=user,
            refresh_token_model=database.RefreshToken,
            project_member_model=database.ProjectMember,
            active_refresh_tokens=refresh_counts.get(user.username, 0),
            project_count=project_counts.get(user.username, 0),
        )
        for user in users
    ]


@router.post("/api/v1/admin/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def v1_admin_create_user(
    payload: UserAdminCreate,
    admin: database.User = Depends(require_roles("admin")),
    db: Session = Depends(database.get_db),
):
    username = normalize_username(payload.username)
    email = normalize_email(payload.email) if payload.email else None
    if payload.role not in GLOBAL_ROLES:
        raise HTTPException(status_code=400, detail="无效角色")
    if db.query(database.User).filter(database.User.username == username).first():
        raise HTTPException(status_code=400, detail="用户名已被注册")
    if email and db.query(database.User).filter(database.User.email == email).first():
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    user = database.User(
        username=username,
        email=email,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user)
    add_audit_log(
        db,
        database.AuditLog,
        admin.username,
        "admin.user_created",
        "user",
        username,
        json.dumps({"role": payload.role, "email": email, "is_active": payload.is_active}, ensure_ascii=False),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="用户名或邮箱已被注册") from exc
    db.refresh(user)
    return user_admin_payload(
        db,
        user=user,
        refresh_token_model=database.RefreshToken,
        project_member_model=database.ProjectMember,
    )


@router.patch("/api/v1/admin/users/{target_username}", response_model=UserOut)
def v1_admin_update_user(
    target_username: str,
    payload: UserAdminUpdate,
    admin: database.User = Depends(require_roles("admin")),
    db: Session = Depends(database.get_db),
):
    target = db.query(database.User).filter(database.User.username == target_username).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    next_role = payload.role if payload.role is not None else target.role
    next_is_active = payload.is_active if payload.is_active is not None else target.is_active
    ensure_admin_not_orphaned(db, database.User, target, next_role, next_is_active)
    if payload.role is not None:
        if payload.role not in GLOBAL_ROLES:
            raise HTTPException(status_code=400, detail="无效角色")
        target.role = payload.role
    if payload.email is not None:
        email = normalize_email(payload.email)
        existing = db.query(database.User).filter(database.User.email == email, database.User.username != target.username).first()
        if existing:
            raise HTTPException(status_code=400, detail="邮箱已被注册")
        target.email = email
    if payload.is_active is not None:
        if target.username == admin.username and payload.is_active is False:
            raise HTTPException(status_code=400, detail="不能禁用当前管理员")
        target.is_active = payload.is_active
        if not payload.is_active:
            db.query(database.RefreshToken).filter(
                database.RefreshToken.username == target.username,
                database.RefreshToken.revoked_at.is_(None),
            ).update({"revoked_at": utc_now()})
    add_audit_log(
        db,
        database.AuditLog,
        admin.username,
        "admin.user_updated",
        "user",
        target.username,
        json.dumps(schema_dict(payload, exclude_none=True), ensure_ascii=False),
    )
    db.commit()
    db.refresh(target)
    return user_admin_payload(
        db,
        user=target,
        refresh_token_model=database.RefreshToken,
        project_member_model=database.ProjectMember,
    )


@router.post("/api/v1/admin/users/{target_username}/password")
def v1_admin_reset_user_password(
    target_username: str,
    payload: UserPasswordReset,
    admin: database.User = Depends(require_roles("admin")),
    db: Session = Depends(database.get_db),
):
    target = db.query(database.User).filter(database.User.username == target_username).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    target.hashed_password = get_password_hash(payload.new_password)
    now = utc_now()
    updated = db.query(database.RefreshToken).filter(
        database.RefreshToken.username == target.username,
        database.RefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": now})
    add_audit_log(
        db,
        database.AuditLog,
        admin.username,
        "admin.password_reset",
        "user",
        target.username,
        f"revoked {updated}",
    )
    db.commit()
    return {"status": "success", "revoked": updated}


@router.post("/api/v1/admin/users/{target_username}/revoke-refresh-tokens")
def v1_admin_revoke_user_tokens(
    target_username: str,
    admin: database.User = Depends(require_roles("admin")),
    db: Session = Depends(database.get_db),
):
    target = db.query(database.User).filter(database.User.username == target_username).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    now = utc_now()
    updated = db.query(database.RefreshToken).filter(
        database.RefreshToken.username == target_username,
        database.RefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": now})
    add_audit_log(
        db,
        database.AuditLog,
        admin.username,
        "admin.refresh_tokens_revoked",
        "user",
        target_username,
        f"revoked {updated}",
    )
    db.commit()
    return {"status": "success", "revoked": updated}
