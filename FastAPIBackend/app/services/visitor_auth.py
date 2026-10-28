"""Opaque, revocable visitor sessions. Never accept staff JWTs or client OpenIDs."""
import ipaddress
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
import scenic_database as database
from app.core.config import get_settings
from app.services.time_utils import utc_now, as_utc
from app.services.rate_limit import RateLimitRule, rate_limiter

COOKIE = "myreid_visitor"
PREFIX = "/api/v1/scenic/visitor"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def account_provider(account):
    return {"visitor-browser": "browser", "visitor-test": "test"}.get(account.appid, "wechat")


def require_browser_mode():
    settings = get_settings()
    if settings.visitor_auth_provider == "test":
        raise HTTPException(404, "测试环境请使用测试游客入口")


def require_visitor(request: Request, db: Session = Depends(database.get_db)):
    token = request.cookies.get(COOKIE, "")
    session = db.get(database.VisitorSession, digest(token)) if token else None
    if not session or as_utc(session.expires_at) <= utc_now():
        raise HTTPException(401, "请从景区入口登录后继续")
    validate_visitor_request(request)
    account = db.get(database.VisitorAccount, session.account_id)
    if account and account.appid == "visitor-test" and get_settings().visitor_auth_provider != "test":
        raise HTTPException(401, "测试登录已关闭，请重新登录")
    if account and account.appid == "visitor-browser":
        try:
            require_browser_mode()
        except HTTPException:
            raise HTTPException(401, "浏览器登录已关闭，请从景区入口重新登录")
    if not account:
        raise HTTPException(401, "游客账号不存在")
    if request.method not in {"GET", "HEAD", "OPTIONS"} or request.url.path.endswith("/preview"):
        rate_limiter.enforce("visitor-work", RateLimitRule("account", str(account.id),
            get_settings().visitor_operations_per_hour, 3600))
        visit_id = request.path_params.get("visit_id")
        if visit_id is not None:
            # Starlette's raw path_params remain strings even when the route's
            # argument is annotated int. PostgreSQL rejects INTEGER = VARCHAR.
            try:
                visit_id = int(visit_id)
            except (TypeError, ValueError):
                raise HTTPException(422, "游览记录编号必须是整数") from None
            project_id = db.query(database.TouristSession.project_id).join(
                database.VisitorVisit, database.VisitorVisit.session_id == database.TouristSession.id).filter(
                database.VisitorVisit.id == visit_id, database.VisitorVisit.account_id == account.id).scalar()
            if project_id is not None:
                rate_limiter.enforce("visitor-project-work", RateLimitRule("project", str(project_id),
                    get_settings().visitor_project_operations_per_hour, 3600))
    return account


def validate_visitor_request(request: Request):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("X-Visitor-Request") != "1":
            raise HTTPException(403, "请求校验失败，请重新打开页面")
        origin = request.headers.get("origin")
        base = get_settings().visitor_public_base_url
        if origin and base:
            expected = urlsplit(base)
            if origin != f"{expected.scheme}://{expected.netloc}":
                raise HTTPException(403, "请求来源不匹配")


def establish_session(response, db, account):
    token = secrets.token_urlsafe(32)
    db.add(database.VisitorSession(token_hash=digest(token), account_id=account.id,
        expires_at=utc_now() + timedelta(days=30)))
    db.commit()
    response.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True,
        secure=get_settings().visitor_cookie_secure, samesite="lax", path=PREFIX)
    response.headers["Cache-Control"] = "no-store"


def owned_visit(db, visit_id, account, *, require_verified=True):
    row = db.query(database.VisitorVisit).filter_by(id=visit_id, account_id=account.id).first()
    if not row:
        raise HTTPException(404, "游览记录不存在")
    if require_verified and get_settings().visitor_require_staff_verification and not row.verified_at:
        raise HTTPException(403, "请到服务台核验本人、票据和照片后继续")
    return row


def visitor_origin_allowed(settings):
    """Explicit LAN opt-in; never allow public HTTP OAuth origins."""
    url = urlsplit(settings.visitor_public_base_url)
    if not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        return False
    if url.scheme == "https":
        return True
    if url.scheme != "http" or not settings.visitor_allow_lan_http or settings.visitor_cookie_secure:
        return False
    try:
        address = ipaddress.ip_address(url.hostname)
        return any(address in ipaddress.ip_network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    except ValueError:
        return False
