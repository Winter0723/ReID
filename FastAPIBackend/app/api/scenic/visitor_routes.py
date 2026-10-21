"""Visitor-only DTOs and ownership checks; shared scenic business capabilities."""
import base64
import hashlib
import hmac
import io
import json
import secrets
import time
import uuid
from datetime import timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, Response as ApiResponse
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import scenic_database as database
from app.api.auth_dependencies import get_scenic_current_username
from app.api.scenic.service_adapters import audit, ensure_access
from app.core.config import get_settings
from app.scenic_editing_schemas import TouristReference, DraftUpdate
from app.services.asset_delivery import deliver_object, signed_asset_url, has_valid_asset_signature
from app.services.storage import storage, UploadTooLargeError
from app.services.media_service import load_verified_rgb_image, ImagePixelLimitError
from app.services.time_utils import utc_now, as_utc
from app.services.visitor_auth import PREFIX, COOKIE, digest, establish_session, require_visitor, owned_visit, visitor_origin_allowed, require_browser_mode, validate_visitor_request
from app.services.visitor_production import MESSAGES, output_complete
from app.workers.scenic_editing import lock_path
from filelock import FileLock, Timeout
from contextlib import contextmanager
from pathlib import Path

def private_response(response: ApiResponse):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(dependencies=[Depends(private_response)])


@router.get(PREFIX + "/runtime")
def runtime():
    settings = get_settings()
    if settings.visitor_auth_provider == "browser":
        require_browser_mode()
    from app.services.visitor_testing import require_test_mode
    for kind in ("auth", "payment"):
        if getattr(settings, "visitor_" + kind + "_provider") == "test":
            require_test_mode(kind)
    return {"auth_provider": settings.visitor_auth_provider,
        "payment_provider": settings.visitor_payment_provider,
        "testing": "test" in {settings.visitor_auth_provider, settings.visitor_payment_provider}}


class BrowserLogin(BaseModel):
    project_id: int = Field(gt=0)


@router.post(PREFIX + "/auth/browser")
def browser_login(payload: BrowserLogin, request: Request, response: ApiResponse, db: Session = Depends(database.get_db)):
    require_browser_mode()
    validate_visitor_request(request)
    service(db, payload.project_id)
    token = request.cookies.get(COOKIE)
    previous = db.get(database.VisitorSession, digest(token)) if token else None
    account = db.get(database.VisitorAccount, previous.account_id) if previous and as_utc(previous.expires_at) > utc_now() else None
    if account and account.appid == "visitor-browser":
        # Reuse the browser's identity across reloads and scenic projects.
        return {"authenticated": True}
    account = database.VisitorAccount(appid="visitor-browser", openid=secrets.token_urlsafe(32))
    db.add(account)
    db.flush()
    if previous:
        db.delete(previous)
    establish_session(response, db, account)
    return {"authenticated": True}


class TestLogin(BaseModel):
    project_id: int = Field(gt=0)
    visitor: Literal["A", "B"]
    invite_code: str = Field(min_length=1, max_length=256)


@router.post(PREFIX + "/auth/test")
def test_login(payload: TestLogin, request: Request, response: ApiResponse, db: Session = Depends(database.get_db)):
    from app.services.visitor_testing import require_test_mode
    from app.services.visitor_auth import validate_visitor_request
    settings = require_test_mode("auth")
    validate_visitor_request(request)
    if not secrets.compare_digest(payload.invite_code.encode(), settings.visitor_test_invite_code.encode()):
        raise HTTPException(403, "测试邀请码不正确")
    service(db, payload.project_id)
    # Fixed server-owned identities. Never accept an arbitrary OpenID or account ID.
    openid = f"site-{payload.project_id}-visitor-{payload.visitor}"
    account = db.query(database.VisitorAccount).filter_by(appid="visitor-test", openid=openid).first()
    if not account:
        account = database.VisitorAccount(appid="visitor-test", openid=openid)
        db.add(account)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            account = db.query(database.VisitorAccount).filter_by(appid="visitor-test", openid=openid).one()
    old_token = request.cookies.get(COOKIE)
    if old_token:
        db.query(database.VisitorSession).filter_by(token_hash=digest(old_token)).delete()
    establish_session(response, db, account)
    return {"authenticated": True, "visitor": payload.visitor}


@router.post(PREFIX + "/payments/wechat/notify")
async def wechat_notify(request: Request):
    from app.api.scenic.order_routes import _process_wechat_payment_notification, PAYMENT_NOTIFICATION_MAX_BYTES
    from app.services.payments import WechatNativePaymentClient
    from starlette.concurrency import run_in_threadpool
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > PAYMENT_NOTIFICATION_MAX_BYTES:
            raise HTTPException(413, "支付通知请求体过大")
        chunks.append(chunk)
    return await run_in_threadpool(_process_wechat_payment_notification, dict(request.headers), b"".join(chunks), WechatNativePaymentClient())


def service(db, project_id):
    row = db.get(database.VisitorService, project_id)
    if not row or not row.enabled or not row.project:
        raise HTTPException(404, "该景区暂未开放游客自助服务")
    return row


def visit_payload(row):
    verified = not get_settings().visitor_require_staff_verification or bool(row.verified_at)
    order = row.order
    expires = as_utc(order.paid_at or order.created_at) + timedelta(hours=get_settings().scenic_delivery_token_ttl_hours) if order else None
    return {"id": row.id, "project_id": row.session.project_id, "name": row.session.project.name,
        "created_at": as_utc(row.session.created_at), "checked_out_at": as_utc(row.session.checked_out_at),
        "stage": row.stage, "message": "请到服务台核验本人、票据和照片" if get_settings().visitor_require_staff_verification and not row.verified_at else MESSAGES.get(row.stage, "正在处理"), "price_cents": row.price_cents,
        "identity_verified": bool(row.verified_at),
        "payment_provider": get_settings().visitor_payment_provider,
        "order": {"id": order.id, "order_no": order.order_no, "status": order.status,
            "amount_cents": order.amount_cents, "payment_provider": order.payment_provider, "expires_at": expires, "claimed": bool(order.claimed_at)} if order else None,
        "selection_available": bool(verified and row.session.checked_out_at and row.session.segments and row.stage not in {"sealing", "waiting", "matching", "rendering"} and not order),
        "can_retry": bool(verified and row.stage == "review" and not order),
        "render_progress": [{"orientation": variant.orientation, "status": variant.status, "progress": variant.progress,
            "message": {"queued": "排队中", "running": "制作中", "succeeded": "已完成", "failed": "制作未完成，可以重试"}.get(variant.status, "正在处理")}
            for variant in row.vlog.variants] if row.vlog else [],
        "video_available": verified and row.stage == "ready", "expired": bool(expires and utc_now() >= expires),
        "delivery_page_url": f"{PREFIX}/deliveries/{order.delivery_token}/page" if verified and order and order.status == "paid" and expires > utc_now() else "",
        "download_urls": {orientation: signed_asset_url(f"{PREFIX}/downloads/{row.id}/{order.id}/{orientation}")
            for orientation in ("landscape", "portrait")} if verified and order and order.status == "paid" and expires > utc_now() else {}}


@router.get(PREFIX + "/sites/{project_id}")
def get_site(project_id: int, db: Session = Depends(database.get_db)):
    row = service(db, project_id)
    return {"id": row.project_id, "name": row.project.name, "description": row.description,
        "consent_text": row.consent_text, "consent_version": row.consent_version, "price_cents": row.price_cents}


@router.get(PREFIX + "/auth/wechat")
def oauth_start(project_id: int, db: Session = Depends(database.get_db)):
    service(db, project_id)
    settings = get_settings()
    if not settings.visitor_wechat_appid or not settings.visitor_wechat_secret or not visitor_origin_allowed(settings):
        raise HTTPException(503, "微信登录尚未开放，请联系景区工作人员")
    state = secrets.token_urlsafe(24)
    payload = base64.urlsafe_b64encode(json.dumps([state, project_id, int(time.time()) + 600]).encode()).decode()
    signature = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    callback = settings.visitor_public_base_url.rstrip("/") + PREFIX + "/auth/callback"
    response = RedirectResponse("https://open.weixin.qq.com/connect/oauth2/authorize?" + urlencode({
        "appid": settings.visitor_wechat_appid, "redirect_uri": callback, "response_type": "code",
        "scope": "snsapi_base", "state": state}) + "#wechat_redirect")
    response.set_cookie("visitor_oauth", payload + "." + signature, max_age=600,
        httponly=True, secure=settings.visitor_cookie_secure, samesite="lax", path=PREFIX + "/auth")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get(PREFIX + "/auth/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(database.get_db)):
    settings = get_settings()
    try:
        payload, signature = request.cookies.get("visitor_oauth", "").rsplit(".", 1)
        expected = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        saved_state, project_id, expires = json.loads(base64.urlsafe_b64decode(payload))
        if not hmac.compare_digest(signature, expected) or not hmac.compare_digest(state, saved_state) or expires < time.time() or not code:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, "微信授权已失效，请从景区入口重试")
    service(db, project_id)
    try:
        with httpx.Client(timeout=15) as client:
            result = client.get("https://api.weixin.qq.com/sns/oauth2/access_token", params={
                "appid": settings.visitor_wechat_appid, "secret": settings.visitor_wechat_secret,
                "code": code, "grant_type": "authorization_code"})
            result.raise_for_status()
            data = result.json()
        openid = data.get("openid")
        if not isinstance(openid, str) or not openid or data.get("errcode"):
            raise ValueError()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "微信授权失败，请返回入口重试")
    account = db.query(database.VisitorAccount).filter_by(appid=settings.visitor_wechat_appid, openid=openid).first()
    if not account:
        account = database.VisitorAccount(appid=settings.visitor_wechat_appid, openid=openid)
        db.add(account)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            account = db.query(database.VisitorAccount).filter_by(appid=settings.visitor_wechat_appid, openid=openid).one()
    response = RedirectResponse(settings.visitor_public_base_url.rstrip("/") + f"/visitor/#/sites/{project_id}/register", status_code=303)
    response.delete_cookie("visitor_oauth", path=PREFIX + "/auth")
    establish_session(response, db, account)
    return response


@router.get(PREFIX + "/me")
def me(account=Depends(require_visitor)):
    return {"authenticated": True}


@router.post(PREFIX + "/logout")
def logout(request: Request, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    db.query(database.VisitorSession).filter_by(token_hash=digest(request.cookies[COOKIE])).delete()
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path=PREFIX)
    return response


def verify_reference(image, box):
    """Reject ambiguous photos instead of silently choosing the largest person."""
    import numpy as np
    from app.services.model_runtime import detect_people
    width, height = image.size
    x1, y1, x2, y2 = box
    crop = image.crop((int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)))
    if crop.width < 60 or crop.height < 120:
        raise HTTPException(422, "人物区域太小，请上传清晰的半身或全身照")
    people = detect_people(np.asarray(crop)[:, :, ::-1].copy(), min_confidence=0.25)
    if len(people) != 1:
        raise HTTPException(422, "所选区域需包含且仅包含本人，请重新框选或更换照片")


@router.post(PREFIX + "/sites/{project_id}/visits")
def register(project_id: int, request_key: str = Form(..., min_length=16, max_length=64),
    consent_version: str = Form(...), consent_granted: bool = Form(False),
    target_box: str = Form(...), file: UploadFile = File(...),
    account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    config = service(db, project_id)
    from app.services.rate_limit import RateLimitRule, rate_limiter
    rate_limiter.enforce("visitor-project-work", RateLimitRule("project", str(project_id),
        get_settings().visitor_project_operations_per_hour, 3600))
    existing = db.query(database.VisitorVisit).filter_by(account_id=account.id, request_key=request_key).first()
    if existing:
        if existing.session.project_id != project_id:
            raise HTTPException(409, "登记请求与景区不匹配")
        return visit_payload(existing)
    if not consent_granted or consent_version != config.consent_version:
        raise HTTPException(422, "请阅读并同意最新拍摄授权说明")
    try:
        box = TouristReference(entry_target_box=json.loads(target_box)).entry_target_box
        if not box:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "请框选照片中的本人")
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "照片不能超过 10 MB")
    stored = storage.save_upload(f"scenic/{project_id}", "reference.jpg", io.BytesIO(raw), category="tourists/entry")
    try:
        image = load_verified_rgb_image(storage.path_for(stored.object_key))
        verify_reference(image, box)
        session = database.TouristSession(project_id=project_id, ticket_code="V" + uuid.uuid4().hex,
            visitor_label="自助游客", consent_granted=True, consent_version=config.consent_version,
            consent_granted_at=utc_now(), entry_target_box_json=json.dumps(box),
            entry_image_object_key=stored.object_key, entry_image_filename="reference.jpg",
            created_by_username=config.project.owner_username)
        db.add(session)
        db.flush()
        row = database.VisitorVisit(account_id=account.id, session_id=session.id, request_key=request_key,
            consent_text=config.consent_text, price_cents=config.price_cents)
        db.add(row)
        db.flush()
        audit(db, session.created_by_username, "visitor.self_registered", "tourist_session", str(session.id), f"visitor_account={account.id}; visit={row.id}")
        db.commit()
        return visit_payload(row)
    except IntegrityError:
        db.rollback()
        storage.delete(stored.object_key)
        existing = db.query(database.VisitorVisit).filter_by(account_id=account.id, request_key=request_key).first()
        if existing and existing.session.project_id == project_id:
            return visit_payload(existing)
        raise HTTPException(409, "登记请求冲突，请刷新后重试")
    except Exception as exc:
        db.rollback()
        storage.delete(stored.object_key)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, (ValueError, ImagePixelLimitError, UploadTooLargeError)):
            raise HTTPException(422, "照片无法读取，请更换清晰照片") from exc
        raise HTTPException(503, "人物检测暂不可用，请稍后重试") from exc


@router.get(PREFIX + "/visits")
def visits(account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    rows = db.query(database.VisitorVisit).filter_by(account_id=account.id).order_by(database.VisitorVisit.id.desc()).limit(100).all()
    return {"visits": [visit_payload(row) for row in rows]}


@router.get(PREFIX + "/visits/{visit_id}")
def visit(visit_id: int, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    return visit_payload(owned_visit(db, visit_id, account, require_verified=False))


@router.post(PREFIX + "/visits/{visit_id}/finish")
def finish(visit_id: int, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    row = owned_visit(db, visit_id, account, require_verified=False)
    if row.stage != "visiting":
        return visit_payload(row)
    try:
        with FileLock(lock_path(f"visitor-{row.id}"), timeout=.2):
            db.refresh(row)
            if row.stage == "visiting":
                row.session.checked_out_at = utc_now()
                row.session.status = "checked_out"
                row.stage, row.requested_at = "sealing", utc_now()
                if get_settings().visitor_require_staff_verification and not row.verified_at:
                    row.stage, row.reason = "review", "identity_unverified"
                audit(db, row.session.created_by_username, "visitor.production_requested", "tourist_session", str(row.session_id), f"visitor_account={account.id}")
                db.commit()
    except Timeout as exc:
        db.expire_all()
        if row.stage == "visiting":
            raise HTTPException(409, "结束游览请求正在保存，请稍后重试", headers={"Retry-After": "1"}) from exc
    return visit_payload(row)


# Visitor editing reuses the scenic planner, canonical edit validation and RQ
# renderer. All media access is scoped to this account's own matched intervals.
class VisitorDraftRequest(BaseModel):
    segment_ids: list[int] = Field(min_length=1, max_length=100)
    match_revision: str = Field(min_length=1, max_length=64)
    config: dict = Field(default_factory=dict)


class VisitorRetryRequest(BaseModel):
    action: Literal["auto", "match"] = "auto"


class VisitorRenderRequest(BaseModel):
    revision: int = Field(gt=0)


@contextmanager
def visitor_edit_lock(db, row):
    try:
        with FileLock(lock_path(f"visitor-{row.id}"), timeout=1):
            db.refresh(row)
            if not row.session.checked_out_at:
                raise HTTPException(409, "请先结束本次游览")
            if row.order_id:
                raise HTTPException(409, "付款申请已绑定当前成片，无法替换版本")
            yield
    except Timeout as exc:
        raise HTTPException(409, "当前请求正在处理，请稍后重试") from exc


def visitor_segments(row):
    from types import SimpleNamespace
    from app.services.visitor_production import within_visit
    result = []
    for segment in row.session.segments:
        media = segment.media_file
        if media is None or segment.project_id != row.session.project_id or segment.original_media_object_key != media.object_key:
            continue
        if segment.similarity < 0.72 or not within_visit(segment, row.session, segment.start_seconds, segment.end_seconds):
            continue
        result.append(segment)
    return sorted(result, key=lambda segment: (segment.sort_order, segment.id))


def match_revision(row, segments):
    evidence = sorted((segment.id, segment.media_id, segment.original_media_object_key, segment.start_seconds, segment.end_seconds) for segment in segments)
    return hashlib.sha256(json.dumps(evidence, separators=(",", ":")).encode()).hexdigest()


def visitor_draft_sources(row):
    """Revalidate legacy drafts against this visitor's current matched windows."""
    if not row.draft or row.draft.session_id != row.session_id:
        return None
    sources = json.loads(row.draft.data_json).get("sources", [])
    allowed = visitor_segments(row)
    for source in sources:
        candidates = [segment for segment in allowed if segment.media_id == source.get("media_id")
            and segment.original_media_object_key == source.get("object_key")]
        if not candidates:
            return None
        if source.get("kind") == "image":
            continue
        start = float(source.get("available_start_seconds", source["start_seconds"]))
        end = float(source.get("available_end_seconds", source["end_seconds"]))
        covered = start
        for segment in sorted(candidates, key=lambda item: item.start_seconds):
            if segment.start_seconds <= covered + 1e-6:
                covered = max(covered, segment.end_seconds)
        if covered < end - 1e-6:
            return None
    return {source["id"]: source for source in sources} if sources else None


def visitor_draft_payload(row):
    draft = row.draft
    if not draft or visitor_draft_sources(row) is None:
        return None
    data = json.loads(draft.data_json)
    root_fields = {"template_id", "title", "brand", "music_id", "music_volume", "original_volume", "landscape_seconds", "portrait_seconds", "use_tracking", "cover_clip_id", "result_type"}
    clip_fields = {"id", "kind", "start_seconds", "end_seconds", "included", "locked", "keep_audio", "focus_x", "focus_y", "crop_mode", "title", "camera_name", "width", "height", "has_audio", "available_start_seconds", "available_end_seconds"}
    def public_clip(clip):
        result = {key: value for key, value in clip.items() if key in clip_fields}
        base = f"{PREFIX}/visits/{row.id}/draft/sources/{clip['id']}"
        result.update(thumbnail_url=base + "/thumbnail", preview_url=base + "/preview")
        return result
    public = {key: value for key, value in data.items() if key in root_fields}
    public["sources"] = [public_clip(clip) for clip in data.get("sources", [])]
    public["variants"] = {orientation: [public_clip(clip) for clip in clips] for orientation, clips in data.get("variants", {}).items()}
    public["warnings"] = ["编排已按可用画面与实际时长调整，请查看横竖版镜头。"] if data.get("warnings") else []
    return {"id": draft.id, "revision": draft.revision, "data": public}


@router.get(PREFIX + "/visits/{visit_id}/editor")
def visitor_editor(visit_id: int, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    row = owned_visit(db, visit_id, account)
    if not row.session.checked_out_at:
        raise HTTPException(409, "请先结束本次游览")
    from app.api.scenic.editing_routes import editing_catalog, prepare_camera_sources
    prepare_camera_sources(row.session, db, {segment.media_id for segment in row.session.segments if segment.media_id}, refs_only=True)
    catalog = editing_catalog(row.session.created_by_username)
    for track in catalog.get("music", []):
        track["preview_url"] = f"{PREFIX}/visits/{row.id}/music/{track['id']}/preview"
    segments = visitor_segments(row)
    return {"editable": not row.order_id and row.stage not in {"sealing", "waiting", "matching", "rendering"},
        "match_revision": match_revision(row, segments), "catalog": catalog,
        "segments": [{"id": segment.id, "camera_name": segment.camera_name, "similarity": segment.similarity,
            "start_seconds": segment.start_seconds, "end_seconds": segment.end_seconds, "included": segment.included,
            "thumbnail_url": f"{PREFIX}/visits/{row.id}/segments/{segment.id}/thumbnail",
            "preview_url": f"{PREFIX}/visits/{row.id}/segments/{segment.id}/preview"} for segment in segments],
        "draft": visitor_draft_payload(row)}


@router.get(PREFIX + "/visits/{visit_id}/music/{music_id}/preview")
def visitor_music_preview(visit_id: int, music_id: str, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    row = owned_visit(db, visit_id, account)
    from app.api.scenic.editing_routes import music_preview
    return music_preview(music_id, row.session.created_by_username)


@router.post(PREFIX + "/visits/{visit_id}/draft")
def visitor_create_draft(visit_id: int, payload: VisitorDraftRequest, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    from app.api.scenic.editing_routes import auto_draft
    from app.scenic_editing_schemas import EditConfig
    row = owned_visit(db, visit_id, account)
    with visitor_edit_lock(db, row):
        if row.stage in {"sealing", "waiting", "matching", "rendering"}:
            raise HTTPException(409, "请等待当前处理完成")
        segments = visitor_segments(row)
        if match_revision(row, segments) != payload.match_revision:
            raise HTTPException(409, "匹配结果已更新，请重新查看并选择片段")
        selected = payload.segment_ids
        allowed = {segment.id for segment in segments}
        if len(set(selected)) != len(selected) or not set(selected) <= allowed:
            raise HTTPException(422, "只能选择本次游览匹配到的有效片段")
        try:
            config = EditConfig.model_validate(payload.config)
        except ValueError as exc:
            raise HTTPException(422, "剪辑设置无效") from exc
        config.previous_draft_id = None
        fingerprint = hashlib.sha256(json.dumps([payload.match_revision, selected, config.model_dump()], sort_keys=True).encode()).hexdigest()
        if row.draft and json.loads(row.draft.data_json).get("visitor_selection_hash") == fingerprint:
            return {"visit": visit_payload(row), "draft": visitor_draft_payload(row)}
        for segment in row.session.segments:
            segment.included = segment.id in selected
            if segment.included:
                segment.sort_order = selected.index(segment.id) + 1
        db.flush()
        result = auto_draft(row.session_id, config, row.session.created_by_username, db)
        row.draft_id = result["id"]
        db.flush()
        db.expire(row, ["draft"])
        data = json.loads(row.draft.data_json)
        data["visitor_selection_hash"] = fingerprint
        row.draft.data_json = json.dumps(data, ensure_ascii=False)
        row.stage, row.reason, row.internal_error = "editing", "", ""
        audit(db, row.session.created_by_username, "visitor.clips_selected", "tourist_session", str(row.session_id), f"visit={row.id}; selected={len(selected)}")
        db.commit()
        return {"visit": visit_payload(row), "draft": visitor_draft_payload(row)}


@router.put(PREFIX + "/visits/{visit_id}/draft")
def visitor_save_draft(visit_id: int, payload: "DraftUpdate", account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    from app.api.scenic.editing_routes import save_draft, canonical_edit
    row = owned_visit(db, visit_id, account)
    with visitor_edit_lock(db, row):
        if row.stage not in {"editing", "selecting", "review", "ready"} or not row.draft:
            raise HTTPException(409, "请先选择片段并编排短片")
        if visitor_draft_sources(row) is None:
            raise HTTPException(409, "匹配结果已变化，请重新挑选片段并编排")
        saved = json.loads(row.draft.data_json)
        data = canonical_edit(payload.data, saved)
        for clips in data["variants"].values():
            for clip in clips:
                if clip["start_seconds"] < clip.get("available_start_seconds", clip["start_seconds"]) - 1e-6 or clip["end_seconds"] > clip.get("available_end_seconds", clip["end_seconds"]) + 1e-6:
                    raise HTTPException(422, "只能裁切已匹配的片段，不能扩大至其他摄像头画面")
        save_draft(row.session_id, row.draft_id, payload, row.session.created_by_username, db)
        row.stage, row.reason = "editing", ""
        db.commit()
        return {"visit": visit_payload(row), "draft": visitor_draft_payload(row)}


@router.post(PREFIX + "/visits/{visit_id}/render")
def visitor_render(visit_id: int, payload: VisitorRenderRequest, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    from app.api.scenic.editing_routes import submit_edited_vlog
    from app.scenic_editing_schemas import VlogSubmit
    row = owned_visit(db, visit_id, account)
    with visitor_edit_lock(db, row):
        if not row.draft or payload.revision != row.draft.revision:
            raise HTTPException(409, "草稿版本已更新，请重新保存后制作")
        if visitor_draft_sources(row) is None:
            raise HTTPException(409, "匹配结果已变化，请重新挑选片段并编排")
        if row.stage in {"sealing", "waiting", "matching"}:
            raise HTTPException(409, "请等待匹配完成")
        result = submit_edited_vlog(row.session_id, VlogSubmit(draft_id=row.draft_id, revision=payload.revision), row.session.created_by_username, db)
        row.vlog_id = result["vlog"]["id"]
        row.stage, row.reason, row.internal_error, row.requested_at = "rendering", "", "", utc_now()
        db.commit()
        return visit_payload(row)


@router.post(PREFIX + "/visits/{visit_id}/retry")
def visitor_retry(visit_id: int, payload: VisitorRetryRequest | None = None, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    from app.api.scenic.editing_routes import retry_variant
    row = owned_visit(db, visit_id, account)
    with visitor_edit_lock(db, row):
        if row.stage not in {"review", "selecting", "editing", "ready"}:
            raise HTTPException(409, "当前请求无需重试")
        if (not payload or payload.action == "auto") and row.stage == "review" and row.vlog and row.vlog.status in {"partial", "failed"}:
            for variant in list(row.vlog.variants):
                if variant.status == "failed":
                    retry_variant(row.vlog_id, variant.id, row.session.created_by_username, db)
            row.stage = "rendering"
        else:
            row.stage, row.draft_id, row.vlog_id = "waiting", None, None
        row.reason, row.internal_error, row.requested_at = "", "", utc_now()
        db.commit()
        return visit_payload(row)


def _visitor_clip_response(row, media, start, end):
    from fastapi.responses import FileResponse
    from app.services.media_service import video_preview_payload
    from botocore.exceptions import BotoCoreError, ClientError
    from app.services.camera_stream import scenic_camera_stream_manager
    if end - start < 0.3 - 1e-6:
        raise HTTPException(422, "片段有效时长不足，请选择其他画面")
    try:
        result = video_preview_payload(media, start_seconds=start, duration_seconds=end-start,
            storage_project_id=f"scenic/{row.session.project_id}", stream_manager=scenic_camera_stream_manager,
            minimum_duration_seconds=0.3)
    except FileNotFoundError as exc:
        raise HTTPException(404, "该片段素材暂不可用，请选择其他片段或重新匹配") from exc
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "素材服务暂不可用，请稍后重试") from exc
    except HTTPException as exc:
        if exc.status_code >= 500:
            raise HTTPException(503, "此片段暂不可预览，请稍后重试或选择其他画面") from exc
        raise
    key = result.get("preview_object_key")
    if not key:
        raise HTTPException(404, "该片段暂不可预览")
    path = (get_settings().object_storage_dir / key).resolve()
    if not path.is_relative_to(get_settings().object_storage_dir.resolve()) or not path.is_file():
        raise HTTPException(404, "该片段暂不可预览")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.get(PREFIX + "/visits/{visit_id}/segments/{segment_id}/{asset}")
def visitor_segment_asset(visit_id: int, segment_id: int, asset: Literal["thumbnail", "preview"], account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    row = owned_visit(db, visit_id, account)
    segment = next((segment for segment in visitor_segments(row) if segment.id == segment_id), None)
    if not segment:
        raise HTTPException(404, "匹配片段不存在")
    if asset == "thumbnail":
        return deliver_object(segment.thumbnail_object_key, content_type="image/jpeg")
    from app.api.scenic.editing_routes import prepare_camera_sources
    prepare_camera_sources(row.session, db, {segment.media_id})
    return _visitor_clip_response(row, segment.media_file, segment.start_seconds, segment.end_seconds)


@router.get(PREFIX + "/visits/{visit_id}/draft/sources/{source_id}/{asset}")
def visitor_draft_asset(visit_id: int, source_id: str, asset: Literal["thumbnail", "preview"],
    start_seconds: float | None = Query(default=None, ge=0), end_seconds: float | None = Query(default=None, ge=0),
    account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    row = owned_visit(db, visit_id, account)
    sources = visitor_draft_sources(row) or {}
    source = sources.get(source_id)
    if not source:
        raise HTTPException(404, "剪辑镜头不存在")
    media = db.get(database.MediaFile, source["media_id"])
    if not media or media.project_id != row.session.project_id or media.object_key != source["object_key"]:
        raise HTTPException(404, "剪辑素材已更新，请重新编排")
    if asset == "thumbnail":
        return deliver_object(source.get("thumbnail_object_key") or source.get("identity_thumbnail_object_key") or "", content_type="image/jpeg")
    if source["kind"] == "image":
        return deliver_object(source["object_key"], content_type="image/jpeg")
    available_start = source.get("available_start_seconds", source["start_seconds"])
    available_end = source.get("available_end_seconds", source["end_seconds"])
    if (start_seconds is None) != (end_seconds is None):
        raise HTTPException(422, "请同时提供镜头入出点")
    start = available_start if start_seconds is None else start_seconds
    end = min(available_end, start + 60) if end_seconds is None else end_seconds
    if start < available_start - 1e-6 or end > available_end + 1e-6 or end - start > 60:
        raise HTTPException(422, "只能预览已匹配范围内的镜头")
    return _visitor_clip_response(row, media, start, end)


@router.get(PREFIX + "/visits/{visit_id}/video/{orientation}/{asset}")
def video(visit_id: int, orientation: str, asset: str, download: bool = False,
    account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    row = owned_visit(db, visit_id, account)
    if row.stage != "ready" or orientation not in {"landscape", "portrait"} or asset not in {"preview", "full"}:
        raise HTTPException(404, "视频暂未开放")
    vlog = row.vlog
    if asset == "full":
        if not row.order or row.order.status != "paid":
            raise HTTPException(402, "购买后可领取完整视频")
        if visit_payload(row)["expired"]:
            raise HTTPException(410, "领取有效期已结束，请联系景区工作人员")
        vlog = row.order.vlog_job
    variant = next((v for v in vlog.variants if v.orientation == orientation and v.status == "succeeded"), None) if vlog else None
    if not variant:
        raise HTTPException(404, "视频暂不可用")
    key = getattr(variant, asset + "_object_key")
    return deliver_object(key, filename=f"我的游览回忆-{orientation}.mp4", content_type="video/mp4", download=download)


@router.post(PREFIX + "/visits/{visit_id}/pay")
def pay(visit_id: int, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    from app.services.payments import WechatNativePaymentClient, WechatNativeOrderRequest, PaymentError
    from app.api.scenic.system_routes import _apply_wechat_payment_status
    row = owned_visit(db, visit_id, account)
    settings = get_settings()
    testing = settings.visitor_payment_provider == "test"
    manual = settings.visitor_payment_provider == "manual"
    provider = "visitor_manual" if manual else "visitor_test" if testing else "wechat_jsapi"
    if testing:
        from app.services.visitor_testing import require_test_mode
        require_test_mode("payment")
    if not testing and not manual and settings.wechat_pay_appid != account.appid:
        raise HTTPException(503, "微信支付配置尚未就绪")
    with FileLock(lock_path(f"visitor-{row.id}"), timeout=30):
        db.refresh(row)
        if row.stage != "ready" or not output_complete(row.vlog):
            raise HTTPException(409, "请等待视频制作完成后再购买")
        if row.order and row.order.status == "paid":
            return {"visit": visit_payload(row), "payment": None}
        if not row.order:
            order = database.ScenicOrder(session_id=row.session_id, vlog_job_id=row.vlog_id,
                order_no="SV" + uuid.uuid4().hex.upper(), amount_cents=row.price_cents, currency="CNY",
                payment_method=provider,
                payment_provider=provider, delivery_token=secrets.token_hex(32))
            db.add(order)
            db.flush()
            row.order_id = order.id
            db.commit()  # persist merchant order number before contacting WeChat
            db.refresh(row)
        order = row.order
        expected_provider = provider
        if order.payment_provider != expected_provider:
            raise HTTPException(409, "订单支付模式不匹配，请联系工作人员")
        if manual:
            if order.status != "pending":
                raise HTTPException(409, "订单需要工作人员处理")
            if order.provider_trade_state != "WAITING_STAFF":
                order.provider_trade_state = "WAITING_STAFF"
                audit(db, row.session.created_by_username, "visitor.payment_requested", "tourist_session", str(row.session_id), order.order_no)
                db.commit()
            return {"visit": visit_payload(row), "payment": {"provider": "manual"}}
        if testing:
            from app.services.visitor_testing import checkout
            payment = checkout(order)
            db.commit()
            return {"visit": visit_payload(row), "payment": payment}
        client = WechatNativePaymentClient(settings)
        try:
            if order.payment_code_url:
                status = client.query_order(order.order_no)
                _apply_wechat_payment_status(order, status)
                db.commit()
                if order.status == "paid":
                    return {"visit": visit_payload(row), "payment": None}
                if status.trade_state in {"CLOSED", "REVOKED", "REFUND", "PAYERROR"}:
                    raise HTTPException(409, "订单需要工作人员处理，请联系景区服务台")
            if not order.payment_code_url or not order.payment_expires_at or as_utc(order.payment_expires_at) <= utc_now():
                order.payment_code_url = client.create_jsapi_order(WechatNativeOrderRequest(
                    order_no=order.order_no, description="景区游览回忆视频", amount_cents=order.amount_cents, currency=order.currency), account.openid)
                order.payment_expires_at = utc_now() + timedelta(minutes=110)
                db.commit()
            return {"visit": visit_payload(row), "payment": client.jsapi_parameters(order.payment_code_url)}
        except PaymentError as exc:
            raise HTTPException(503, "微信支付暂不可用，请稍后重试；订单已保留") from exc


@router.get(PREFIX + "/downloads/{visit_id}/{order_id}/{orientation}")
def download_video(visit_id: int, order_id: int, orientation: str, request: Request, db: Session = Depends(database.get_db)):
    # This short-lived capability is issued only in an owner's authenticated DTO.
    # Binding the immutable order and orientation lets WeChat hand it to another
    # browser without exporting the visitor's session cookie or storage keys.
    if not has_valid_asset_signature(request):
        raise HTTPException(403, "下载链接已失效，请回到微信重新获取")
    row = db.query(database.VisitorVisit).filter_by(id=visit_id, order_id=order_id).first()
    if not row:
        raise HTTPException(404, "视频不存在")
    return video(visit_id, orientation, "full", True, db.get(database.VisitorAccount, row.account_id), db)


def visitor_delivery_order(delivery_token, db):
    from app.api.scenic.order_routes import _delivery_order
    order = _delivery_order(delivery_token, db)
    row = db.query(database.VisitorVisit).filter_by(order_id=order.id, session_id=order.session_id).first()
    if not row:
        raise HTTPException(404, "领取链接不存在")
    if order.status != "paid":
        raise HTTPException(402, "订单尚未确认付款")
    vlog = order.vlog_job
    if row.stage != "ready" or row.vlog_id != order.vlog_job_id or not vlog or vlog.status != "succeeded" or \
            {v.orientation for v in vlog.variants if v.status == "succeeded" and v.full_object_key} != {"landscape", "portrait"}:
        raise HTTPException(409, "完整成片尚不可领取")
    return order


@router.get(PREFIX + "/deliveries/{delivery_token}/page", response_class=HTMLResponse)
def visitor_delivery_page(delivery_token: str, db: Session = Depends(database.get_db)):
    from app.api.scenic.order_routes import render_delivery_page
    return render_delivery_page(visitor_delivery_order(delivery_token, db), PREFIX + "/deliveries")


@router.get(PREFIX + "/deliveries/{delivery_token}/variants/{variant_id}/{asset}")
def visitor_delivery_variant(delivery_token: str, variant_id: int, asset: str, download: bool = Query(False), db: Session = Depends(database.get_db)):
    from app.api.scenic.order_routes import scenic_delivery_variant
    visitor_delivery_order(delivery_token, db)
    return scenic_delivery_variant(delivery_token, variant_id, asset, download, db)


@router.post(PREFIX + "/deliveries/{delivery_token}/confirm-receipt", response_class=RedirectResponse)
def visitor_delivery_receipt(delivery_token: str, db: Session = Depends(database.get_db)):
    from app.api.scenic.order_routes import confirm_order_receipt
    # The opaque order capability authorizes this form, not a visitor cookie.
    # Receipt does not change payment or permit another video/version.
    confirm_order_receipt(visitor_delivery_order(delivery_token, db), db)
    return RedirectResponse(f"{PREFIX}/deliveries/{delivery_token}/page", status_code=303,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})


@router.post(PREFIX + "/visits/{visit_id}/payment-status")
def payment_status(visit_id: int, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    from app.services.payments import WechatNativePaymentClient, PaymentError
    from app.api.scenic.system_routes import _apply_wechat_payment_status
    row = owned_visit(db, visit_id, account)
    with FileLock(lock_path(f"visitor-{row.id}"), timeout=30):
        db.refresh(row)
        if row.order and row.order.status != "paid":
            try:
                if row.order.payment_provider == "visitor_test":
                    from app.services.visitor_testing import sync_test_payment
                    sync_test_payment(row.order)
                elif row.order.payment_provider == "visitor_manual":
                    pass  # Only authorized staff can confirm this order.
                elif row.order.payment_provider == "wechat_jsapi" and get_settings().visitor_payment_provider == "wechat":
                    _apply_wechat_payment_status(row.order, WechatNativePaymentClient().query_order(row.order.order_no))
                else:
                    raise HTTPException(409, "订单支付模式不匹配")
                db.commit()
            except PaymentError as exc:
                raise HTTPException(503, "支付结果确认中，请稍后刷新") from exc
    return visit_payload(row)


class TestPayment(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    outcome: Literal["success", "cancel", "failure", "delayed", "duplicate"]


@router.post(PREFIX + "/visits/{visit_id}/test-payment")
def test_payment(visit_id: int, payload: TestPayment, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    from app.services.visitor_testing import require_test_mode, simulate
    require_test_mode("payment")
    row = owned_visit(db, visit_id, account)
    with FileLock(lock_path(f"visitor-{row.id}"), timeout=30):
        db.refresh(row)
        if not row.order:
            raise HTTPException(409, "请先创建测试订单")
        simulate(row.order, payload.token, payload.outcome)
        audit(db, row.session.created_by_username, "visitor.test_payment." + payload.outcome,
            "tourist_session", str(row.session_id), row.order.order_no)
        db.commit()
    return visit_payload(row)


@router.post(PREFIX + "/visits/{visit_id}/claim")
def claim(visit_id: int, account=Depends(require_visitor), db: Session = Depends(database.get_db)):
    row = owned_visit(db, visit_id, account)
    if not row.order or row.order.status != "paid" or visit_payload(row)["expired"]:
        raise HTTPException(403, "当前订单不可领取")
    row.order.claimed_at = row.order.claimed_at or utc_now()
    db.commit()
    return visit_payload(row)


class ServiceUpdate(BaseModel):
    enabled: bool = False
    description: str = Field(min_length=1, max_length=1000)
    consent_text: str = Field(min_length=1, max_length=3000)
    consent_version: str = Field(min_length=1, max_length=32)
    price_cents: int = Field(gt=0, le=100000)


@router.get("/api/v1/scenic/projects/{project_id}/visitor-entry-qr")
def entry_qr(project_id: int, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    ensure_access(project_id, username, db, "write")
    from app.services.payments import render_qr_png_bytes
    base = get_settings().visitor_public_base_url.rstrip("/")
    if not visitor_origin_allowed(get_settings()):
        raise HTTPException(409, "请配置游客 HTTPS 地址，或显式启用局域网 HTTP 联调")
    return Response(render_qr_png_bytes(base + f"/visitor/#/sites/{project_id}", require_qrcode=True), media_type="image/png")


@router.get("/api/v1/scenic/projects/{project_id}/visitor-service")
def staff_service(project_id: int, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    ensure_access(project_id, username, db, "write")
    row = db.get(database.VisitorService, project_id)
    values = {name: getattr(row, name) for name in ServiceUpdate.model_fields} if row else {
        "enabled": False, "description": "留下游览中的精彩画面，制作属于你的旅行短片。",
        "consent_text": "我同意本景区在本次游览期间使用我的照片及拍摄画面进行人物匹配、视频制作与交付。",
        "consent_version": "visitor-v1", "price_cents": get_settings().scenic_order_price_cents}
    values["entry_url"] = get_settings().visitor_public_base_url.rstrip("/") + f"/visitor/#/sites/{project_id}"
    return values


@router.put("/api/v1/scenic/projects/{project_id}/visitor-service")
def configure_service(project_id: int, payload: ServiceUpdate, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    ensure_access(project_id, username, db, "write")
    row = db.get(database.VisitorService, project_id)
    if not row:
        row = database.VisitorService(project_id=project_id)
        db.add(row)
    for name, value in payload.model_dump().items():
        setattr(row, name, value)
    audit(db, username, "visitor.service_configured", "project", str(project_id))
    db.commit()
    return staff_service(project_id, username, db)


@router.get("/api/v1/scenic/projects/{project_id}/visitor-queue")
def staff_queue(project_id: int, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    ensure_access(project_id, username, db, "write")
    can_confirm_payment = True
    try:
        ensure_access(project_id, username, db, "manage")
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        can_confirm_payment = False
    rows = db.query(database.VisitorVisit).join(database.TouristSession).filter(
        database.TouristSession.project_id == project_id).outerjoin(
        database.ScenicOrder, database.VisitorVisit.order_id == database.ScenicOrder.id).order_by(
        case(((database.ScenicOrder.payment_provider == "visitor_manual") & (database.ScenicOrder.status == "pending"), 0), else_=1),
        database.VisitorVisit.id.desc()).limit(100).all()
    return {"can_confirm_payment": can_confirm_payment, "requests": [{**visit_payload(row), "session_id": row.session_id, "reason": row.reason,
        "draft_id": row.draft_id, "vlog_id": row.vlog_id, "error": row.internal_error} for row in rows]}


class ResumeRequest(BaseModel):
    action: str = Field(pattern="^(retry|publish|verify)$")
    vlog_id: int | None = Field(default=None, gt=0)


@router.post("/api/v1/scenic/visitor-requests/{visit_id}/resume")
def resume(visit_id: int, payload: ResumeRequest, username: str = Depends(get_scenic_current_username), db: Session = Depends(database.get_db)):
    row = db.get(database.VisitorVisit, visit_id)
    if not row:
        raise HTTPException(404, "制作请求不存在")
    ensure_access(row.session.project_id, username, db, "write")
    with FileLock(lock_path(f"visitor-{row.id}"), timeout=30):
        db.refresh(row)
        if payload.action == "verify":
            row.verified_at, row.verified_by = utc_now(), username
            if row.reason == "identity_unverified":
                row.stage, row.reason, row.requested_at = "sealing", "", utc_now()
            audit(db, username, "visitor.identity_verified", "tourist_session", str(row.session_id), f"visit={row.id}; account={row.account_id}")
            db.commit()
            return visit_payload(row)
        if get_settings().visitor_require_staff_verification and not row.verified_at:
            raise HTTPException(409, "请先核验游客本人、账号和照片")
        if row.order_id:
            raise HTTPException(409, "已有订单绑定当前交付版本，不可替换")
        if payload.action == "publish":
            vlog = db.get(database.ScenicVlogJob, payload.vlog_id) if payload.vlog_id else None
            if not vlog or vlog.session_id != row.session_id or not output_complete(vlog):
                raise HTTPException(409, "请先完成并复核本次游览的横竖版成片")
            row.vlog_id, row.stage = vlog.id, "ready"
        else:
            if row.stage != "review":
                raise HTTPException(409, "只有需要处理的请求可以重试")
            if row.reason == "recording_failed":
                from app.services.camera_stream import scenic_camera_stream_manager, solidify_project_camera_recordings
                solidify_project_camera_recordings(scenic_camera_stream_manager, db, row.session.project_id, username)
            row.stage, row.draft_id, row.vlog_id, row.requested_at = "waiting", None, None, utc_now()
        row.reason, row.internal_error = "", ""
        audit(db, username, "visitor." + payload.action, "tourist_session", str(row.session_id), f"visit={row.id}; vlog={row.vlog_id}")
        db.commit()
    return visit_payload(row)
