"""Development-only visitor adapters; persistent payment state uses the real order."""
import secrets
from datetime import timedelta
from fastapi import HTTPException
from app.core.config import get_settings
from app.services.time_utils import utc_now, as_utc
from app.services.payments import WechatPaymentNotification

PROVIDER = "visitor_test"


def require_test_mode(kind):
    settings = get_settings()
    if (settings.app_env.strip().lower() not in {"development", "dev", "local", "test"}
        or getattr(settings, "visitor_" + kind + "_provider") != "test"
        or len(settings.visitor_test_invite_code) < 16):
        raise HTTPException(404, "联调入口未开放")
    return settings


def apply_test_result(order, state):
    # Reuse amount verification, paid timestamp and monotonic/idempotent transitions.
    from app.api.scenic.system_routes import _apply_wechat_payment_status
    return _apply_wechat_payment_status(order, WechatPaymentNotification(
        event_id="test-" + order.order_no + "-" + state,
        out_trade_no=order.order_no, trade_state=state,
        transaction_id="test-" + order.order_no if state == "SUCCESS" else "",
        amount_cents=order.amount_cents, currency=order.currency))


def sync_test_payment(order):
    require_test_mode("payment")
    if order.payment_provider != PROVIDER:
        raise HTTPException(409, "订单支付模式不匹配")
    if (order.status != "paid" and order.provider_trade_state == "USERPAYING"
        and order.payment_expires_at and as_utc(order.payment_expires_at) <= utc_now()):
        apply_test_result(order, "SUCCESS")


def checkout(order):
    sync_test_payment(order)
    if order.status == "paid":
        return None
    if not order.payment_code_url:
        order.payment_code_url = secrets.token_urlsafe(32)
    return {"provider": "test", "token": order.payment_code_url,
        "amount_cents": order.amount_cents, "currency": order.currency}


def simulate(order, token, outcome):
    sync_test_payment(order)
    if not order.payment_code_url or not secrets.compare_digest(token.encode(), order.payment_code_url.encode()):
        raise HTTPException(403, "测试收银台凭据无效，请重新打开收银台")
    if order.status == "paid" and outcome != "duplicate":
        return
    if order.provider_trade_state == "USERPAYING" and outcome != "duplicate":
        raise HTTPException(409, "模拟付款已受理，请等待 15 秒后刷新到账结果")
    if outcome == "duplicate":
        if order.status != "paid":
            raise HTTPException(409, "请先模拟成功到账，再发送重复通知")
        apply_test_result(order, "SUCCESS")
        apply_test_result(order, "SUCCESS")
    elif outcome == "delayed":
        apply_test_result(order, "USERPAYING")
        order.payment_expires_at = utc_now() + timedelta(seconds=15)
    else:
        apply_test_result(order, {"success": "SUCCESS", "cancel": "NOTPAY", "failure": "PAYERROR"}[outcome])
