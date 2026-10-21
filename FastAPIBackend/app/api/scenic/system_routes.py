from app.services.camera_policy import public_camera_source
import datetime
import io
import json
import os
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.orm import Session

from app.api.scenic.service_adapters import ensure_access as _ensure_project_access
from app.services.media_service import (
    BASE_GALLERY_DIR,
    load_verified_rgb_image as _load_verified_rgb_image,
    media_video_seek_seconds as _media_video_seek_seconds,
    merge_central_camera_status as _merge_central_camera_status,
    source_info_video_seek_seconds as _source_info_video_seek_seconds,
    timestamp_to_seconds_for_payload as _timestamp_to_seconds_for_payload,
)
from app.services.time_utils import as_utc as _as_utc, utc_now as _utc_now
from app.core.config import PRODUCTION_LIKE_ENVIRONMENTS, get_settings
from app.schemas import ScenicOrderCreate
from app.services.camera_stream import (
    list_camera_devices,
    scenic_camera_stream_manager,
)
from app.services.central_camera_service import find_camera_service
from app.services.model_runtime import clip_image_feature, detect_people
from app.services.payments import (
    PaymentConfigurationError,
    PaymentError,
    PaymentProviderError,
    PaymentVerificationError,
    WECHAT_FAILED_STATES,
    WECHAT_NATIVE_PROVIDER,
    WECHAT_PENDING_STATES,
    WechatNativePaymentClient,
    WechatNativeOrderRequest,
    WechatPaymentNotification,
    WechatPaymentStatus,
    get_wechat_native_payment_client,
    render_qr_png_bytes,
)
from app.services.asset_delivery import signed_asset_url
from app.services.storage import storage
import scenic_database as database

camera_stream_manager = scenic_camera_stream_manager
SCENIC_STORAGE_NAMESPACE = "scenic"
SCENIC_CAMERA_STREAM_KEY_BASE = 1_000_000
PROJECT_ROLES = {"owner", "operator", "viewer"}
PROJECT_PERMISSION_LEVELS = {"viewer": 1, "operator": 2, "owner": 3}
PROJECT_PERMISSION_MINIMUMS = {"read": 1, "write": 2, "manage": 3}


def _ensure_simulated_payment_allowed() -> None:
    if get_settings().app_env.strip().lower() in PRODUCTION_LIKE_ENVIRONMENTS:
        raise HTTPException(status_code=403, detail="生产或预发布环境已禁用模拟支付")


def _camera_device_is_iphone(device: dict) -> bool:
    text = " ".join(
        str(device.get(key, ""))
        for key in ("type", "label", "name", "description")
    ).lower()
    return "iphone" in text or "continuity" in text


def _camera_device_is_virtual(device: dict) -> bool:
    text = " ".join(
        str(device.get(key, ""))
        for key in ("type", "label", "name", "description")
    ).lower()
    return "obs" in text or "virtual" in text


def _camera_device_source(device: dict, fallback: str = "0") -> str:
    source = str(device.get("source") if device.get("source") is not None else fallback).strip()
    return source or fallback


def _camera_device_label(device: dict) -> str:
    label = str(device.get("label") or device.get("name") or "").strip()
    if label:
        return label
    if _camera_device_is_iphone(device):
        return "iPhone 实时相机"
    return "摄像头"


def _ordered_camera_devices(include_fallback: bool = False, iphone_only: bool = False) -> list[dict]:
    devices = [device for device in list_camera_devices()
               if device.get("enabled", True) and device.get("online", True)
               and (device.get("kind") in ("dshow", "v4l2", "rtsp") or not _camera_device_is_virtual(device))]
    iphone_devices = [device for device in devices if _camera_device_is_iphone(device)]
    if iphone_only:
        devices = iphone_devices
    devices.sort(key=lambda item: (0 if _camera_device_is_iphone(item) else 1, str(item.get("source", ""))))
    if not devices and include_fallback and not iphone_only:
        devices = [{"label": "iPhone/本机实时相机", "source": "0", "type": "manual"}]
    return devices


def _iphone_camera_sources(devices: list[dict]) -> set[str]:
    return {_camera_device_source(device) for device in devices if _camera_device_is_iphone(device)}


def _camera_point_can_follow_iphone(point: database.ScenicCameraPoint) -> bool:
    source = (point.camera_source or "").strip()
    camera_id = (point.camera_id or "").strip()
    return source in {"", "0"} and camera_id in {"", "0"}


def _camera_point_matches_sources(point: database.ScenicCameraPoint, sources: set[str]) -> bool:
    return bool(sources & {point.camera_source or "", point.camera_id or ""})


def _scenic_storage_project_id(project_id: int) -> str:
    return f"{SCENIC_STORAGE_NAMESPACE}/{project_id}"


def _project_payload(project: database.Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "project_type": project.project_type or "scenic",
        "camera_source": public_camera_source(project.camera_source or ""),
        "owner_username": project.owner_username,
        "created_at": project.created_at,
    }


def _segment_seconds(media, timestamp, source_info: str = None, fallback_seconds: float = 0.0) -> float:
    seconds = _source_info_video_seek_seconds(source_info)
    if seconds is not None:
        return float(seconds)
    seconds = _media_video_seek_seconds(media, timestamp)
    if seconds is not None:
        return float(seconds)
    seconds = _timestamp_to_seconds_for_payload(timestamp)
    if seconds is not None:
        return float(seconds)
    return fallback_seconds


def _camera_point_name(db: Session, project_id: int, camera_id: str) -> str:
    if not camera_id:
        return "未知点位"
    point = (
        db.query(database.ScenicCameraPoint)
        .filter(
            database.ScenicCameraPoint.project_id == project_id,
            database.ScenicCameraPoint.is_active.is_(True),
            (
                (database.ScenicCameraPoint.camera_id == camera_id)
                | (database.ScenicCameraPoint.camera_source == camera_id)
            ),
        )
        .first()
    )
    return point.name if point else f"摄像头 {camera_id}"


def _camera_point_payload(point: database.ScenicCameraPoint) -> dict:
    return {
        "id": point.id,
        "project_id": point.project_id,
        "name": point.name,
        "camera_source": public_camera_source(point.camera_source),
        "camera_id": public_camera_source(point.camera_id),
        "location": point.location,
        "role": point.role,
        "is_active": point.is_active,
        "created_at": point.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": point.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _scenic_camera_stream_key(project_id: int, point_id: int) -> int:
    return project_id * SCENIC_CAMERA_STREAM_KEY_BASE + point_id


def _stop_scenic_stream_keys(project_id: int, stream_keys: list[int | None]) -> None:
    """Signal every stream first, then join, so multi-camera shutdown is bounded."""

    unique_keys = list(dict.fromkeys(stream_keys))
    for stream_key in unique_keys:
        camera_stream_manager.stop(project_id, wait=False, stream_key=stream_key)
    for stream_key in unique_keys:
        camera_stream_manager.stop(project_id, wait=True, stream_key=stream_key)


def _stop_all_scenic_project_streams(project_id: int, db: Session) -> int:
    point_ids = [
        row[0]
        for row in db.query(database.ScenicCameraPoint.id)
        .filter(database.ScenicCameraPoint.project_id == project_id)
        .all()
    ]
    stream_keys: list[int | None] = [None]
    stream_keys.extend(_scenic_camera_stream_key(project_id, point_id) for point_id in point_ids)
    _stop_scenic_stream_keys(project_id, stream_keys)
    return len(stream_keys)


def _gallery_url_path(gallery_url: str) -> str:
    if not gallery_url or not gallery_url.startswith("/gallery/"):
        return ""
    return os.path.join(BASE_GALLERY_DIR, *gallery_url[len("/gallery/"):].split("/"))


def _camera_wall_point_payload(point: database.ScenicCameraPoint, db: Session, *, light: bool = False) -> dict:
    stream_key = _scenic_camera_stream_key(point.project_id, point.id)
    status = camera_stream_manager.status(point.project_id, stream_key=stream_key)
    latest_frame_url = f"/gallery/scenic_project_{point.project_id}/camera/{stream_key}/latest.jpg"
    latest_frame_path = _gallery_url_path(latest_frame_url)
    if not status.get("latest_frame_url") and os.path.exists(latest_frame_path):
        status["latest_frame_url"] = latest_frame_url
    if not status.get("latest_frame_version") and os.path.exists(latest_frame_path):
        status["latest_frame_version"] = int(os.path.getmtime(latest_frame_path))
    if not light:
        camera_ids = {value for value in [point.camera_id, point.camera_source] if value}
        target_query = db.query(database.DetectionTarget).filter(database.DetectionTarget.project_id == point.project_id)
        if camera_ids:
            target_query = target_query.filter(database.DetectionTarget.camera_id.in_(camera_ids))
        target_count = target_query.count()
        status["indexed_targets"] = max(int(status.get("indexed_targets") or 0), target_count)
    configured_source = point.camera_source or point.camera_id or "0"
    status["camera_source"] = (status.get("camera_source") or configured_source) if status.get("running") else configured_source
    status = _merge_central_camera_status(status, camera_source=status["camera_source"])
    monitor = status.get("monitor_status") or {}
    if status.get("monitor_running") and monitor.get("latest_frame_url"):
        # Display the capture thread's latest frame independently of slow
        # detection/embedding/storage work. Ingestion status stays separate.
        status["latest_frame_url"] = monitor.get("latest_frame_url") or ""
        status["latest_frame_version"] = int(monitor.get("latest_frame_version") or 0)
    elif not status.get("running"):
        status["latest_frame_url"] = ""
        status["latest_frame_version"] = 0
    if status.get("recording_url") and status.get("media_id"):
        status["recording_url"] = signed_asset_url(
            f"/api/v1/scenic/projects/{point.project_id}/media/{status['media_id']}/content"
        )
    payload = _camera_point_payload(point)
    payload.update({
        "point_id": point.id,
        "stream_key": stream_key,
        "status": status,
        "running": bool(status.get("running")),
        "monitor_running": bool(status.get("monitor_running")),
        "capture_fps": float(monitor.get("capture_fps") or 0),
        "preview_fps": float(monitor.get("preview_fps") or 0),
        "preview_width": int(monitor.get("width") or 0),
        "preview_height": int(monitor.get("height") or 0),
        "latest_frame_url": status.get("latest_frame_url") or "",
        "latest_frame_version": int(status.get("latest_frame_version") or 0),
        "indexed_targets": int(status.get("indexed_targets") or 0),
        "message": status.get("message") or "",
        "recording_url": status.get("recording_url") or "",
        "recording_filename": status.get("recording_filename") or "",
    })
    return payload


def _camera_wall_payload(project_id: int, db: Session, *, light: bool = False) -> list[dict]:
    points = (
        db.query(database.ScenicCameraPoint)
        .filter(database.ScenicCameraPoint.project_id == project_id, database.ScenicCameraPoint.is_active.is_(True))
        .order_by(database.ScenicCameraPoint.role.asc(), database.ScenicCameraPoint.created_at.asc())
        .all()
    )
    from app.services.camera_bridge import scenic_points_for_environment
    return [_camera_wall_point_payload(point, db, light=light) for point in scenic_points_for_environment(points)]


def _segment_payload(segment: database.TouristVisitSegment) -> dict:
    original_media_object_key = segment.original_media_object_key or (segment.media_file.object_key if segment.media_file else "")
    return {
        "id": segment.id,
        "session_id": segment.session_id,
        "target_id": segment.target_id,
        "media_id": segment.media_id,
        "camera_id": public_camera_source(segment.camera_id),
        "camera_name": segment.camera_name,
        "timestamp": segment.timestamp,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "duration_seconds": max(0.0, float(segment.end_seconds or 0) - float(segment.start_seconds or 0)),
        "similarity": segment.similarity,
        "thumbnail_url": signed_asset_url(
            f"/api/v1/scenic/segments/{segment.id}/thumbnail"
        ) if segment.thumbnail_object_key else "",
        "original_media_url": signed_asset_url(
            f"/api/v1/scenic/segments/{segment.id}/original"
        ) if original_media_object_key else "",
        "included": segment.included,
        "sort_order": segment.sort_order,
        "created_at": segment.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _variant_payload(variant, delivery_token=None, paid=False):
    base = (f"/api/v1/scenic/deliveries/{delivery_token}/variants/{variant.id}" if delivery_token
            else f"/api/v1/scenic/vlogs/{variant.vlog_job_id}/variants/{variant.id}")
    def url(asset, key):
        if not key:
            return ""
        return f"{base}/{asset}" if delivery_token else signed_asset_url(f"{base}/{asset}")
    result = {"id": variant.id, "orientation": variant.orientation, "status": variant.status,
        "progress": variant.progress, "message": variant.message, "error": variant.error,
        "duration_seconds": variant.duration_seconds,
        "preview_url": url("preview", variant.preview_object_key),
        "full_url": url("full", variant.full_object_key) if not delivery_token or paid else "",
        "cover_url": url("cover", variant.cover_object_key)}
    if not delivery_token:
        result["draft_preview_url"] = url("draft-preview", variant.draft_preview_object_key)
    return result


def _vlog_payload(job: database.ScenicVlogJob) -> dict:
    variants = list(job.variants)
    snapshot = json.loads(job.snapshot_json) if job.snapshot_json else {}
    return {
        "id": job.id,
        "session_id": job.session_id,
        "processing_job_id": job.processing_job_id,
        "status": job.status,
        "draft_id": job.draft_id,
        "draft_revision": job.draft_revision,
        "template_id": snapshot.get("template_id", ""),
        "title": snapshot.get("title", ""),
        "variants": [_variant_payload(variant) for variant in variants],
        "progress": sum(v.progress for v in variants) / len(variants) if variants else (job.processing_job.progress if job.processing_job else 0),
        "message": " · ".join(v.message for v in variants) if variants else (job.processing_job.message if job.processing_job else ""),
        "preview_url": signed_asset_url(
            f"/api/v1/scenic/vlogs/{job.id}/preview"
        ) if job.preview_object_key else "",
        "full_url": signed_asset_url(
            f"/api/v1/scenic/vlogs/{job.id}/full"
        ) if job.full_object_key else "",
        "preview_filename": job.preview_filename,
        "full_filename": job.full_filename,
        "segment_count": job.segment_count,
        "duration_seconds": job.duration_seconds,
        "error": job.error,
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": job.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _order_payload(order: database.ScenicOrder) -> dict:
    vlog = order.vlog_job
    payment_qr_url = (
        f"/api/v1/scenic/deliveries/{order.delivery_token}/payment-qr"
        if order.payment_qr_object_key
        else ""
    )
    payment_qr_text = order.payment_code_url or (f"SIM-PAY-{order.order_no}" if order.payment_method == "simulated" else "")
    return {
        "id": order.id,
        "session_id": order.session_id,
        "vlog_job_id": order.vlog_job_id,
        "order_no": order.order_no,
        "amount_cents": order.amount_cents,
        "currency": order.currency,
        "status": order.status,
        "payment_method": order.payment_method,
        "payment_provider": order.payment_provider,
        "provider_trade_no": order.provider_trade_no,
        "provider_trade_state": order.provider_trade_state,
        "payment_code_url": order.payment_code_url,
        "payment_qr_url": payment_qr_url,
        "payment_error": order.payment_error,
        "payment_expires_at": order.payment_expires_at.strftime("%Y-%m-%d %H:%M:%S") if order.payment_expires_at else "",
        "last_payment_sync_at": order.last_payment_sync_at.strftime("%Y-%m-%d %H:%M:%S") if order.last_payment_sync_at else "",
        "notify_event_id": order.notify_event_id,
        "delivery_token": order.delivery_token,
        "delivery_url": f"/api/v1/scenic/deliveries/{order.delivery_token}",
        "delivery_page_url": f"/api/v1/scenic/deliveries/{order.delivery_token}/page",
        "delivery_qr_url": f"/api/v1/scenic/deliveries/{order.delivery_token}/qr",
        "variants": [_variant_payload(v, order.delivery_token, order.status == "paid") for v in vlog.variants] if vlog else [],
        "payment_code": payment_qr_text,
        "payment_qr_text": payment_qr_text,
        "preview_url": f"/api/v1/scenic/deliveries/{order.delivery_token}/preview" if vlog and vlog.preview_object_key else "",
        "full_url": f"/api/v1/scenic/deliveries/{order.delivery_token}/full" if order.status == "paid" and vlog and vlog.full_object_key else "",
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "paid_at": order.paid_at.strftime("%Y-%m-%d %H:%M:%S") if order.paid_at else "",
        "delivered_at": order.delivered_at.strftime("%Y-%m-%d %H:%M:%S") if order.delivered_at else "",
        "claimed_at": order.claimed_at.strftime("%Y-%m-%d %H:%M:%S") if order.claimed_at else "",
    }


def _tourist_payload(session: database.TouristSession, db: Session, include_children: bool = False) -> dict:
    payload = {
        "id": session.id,
        "public_id": session.public_id,
        "project_id": session.project_id,
        "ticket_code": session.ticket_code,
        "visitor_label": session.visitor_label,
        "contact_hint": session.contact_hint,
        "consent_granted": session.consent_granted,
        "consent_version": session.consent_version,
        "consent_granted_at": session.consent_granted_at,
        "entry_target_box": json.loads(session.entry_target_box_json) if session.entry_target_box_json else None,
        "entry_image_url": signed_asset_url(
            f"/api/v1/scenic/tourists/{session.id}/entry-image"
        ),
        "entry_image_filename": session.entry_image_filename,
        "status": session.status,
        "segment_count": session.segment_count,
        "best_similarity": session.best_similarity,
        "created_by_username": session.created_by_username,
        "checked_out_at": session.checked_out_at.strftime("%Y-%m-%d %H:%M:%S") if session.checked_out_at else "",
        "created_at": session.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": session.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if include_children:
        payload["segments"] = [
            _segment_payload(segment)
            for segment in db.query(database.TouristVisitSegment)
            .filter(database.TouristVisitSegment.session_id == session.id)
            .order_by(database.TouristVisitSegment.sort_order.asc(), database.TouristVisitSegment.id.asc())
            .all()
        ]
        payload["vlog_jobs"] = [
            _vlog_payload(job)
            for job in db.query(database.ScenicVlogJob)
            .filter(database.ScenicVlogJob.session_id == session.id)
            .order_by(database.ScenicVlogJob.created_at.desc())
            .all()
        ]
        payload["orders"] = [
            _order_payload(order)
            for order in db.query(database.ScenicOrder)
            .filter(database.ScenicOrder.session_id == session.id)
            .order_by(database.ScenicOrder.created_at.desc())
            .all()
        ]
    return payload


def _ensure_tourist_access(session_id: int, username: str, db: Session, permission: str = "read") -> database.TouristSession:
    session = db.query(database.TouristSession).filter(database.TouristSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="游客记录不存在")
    _ensure_project_access(session.project_id, username, db, permission)
    return session


def _latest_vlog(session: database.TouristSession, db: Session, vlog_job_id: int | None = None) -> database.ScenicVlogJob:
    vlog = (
        db.query(database.ScenicVlogJob)
        .filter(database.ScenicVlogJob.session_id == session.id, database.ScenicVlogJob.status == "succeeded")
        .filter(database.ScenicVlogJob.id == vlog_job_id if vlog_job_id is not None else True)
        .order_by(database.ScenicVlogJob.created_at.desc())
        .first()
    )
    if not vlog or not vlog.preview_object_key or not vlog.full_object_key:
        raise HTTPException(status_code=400, detail="请先生成可交付的游览视频")
    if vlog.variants and (len(vlog.variants) != 2 or any(v.status != "succeeded" for v in vlog.variants)):
        raise HTTPException(status_code=400, detail="横版和竖版都完成后才能创建订单")
    return vlog


def _create_order_record(session: database.TouristSession, vlog: database.ScenicVlogJob, payload: ScenicOrderCreate) -> database.ScenicOrder:
    settings = get_settings()
    payment_method = (payload.payment_method or "manual").strip().lower() or "manual"
    payment_provider = WECHAT_NATIVE_PROVIDER if payment_method in {WECHAT_NATIVE_PROVIDER, "wechat"} else payment_method
    return database.ScenicOrder(
        session_id=session.id,
        vlog_job_id=vlog.id,
        order_no=f"SV{uuid.uuid4().hex.upper()}",
        amount_cents=settings.scenic_order_price_cents,
        currency=settings.scenic_order_currency.upper(),
        payment_method=payment_method,
        payment_provider=payment_provider if payment_provider in {WECHAT_NATIVE_PROVIDER, "manual", "simulated"} else "",
        provider_trade_state="NOTPAY" if payment_provider == WECHAT_NATIVE_PROVIDER else "",
        delivery_token=uuid.uuid4().hex,
    )


def _payment_client():
    return get_wechat_native_payment_client(get_settings())


def _payment_error_response(exc: PaymentError) -> HTTPException:
    status_code = 503 if isinstance(exc, (PaymentConfigurationError, PaymentProviderError)) else 400
    return HTTPException(status_code=status_code, detail=str(exc))


def _store_order_qr(order: database.ScenicOrder, client) -> None:
    require_qrcode = isinstance(client, WechatNativePaymentClient)
    qr_bytes = render_qr_png_bytes(order.payment_code_url, require_qrcode=require_qrcode)
    stored = storage.save_upload(
        _scenic_storage_project_id(order.session.project_id),
        f"wechat_order_{order.order_no}.png",
        io.BytesIO(qr_bytes),
        category=f"tourists/{order.session_id}/payments",
    )
    order.payment_qr_object_key = stored.object_key


def _initiate_wechat_payment(order: database.ScenicOrder) -> None:
    client = _payment_client()
    response = client.create_native_order(WechatNativeOrderRequest(
        order_no=order.order_no,
        description=f"MyReID 景区视频 {order.order_no}",
        amount_cents=order.amount_cents,
        currency=order.currency,
        attach=json.dumps({"session_id": order.session_id, "vlog_job_id": order.vlog_job_id}, ensure_ascii=False),
    ))
    order.payment_provider = WECHAT_NATIVE_PROVIDER
    order.payment_method = WECHAT_NATIVE_PROVIDER
    order.provider_trade_state = response.trade_state
    order.payment_code_url = response.code_url
    order.payment_expires_at = response.expires_at.replace(tzinfo=None) if response.expires_at.tzinfo else response.expires_at
    order.payment_error = ""
    _store_order_qr(order, client)


def _mark_order_paid(order: database.ScenicOrder, *, paid_at: datetime.datetime | None = None) -> None:
    now = paid_at or _utc_now()
    order.status = "paid"
    order.paid_at = order.paid_at or now
    order.delivered_at = order.delivered_at or now
    order.session.status = "paid"


def _mark_order_payment_failed(order: database.ScenicOrder, message: str = "支付失败，请重试并重新付款") -> None:
    order.status = "payment_failed"
    order.provider_trade_state = "PAYERROR"
    order.payment_error = message
    order.last_payment_sync_at = _utc_now()


def _apply_wechat_payment_status(order: database.ScenicOrder, status: WechatPaymentStatus | WechatPaymentNotification) -> bool:
    if status.out_trade_no != order.order_no:
        raise PaymentVerificationError("微信支付订单号不匹配")
    if status.amount_cents is not None and int(status.amount_cents) != int(order.amount_cents):
        raise PaymentVerificationError("微信支付金额不匹配")
    if status.currency and status.currency.upper() != order.currency.upper():
        raise PaymentVerificationError("微信支付币种不匹配")

    if status.paid and status.amount_cents is None:
        raise PaymentVerificationError("微信支付成功结果缺少金额")
    if status.paid and not status.currency:
        raise PaymentVerificationError("微信支付成功结果缺少币种")

    was_paid = order.status == "paid"
    if was_paid:
        # Payment is monotonic.  Delayed CLOSED/REFUND/PAYERROR notifications
        # must not revoke an already delivered order; refunds need a separate
        # explicit business workflow rather than being treated as payment failure.
        if status.paid and order.provider_trade_no and status.transaction_id:
            if order.provider_trade_no != status.transaction_id:
                raise PaymentVerificationError("微信支付交易号与已支付订单不匹配")
        order.last_payment_sync_at = _utc_now()
        if isinstance(status, WechatPaymentNotification) and status.event_id:
            order.notify_event_id = status.event_id
        if status.paid:
            order.provider_trade_state = status.trade_state
            order.provider_trade_no = status.transaction_id or order.provider_trade_no
            order.payment_error = ""
        return False

    order.provider_trade_state = status.trade_state
    order.provider_trade_no = status.transaction_id or order.provider_trade_no
    order.last_payment_sync_at = _utc_now()
    order.payment_error = ""
    if isinstance(status, WechatPaymentNotification) and status.event_id:
        order.notify_event_id = status.event_id

    if status.paid:
        _mark_order_paid(order)
    elif status.trade_state in WECHAT_FAILED_STATES:
        _mark_order_payment_failed(order, status.trade_state)
    elif order.status != "paid" and status.trade_state in WECHAT_PENDING_STATES:
        order.status = "pending"
    return order.status == "paid" and not was_paid


def _require_scenic_admin(username: str, db: Session) -> database.User:
    user = db.query(database.User).filter(database.User.username == username, database.User.is_active.is_(True)).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="需要 admin 权限")
    return user


def _unique_camera_point_name(db: Session, project_id: int, base_name: str) -> str:
    normalized = (base_name or "摄像头").strip() or "摄像头"
    existing = {
        row[0]
        for row in db.query(database.ScenicCameraPoint.name)
        .filter(database.ScenicCameraPoint.project_id == project_id)
        .all()
    }
    if normalized not in existing:
        return normalized
    for index in range(2, 1000):
        candidate = f"{normalized}-{index}"
        if candidate not in existing:
            return candidate
    return f"{normalized}-{uuid.uuid4().hex[:8]}"


def _scenic_camera_status_payload(project_id: int, project, db: Session) -> dict:
    payload = camera_stream_manager.status(project_id)
    target_count = (
        db.query(database.DetectionTarget)
        .filter(
            database.DetectionTarget.project_id == project_id,
            database.DetectionTarget.camera_id.isnot(None),
        )
        .count()
    )
    latest_frame_url = f"/gallery/scenic_project_{project_id}/camera/latest.jpg"
    latest_frame_path = os.path.join(BASE_GALLERY_DIR, f"scenic_project_{project_id}", "camera", "latest.jpg")
    if not os.path.exists(latest_frame_path):
        monitor_service = find_camera_service(project.camera_source or "0")
        monitor_url = monitor_service.status().get("latest_frame_url") if monitor_service else ""
        if monitor_url.startswith("/gallery/"):
            monitor_path = os.path.join(BASE_GALLERY_DIR, *monitor_url[len("/gallery/"):].split("/"))
            if os.path.exists(monitor_path):
                latest_frame_path = monitor_path
    if not payload.get("latest_frame_url") and os.path.exists(latest_frame_path):
        payload["latest_frame_url"] = latest_frame_url
    if not payload.get("latest_frame_version") and os.path.exists(latest_frame_path):
        payload["latest_frame_version"] = int(os.path.getmtime(latest_frame_path))
    latest_recording = (
        db.query(database.MediaFile)
        .filter(
            database.MediaFile.project_id == project_id,
            database.MediaFile.media_type == "camera",
            database.MediaFile.size_bytes > 0,
            database.MediaFile.content_type.like("video/%"),
        )
        .order_by(database.MediaFile.created_at.desc())
        .first()
    )
    if latest_recording and not payload.get("recording_url"):
        payload["recording_url"] = signed_asset_url(
            f"/api/v1/scenic/projects/{project_id}/media/{latest_recording.id}/content"
        )
        payload["recording_filename"] = latest_recording.filename
        payload["recording_media_id"] = latest_recording.id
    elif payload.get("recording_media_id"):
        payload["recording_url"] = signed_asset_url(
            f"/api/v1/scenic/projects/{project_id}/media/{payload['recording_media_id']}/content"
        )
    payload["indexed_targets"] = max(int(payload.get("indexed_targets") or 0), target_count)
    payload["project_type"] = project.project_type
    payload["camera_source"] = payload.get("camera_source") or project.camera_source or "0"
    payload = _merge_central_camera_status(payload, camera_source=payload["camera_source"])
    if payload.get("recording_url") and payload.get("media_id"):
        payload["recording_url"] = signed_asset_url(
            f"/api/v1/scenic/projects/{project_id}/media/{payload['media_id']}/content"
        )
    return payload


def _tourist_entry_feature(session: database.TouristSession):
    image_path = storage.path_for(session.entry_image_object_key)
    image = _load_verified_rgb_image(image_path)
    if session.entry_target_box_json:
        x1, y1, x2, y2 = json.loads(session.entry_target_box_json)
        width, height = image.size
        image = image.crop((int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)))
        return clip_image_feature(image)
    # Reuse the verified RGB image: OpenCV path reads fail on Unicode paths
    # on some Windows builds and would silently skip person detection.
    frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    if frame is not None:
        detections = detect_people(frame, min_confidence=0.25)
        if detections:
            detection = max(detections, key=lambda item: (item.x2 - item.x1) * (item.y2 - item.y1))
            crop = detection.crop_from(frame)
            if crop is not None and crop.shape[0] >= 60 and crop.shape[1] >= 30:
                image = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)).convert("RGB")
    return clip_image_feature(image)


def _staff_vlog(vlog_id: int, username: str | None, db: Session) -> database.ScenicVlogJob:
    vlog = db.query(database.ScenicVlogJob).filter(database.ScenicVlogJob.id == vlog_id).first()
    if not vlog:
        raise HTTPException(status_code=404, detail="成片任务不存在")
    if username is not None:
        _ensure_project_access(vlog.session.project_id, username, db)
    return vlog


def _staff_segment(segment_id: int, username: str | None, db: Session) -> database.TouristVisitSegment:
    segment = (
        db.query(database.TouristVisitSegment)
        .filter(database.TouristVisitSegment.id == segment_id)
        .first()
    )
    if not segment:
        raise HTTPException(status_code=404, detail="游览片段不存在")
    if username is not None:
        _ensure_project_access(segment.session.project_id, username, db)
    return segment
