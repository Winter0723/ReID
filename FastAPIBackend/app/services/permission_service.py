from fastapi import HTTPException

from app.services.time_utils import utc_now
from app.services.asset_delivery import signed_asset_url


PROJECT_ROLES = {"owner", "operator", "viewer"}
PROJECT_PERMISSION_LEVELS = {"viewer": 1, "operator": 2, "owner": 3}
PROJECT_PERMISSION_MINIMUMS = {"read": 1, "write": 2, "manage": 3}


def ensure_project_membership(db, project_member_model, project_id: int, username: str, role: str = "owner"):
    member = (
        db.query(project_member_model)
        .filter(
            project_member_model.project_id == project_id,
            project_member_model.username == username,
        )
        .first()
    )
    if member:
        member.role = role
        return member
    member = project_member_model(project_id=project_id, username=username, role=role)
    db.add(member)
    return member


def project_member_role(db, project_member_model, project, username: str) -> str:
    if project.owner_username == username:
        ensure_project_membership(db, project_member_model, project.id, username, "owner")
        return "owner"
    member = (
        db.query(project_member_model)
        .filter(
            project_member_model.project_id == project.id,
            project_member_model.username == username,
        )
        .first()
    )
    return member.role if member else ""


def ensure_project_access(
    db,
    *,
    project_model,
    project_member_model,
    user_model,
    project_id: int,
    username: str,
    permission: str = "read",
):
    if permission not in PROJECT_PERMISSION_MINIMUMS:
        raise ValueError(f"Unsupported project permission: {permission}")
    project = db.query(project_model).filter(project_model.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    user = db.query(user_model).filter(user_model.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    if user.role == "admin":
        return project
    if user.role == "viewer" and permission in {"write", "manage"}:
        raise HTTPException(status_code=403, detail="当前角色无权执行该操作")
    member_role = project_member_role(db, project_member_model, project, username)
    if PROJECT_PERMISSION_LEVELS.get(member_role, 0) < PROJECT_PERMISSION_MINIMUMS[permission]:
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return project


def require_admin(db, user_model, username: str):
    user = db.query(user_model).filter(user_model.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要 admin 权限")
    return user


def active_admin_count(db, user_model) -> int:
    return db.query(user_model).filter(user_model.role == "admin", user_model.is_active.is_(True)).count()


def ensure_admin_not_orphaned(db, user_model, target, next_role: str | None = None, next_is_active: bool | None = None):
    role_after = target.role if next_role is None else next_role
    active_after = target.is_active if next_is_active is None else next_is_active
    if target.role == "admin" and target.is_active and (role_after != "admin" or not active_after):
        # Serialize admin membership changes across processes, including self-service.
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy import text
            db.execute(text("SELECT pg_advisory_xact_lock(713194201)"))
        else:
            # A no-op UPDATE takes SQLite's writer lock before counting.
            db.query(user_model).filter(user_model.role == "admin").update(
                {"role": user_model.role}, synchronize_session=False)
        if active_admin_count(db, user_model) <= 1:
            raise HTTPException(status_code=400, detail="至少需要保留一个启用中的管理员")


def user_admin_payload(
    db,
    *,
    user,
    refresh_token_model,
    project_member_model,
    active_refresh_tokens: int | None = None,
    project_count: int | None = None,
) -> dict:
    now = utc_now()
    if active_refresh_tokens is None:
        active_refresh_tokens = (
            db.query(refresh_token_model)
            .filter(
                refresh_token_model.username == user.username,
                refresh_token_model.revoked_at.is_(None),
                refresh_token_model.expires_at > now,
            )
            .count()
        )
    if project_count is None:
        project_count = db.query(project_member_model).filter(project_member_model.username == user.username).count()
    return {
        "public_id": user.public_id,
        "username": user.username,
        "email": user.email or "",
        "avatar_url": signed_asset_url(user.avatar_url) if user.avatar_url else "",
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at or utc_now(),
        "active_refresh_tokens": active_refresh_tokens,
        "project_count": project_count,
    }
