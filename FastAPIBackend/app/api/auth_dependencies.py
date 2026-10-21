from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import database
import scenic_database
from app.core.security import decode_token
from app.services.asset_delivery import has_valid_asset_signature
from app.services.time_utils import utc_now


api_bearer = HTTPBearer(auto_error=False)


def _authenticated_username(credentials, db, user_model, *, scope: str) -> str:
    if not credentials:
        raise HTTPException(status_code=401, detail="缺少访问令牌")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="无效或过期的访问令牌") from exc
    token_scope = payload.get("scope")
    if (scope == "core" and token_scope not in (None, "core")) or (scope != "core" and token_scope != scope):
        detail = "访问令牌不属于基础系统" if scope == "core" else "访问令牌不属于景区系统"
        raise HTTPException(status_code=401, detail=detail)
    username = payload.get("sub")
    token_model = database.RefreshToken if scope == "core" else scenic_database.RefreshToken
    user = db.query(user_model).join(token_model, token_model.username == user_model.username).filter(
        user_model.username == username, user_model.is_active.is_(True),
        token_model.jti == payload.get("jti"), token_model.username == username,
        token_model.revoked_at.is_(None), token_model.expires_at > utc_now(),
    ).first()
    if user is None:
        raise HTTPException(status_code=401, detail="登录会话已撤销，请重新登录")
    return username


def get_current_username(
    credentials: HTTPAuthorizationCredentials = Depends(api_bearer),
    db=Depends(database.get_db),
):
    return _authenticated_username(credentials, db, database.User, scope="core")


def get_scenic_current_username(
    credentials: HTTPAuthorizationCredentials = Depends(api_bearer),
    db=Depends(scenic_database.get_db),
):
    return _authenticated_username(credentials, db, scenic_database.User, scope="scenic")


def get_core_asset_username(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(api_bearer),
    db=Depends(database.get_db),
):
    if has_valid_asset_signature(request):
        return None
    return _authenticated_username(credentials, db, database.User, scope="core")


def get_scenic_asset_username(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(api_bearer),
    db=Depends(scenic_database.get_db),
):
    if has_valid_asset_signature(request):
        return None
    return _authenticated_username(credentials, db, scenic_database.User, scope="scenic")


def require_roles(*roles: str):
    async def dependency(username: str = Depends(get_current_username), db=Depends(database.get_db)):
        user = db.query(database.User).filter(database.User.username == username).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="用户不存在或已禁用")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return dependency
