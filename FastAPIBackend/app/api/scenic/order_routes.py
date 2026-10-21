"""Scenic order, payment, and customer delivery endpoints."""

from filelock import FileLock
from app.workers.scenic_editing import lock_path
import datetime
import html
import io
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response, RedirectResponse

import scenic_database as database
from app.api.auth_dependencies import get_scenic_current_username as get_current_username
from app.api.scenic.service_adapters import audit as _audit, ensure_access as _ensure_project_access
from app.api.scenic.system_routes import (
    _apply_wechat_payment_status,
    _create_order_record,
    _ensure_simulated_payment_allowed,
    _ensure_tourist_access,
    _initiate_wechat_payment,
    _latest_vlog,
    _mark_order_paid,
    _mark_order_payment_failed,
    _order_payload,
    _payment_client,
    _payment_error_response,
    _require_scenic_admin,
)
from app.core.config import get_settings
from app.schemas import ScenicOrderCreate, ScenicOrderPaymentResult
from app.services.asset_delivery import deliver_object
from app.services.media_service import delete_storage_objects as _delete_storage_objects
from app.services.payments import PaymentError, WECHAT_NATIVE_PROVIDER
from app.services.time_utils import as_utc as _as_utc, utc_now as _utc_now


router = APIRouter()
PAYMENT_NOTIFICATION_MAX_BYTES = 64 * 1024


def v1_create_scenic_order(
    session_id: int,
    payload: ScenicOrderCreate,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    if payload.payment_method == "simulated":
        raise HTTPException(status_code=400, detail="请使用专用的模拟支付接口")
    session = _ensure_tourist_access(session_id, username, db, "write")
    vlog = _latest_vlog(session, db, payload.vlog_job_id)
    order = _create_order_record(session, vlog, payload)
    try:
        db.add(order)
        db.flush()
        if order.payment_provider == WECHAT_NATIVE_PROVIDER:
            _initiate_wechat_payment(order)
        _audit(db, username, "scenic.order_created", "tourist_session", str(session.id), order.order_no)
        db.commit()
        db.refresh(order)
        return _order_payload(order)
    except PaymentError as exc:
        db.rollback()
        _delete_storage_objects([order.payment_qr_object_key])
        raise _payment_error_response(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        _delete_storage_objects([order.payment_qr_object_key])
        raise HTTPException(status_code=409, detail="订单号冲突，请重试") from exc
    except Exception:
        db.rollback()
        _delete_storage_objects([order.payment_qr_object_key])
        raise


def v1_simulate_scenic_payment(
    session_id: int,
    payload: ScenicOrderCreate,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_simulated_payment_allowed()
    session = _ensure_tourist_access(session_id, username, db, "write")
    vlog = _latest_vlog(session, db, payload.vlog_job_id)
    order = _create_order_record(session, vlog, payload)
    order.payment_method = "simulated"
    order.payment_provider = "simulated"
    now = _utc_now()
    db.add(order)
    db.flush()
    _mark_order_paid(order, paid_at=now)
    _audit(db, username, "scenic.order_simulated_paid", "tourist_session", str(session.id), order.order_no)
    db.commit()
    db.refresh(order)
    return _order_payload(order)


def v1_refresh_scenic_order_payment(
    order_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    order = db.query(database.ScenicOrder).filter(database.ScenicOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    _ensure_project_access(order.session.project_id, username, db, "write")
    if order.payment_provider != WECHAT_NATIVE_PROVIDER:
        raise HTTPException(status_code=400, detail="该订单不是微信支付订单")
    try:
        status = _payment_client().query_order(order.order_no)
        became_paid = _apply_wechat_payment_status(order, status)
    except PaymentError as exc:
        order.payment_error = str(exc)
        order.last_payment_sync_at = _utc_now()
        db.commit()
        raise _payment_error_response(exc) from exc
    if became_paid:
        _audit(db, username, "scenic.order_paid.wechat_query", "tourist_session", str(order.session_id), order.order_no)
    db.commit()
    db.refresh(order)
    return _order_payload(order)


def v1_simulate_scenic_order_payment_result(
    order_id: int,
    payload: ScenicOrderPaymentResult,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    _ensure_simulated_payment_allowed()
    order = db.query(database.ScenicOrder).filter(database.ScenicOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    _ensure_project_access(order.session.project_id, username, db, "write")
    if order.payment_provider == "visitor_manual":
        raise HTTPException(409, "人工确认订单不能使用模拟支付，请由管理员确认")
    order.payment_provider = order.payment_provider or "fake_wechat"
    order.payment_method = order.payment_method or "wechat_native"
    order.last_payment_sync_at = _utc_now()
    if payload.result == "success":
        order.provider_trade_state = "SUCCESS"
        order.provider_trade_no = order.provider_trade_no or f"SIM-{order.order_no}"
        order.payment_error = ""
        _mark_order_paid(order)
        _audit(db, username, "scenic.order_paid.local_simulation", "tourist_session", str(order.session_id), order.order_no)
    else:
        if order.status == "paid":
            raise HTTPException(status_code=400, detail="已支付订单不能标记为失败")
        _mark_order_payment_failed(order)
        _audit(db, username, "scenic.order_payment_failed.local_simulation", "tourist_session", str(order.session_id), order.order_no)
    db.commit()
    db.refresh(order)
    return _order_payload(order)


def v1_mark_scenic_order_paid(
    order_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(database.get_db),
):
    order = db.query(database.ScenicOrder).filter(database.ScenicOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    visit = db.query(database.VisitorVisit).filter_by(order_id=order.id).first()
    if visit:
        # Project owners manage their own visitor payments; ordinary operators
        # and visitors cannot approve an order or cross project boundaries.
        _ensure_project_access(order.session.project_id, username, db, "manage")
    else:
        _require_scenic_admin(username, db)
        _ensure_project_access(order.session.project_id, username, db, "write")
    with FileLock(lock_path(f"visitor-{visit.id}" if visit else f"order-{order.id}"), timeout=30):
        db.refresh(order)
        if order.status == "paid":
            return _order_payload(order)
        if visit:
            if order.payment_provider != "visitor_manual" or order.provider_trade_state != "WAITING_STAFF" or order.status != "pending":
                raise HTTPException(409, "仅可确认游客已申请的人工付款订单")
            from app.services.visitor_production import output_complete
            if visit.stage != "ready" or visit.vlog_id != order.vlog_job_id or not output_complete(visit.vlog):
                raise HTTPException(409, "交付视频尚未就绪")
            order.provider_trade_state = "STAFF_CONFIRMED"
            order.last_payment_sync_at = _utc_now()
        _mark_order_paid(order)
        _audit(db, username, "scenic.order_paid.manual_admin", "tourist_session", str(order.session_id), order.order_no)
        db.commit()
        db.refresh(order)
        return _order_payload(order)


def _process_wechat_payment_notification(headers: dict[str, str], body: bytes, payment_client=None) -> dict:
    client = payment_client or _payment_client()
    try:
        notification = client.parse_notification(headers, body)
    except PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db = database.SessionLocal()
    try:
        order = (
            db.query(database.ScenicOrder)
            .filter(database.ScenicOrder.order_no == notification.out_trade_no)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        if order.payment_provider == "visitor_test":
            raise HTTPException(400, "测试订单仅接受游客测试收银台通知")
        if order.payment_provider == "wechat_jsapi":
            from app.services.payments import WechatNativePaymentClient
            if not isinstance(client, WechatNativePaymentClient):
                raise HTTPException(400, "游客订单仅接受真实微信支付通知")
        if notification.event_id and order.notify_event_id == notification.event_id:
            return {"code": "SUCCESS", "message": "成功"}
        try:
            became_paid = _apply_wechat_payment_status(order, notification)
        except PaymentError as exc:
            order.payment_error = str(exc)
            order.last_payment_sync_at = _utc_now()
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if became_paid:
            _audit(
                db,
                order.session.created_by_username,
                "scenic.order_paid.wechat_notify",
                "tourist_session",
                str(order.session_id),
                order.order_no,
            )
        db.commit()
        return {"code": "SUCCESS", "message": "成功"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def v1_wechat_payment_notify(request: Request):
    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > PAYMENT_NOTIFICATION_MAX_BYTES:
            raise HTTPException(status_code=413, detail="支付通知请求体过大")
        chunks.append(chunk)
    return await run_in_threadpool(
        _process_wechat_payment_notification,
        dict(request.headers),
        b"".join(chunks),
    )


def _delivery_order(delivery_token: str, db: Session) -> database.ScenicOrder:
    order = db.query(database.ScenicOrder).filter(database.ScenicOrder.delivery_token == delivery_token).first()
    if not order:
        raise HTTPException(status_code=404, detail="交付链接不存在")
    visit = db.query(database.VisitorVisit).filter_by(order_id=order.id).first()
    if visit and get_settings().visitor_require_staff_verification and not visit.verified_at:
        raise HTTPException(403, "请先到服务台完成身份核验")
    expires_at = _as_utc(order.paid_at or order.created_at) + datetime.timedelta(
        hours=get_settings().scenic_delivery_token_ttl_hours
    )
    if _utc_now() >= expires_at:
        raise HTTPException(status_code=410, detail="交付链接已过期")
    return order


def v1_scenic_delivery(delivery_token: str, db: Session = Depends(database.get_db)):
    order = _delivery_order(delivery_token, db)
    return {
        "status": "success",
        "order": _order_payload(order),
        "paid": order.status == "paid",
        "message": "已支付，可下载高清版" if order.status == "paid" else "待支付，仅开放试看版",
    }


def v1_scenic_delivery_preview(delivery_token: str, db: Session = Depends(database.get_db)):
    order = _delivery_order(delivery_token, db)
    vlog = order.vlog_job
    if not vlog or vlog.status != "succeeded" or not vlog.preview_object_key:
        raise HTTPException(status_code=404, detail="试看资源不存在")
    return deliver_object(
        vlog.preview_object_key,
        filename=vlog.preview_filename,
        content_type="video/mp4",
    )


def v1_scenic_delivery_payment_qr(delivery_token: str, db: Session = Depends(database.get_db)):
    order = _delivery_order(delivery_token, db)
    if not order.payment_qr_object_key:
        raise HTTPException(status_code=404, detail="支付二维码不存在")
    return deliver_object(
        order.payment_qr_object_key,
        filename=f"{order.order_no}-payment.png",
        content_type="image/png",
    )


def v1_scenic_delivery_full(
    delivery_token: str,
    download: bool = Query(False),
    db: Session = Depends(database.get_db),
):
    order = _delivery_order(delivery_token, db)
    if order.status != "paid":
        raise HTTPException(status_code=402, detail="订单尚未支付")
    vlog = order.vlog_job
    if not vlog or vlog.status != "succeeded" or not vlog.full_object_key:
        raise HTTPException(status_code=404, detail="高清资源不存在")
    return deliver_object(
        vlog.full_object_key,
        filename=vlog.full_filename,
        content_type="video/mp4",
        download=download,
    )


@router.get("/api/v1/scenic/deliveries/{delivery_token}/variants/{variant_id}/{asset}")
def scenic_delivery_variant(delivery_token: str, variant_id: int, asset: str, download: bool = Query(False), db: Session = Depends(database.get_db)):
    order = _delivery_order(delivery_token, db)
    if asset not in {"full", "preview", "cover"}:
        raise HTTPException(404, "交付资源不存在")
    if asset == "full" and order.status != "paid":
        raise HTTPException(402, "订单尚未支付")
    variant = db.query(database.ScenicVlogVariant).filter_by(id=variant_id, vlog_job_id=order.vlog_job_id, status="succeeded").first()
    if not variant or not order.vlog_job or order.vlog_job.status != "succeeded":
        raise HTTPException(404, "交付资源不存在")
    key = getattr(variant, asset + "_object_key")
    return deliver_object(key, filename=f"游览纪念_{variant.orientation}.{'jpg' if asset == 'cover' else 'mp4'}",
        content_type="image/jpeg" if asset == "cover" else "video/mp4", download=download)


@router.get("/api/v1/scenic/deliveries/{delivery_token}/qr")
def scenic_delivery_qr(delivery_token: str, request: Request, db: Session = Depends(database.get_db)):
    _delivery_order(delivery_token, db)
    import qrcode
    origin = get_settings().scenic_public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    buffer = io.BytesIO()
    qrcode.make(f"{origin}/api/v1/scenic/deliveries/{delivery_token}/page").save(buffer, format="PNG")
    return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/scenic/deliveries/{delivery_token}/confirm-receipt", response_class=RedirectResponse)
def scenic_confirm_receipt(delivery_token: str, db: Session = Depends(database.get_db)):
    order = _delivery_order(delivery_token, db)
    if order.status != "paid":
        raise HTTPException(402, "订单尚未支付")
    if not order.vlog_job or order.vlog_job.status != "succeeded" or not order.vlog_job.full_object_key:
        raise HTTPException(409, "完整成片尚不可领取")
    confirm_order_receipt(order, db)
    return RedirectResponse(f"/api/v1/scenic/deliveries/{delivery_token}/page", status_code=303,
                            headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})


def confirm_order_receipt(order, db):
    """Record receipt once for either existing delivery entry point."""
    changed = (db.query(database.ScenicOrder)
        .filter(database.ScenicOrder.id == order.id, database.ScenicOrder.claimed_at.is_(None))
        .update({database.ScenicOrder.claimed_at: _utc_now()}, synchronize_session=False))
    if changed:
        _audit(db, order.session.created_by_username, "scenic.order_receipt_confirmed",
               "tourist_session", str(order.session_id),
               json.dumps({"order_id": order.id, "vlog_job_id": order.vlog_job_id,
                           "actor": "delivery_link_holder"}, ensure_ascii=False))
    db.commit()


@router.get("/api/v1/scenic/deliveries/{delivery_token}/page", response_class=HTMLResponse)
def scenic_delivery_page(delivery_token: str, db: Session = Depends(database.get_db)):
    order = _delivery_order(delivery_token, db)
    return render_delivery_page(order)


def render_delivery_page(order, route_prefix="/api/v1/scenic/deliveries"):
    """Reuse the delivery UI with the public visitor proxy's own asset paths."""
    delivery_token = order.delivery_token
    payload = _order_payload(order)
    paid = order.status == "paid"
    title = html.escape(order.session.visitor_label or "我的游览回忆")
    variants = payload["variants"] or [{"orientation": "landscape", "preview_url": payload["preview_url"], "full_url": payload["full_url"], "cover_url": ""}]
    cards = []
    for variant in variants:
        label = "横版游览纪念片" if variant["orientation"] == "landscape" else "竖版分享短片"
        source = variant.get("full_url") if paid else variant.get("preview_url")
        if not source:
            continue
        link = html.escape(source.replace("/api/v1/scenic/deliveries", route_prefix), quote=True)
        poster = html.escape(variant.get("cover_url", "").replace("/api/v1/scenic/deliveries", route_prefix), quote=True)
        action = f'<a class="button" href="{link}?download=true">保存{label}</a>' if paid else '<p class="note">当前为短试看，支付后可领取完整视频。</p>'
        cards.append(f'<article><h2>{label}</h2><video controls playsinline preload="metadata" poster="{poster}" src="{link}"></video>{action}</article>')
    message = "已支付 · 可以保存完整视频" if paid else f'待支付 · {order.amount_cents / 100:.2f} {html.escape(order.currency)}'
    payment = f'<img class="qr" alt="付款二维码" src="{html.escape(payload["payment_qr_url"], quote=True)}"><p>请使用微信扫码付款，完成后刷新页面。</p>' if not paid and payload["payment_qr_url"] else ""
    receipt = ""
    save_label = "两版视频" if len(cards) > 1 else "视频"
    if paid:
        if order.claimed_at:
            receipt = '<p class="receipt">已确认领取 · 感谢留下这段游览回忆。有效期内仍可保存视频。</p>'
        else:
            receipt = (f'<form method="post" action="{route_prefix}/{delivery_token}/confirm-receipt">'
                       f'<p>保存{save_label}后，请确认领取，方便景区工作人员核对。</p>'
                       '<button class="button" type="submit">我已保存，确认领取</button></form>')
    guide = ('<article><h2>下载与领取</h2><p>点击下方保存按钮下载横竖双版视频。'
             '如果微信未开始下载，请在此领取页右上角选择“在浏览器打开”，再点击保存。'
             '领取页凭专用链接校验已付款作品，无需再次微信登录。</p>'
             '<p class="note">请妥善保管领取链接，勿转发给他人。领取有效期内可以重新下载。</p></article>') if paid else ''
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · 游览回忆</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f3;color:#213e36;font:16px/1.7 system-ui,sans-serif}}main{{max-width:880px;margin:auto;padding:32px 20px}}header{{padding:20px 0}}small{{color:#5e7c70;letter-spacing:.12em}}h1{{font-size:32px;margin:8px 0}}h2{{font-size:20px}}article{{background:white;padding:20px;border-radius:20px;margin:20px 0}}video{{display:block;width:100%;max-height:70vh;background:#10261f;border-radius:12px}}.button{{display:inline-block;background:#245c4d;color:white;padding:10px 20px;border-radius:10px;text-decoration:none;margin-top:16px;border:0;font:inherit;cursor:pointer}}.note{{color:#697970}}.qr{{width:180px;height:180px}}footer{{color:#718178;font-size:13px;padding:20px 0}}</style>
<main><header><small>把今天的美好带回家</small><h1>{title}</h1><p>{message}</p></header>{guide}{''.join(cards)}{payment}{receipt}<a href="./page">刷新领取状态</a><footer>请妥善保存领取链接。链接到期后请联系景区工作人员。</footer></main></html>'''
    # Object delivery can redirect to HTTPS S3 or a configured local MinIO origin.
    origins = {"'self'", "https:"}
    for value in (get_settings().s3_endpoint_url, get_settings().s3_public_base_url):
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not any(c in parsed.netloc for c in "'\" ;"):
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    media_sources = " ".join(sorted(origins))
    return HTMLResponse(page, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "Content-Security-Policy": f"default-src 'none'; style-src 'unsafe-inline'; img-src {media_sources} data:; media-src {media_sources}; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"})

router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/orders",
    v1_create_scenic_order,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/tourists/{session_id}/orders/simulate-payment",
    v1_simulate_scenic_payment,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/orders/{order_id}/refresh-payment",
    v1_refresh_scenic_order_payment,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/orders/{order_id}/simulate-payment-result",
    v1_simulate_scenic_order_payment_result,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/orders/{order_id}/mark-paid",
    v1_mark_scenic_order_paid,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/payments/wechat/notify",
    v1_wechat_payment_notify,
    methods=["POST"],
)
router.add_api_route(
    "/api/v1/scenic/deliveries/{delivery_token}",
    v1_scenic_delivery,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/deliveries/{delivery_token}/preview",
    v1_scenic_delivery_preview,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/deliveries/{delivery_token}/payment-qr",
    v1_scenic_delivery_payment_qr,
    methods=["GET"],
)
router.add_api_route(
    "/api/v1/scenic/deliveries/{delivery_token}/full",
    v1_scenic_delivery_full,
    methods=["GET"],
)
