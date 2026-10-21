from __future__ import annotations

import base64
import datetime
import io
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings


WECHAT_NATIVE_PROVIDER = "wechat_native"
WECHAT_API_BASE_URL = "https://api.mch.weixin.qq.com"
WECHAT_SUCCESS_STATE = "SUCCESS"
WECHAT_PENDING_STATES = {"NOTPAY", "USERPAYING", "ACCEPT"}
WECHAT_FAILED_STATES = {"CLOSED", "REVOKED", "PAYERROR", "REFUND"}
FAKE_WECHAT_ORDERS: dict[str, "WechatPaymentStatus"] = {}


class PaymentError(Exception):
    pass


class PaymentConfigurationError(PaymentError):
    pass


class PaymentProviderError(PaymentError):
    pass


class PaymentVerificationError(PaymentError):
    pass


@dataclass
class WechatNativeOrderRequest:
    order_no: str
    description: str
    amount_cents: int
    currency: str
    attach: str = ""
    time_expire: datetime.datetime | None = None


@dataclass
class WechatNativeOrderResponse:
    code_url: str
    expires_at: datetime.datetime
    trade_state: str = "NOTPAY"


@dataclass
class WechatPaymentStatus:
    out_trade_no: str
    trade_state: str
    transaction_id: str = ""
    amount_cents: int | None = None
    currency: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def paid(self) -> bool:
        return self.trade_state == WECHAT_SUCCESS_STATE


@dataclass
class WechatPaymentNotification:
    event_id: str
    out_trade_no: str
    trade_state: str
    transaction_id: str = ""
    amount_cents: int | None = None
    currency: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def paid(self) -> bool:
        return self.trade_state == WECHAT_SUCCESS_STATE


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _parse_wechat_datetime(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compact_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _wechat_amount(payload: dict[str, Any]) -> tuple[int | None, str]:
    amount = payload.get("amount") or {}
    if not isinstance(amount, dict):
        raise PaymentVerificationError("微信支付金额字段格式无效")
    total = amount.get("total")
    currency = str(amount.get("currency") or "").strip().upper()
    if total is None:
        return None, currency
    if isinstance(total, bool):
        raise PaymentVerificationError("微信支付金额字段格式无效")
    try:
        amount_cents = int(total)
    except (TypeError, ValueError) as exc:
        raise PaymentVerificationError("微信支付金额字段格式无效") from exc
    if amount_cents < 0:
        raise PaymentVerificationError("微信支付金额不能为负数")
    return amount_cents, currency


def render_qr_png_bytes(value: str, *, require_qrcode: bool = False) -> bytes:
    try:
        import qrcode  # type: ignore
    except Exception as exc:
        if require_qrcode:
            raise PaymentConfigurationError("缺少 qrcode[pil] 依赖，无法生成微信收款二维码") from exc
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (360, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 340, 340), outline="black", width=3)
        draw.text((42, 160), "QR dependency missing", fill="black")
        draw.text((42, 184), value[:38], fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class WechatNativePaymentClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._private_key = None
        self._platform_public_key = None

    def _validate_config(self, *, require_notify: bool = False) -> None:
        missing = []
        for attr, label in (
            ("payment_public_base_url", "PAYMENT_PUBLIC_BASE_URL"),
            ("wechat_pay_appid", "WECHAT_PAY_APPID"),
            ("wechat_pay_mchid", "WECHAT_PAY_MCHID"),
            ("wechat_pay_api_v3_key", "WECHAT_PAY_API_V3_KEY"),
            ("wechat_pay_merchant_serial_no", "WECHAT_PAY_MERCHANT_SERIAL_NO"),
            ("wechat_pay_merchant_private_key_path", "WECHAT_PAY_MERCHANT_PRIVATE_KEY_PATH"),
        ):
            if not getattr(self.settings, attr, ""):
                missing.append(label)
        if require_notify:
            for attr, label in (
                ("wechat_pay_platform_public_key_id", "WECHAT_PAY_PLATFORM_PUBLIC_KEY_ID"),
                ("wechat_pay_platform_public_key_path", "WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH"),
            ):
                if not getattr(self.settings, attr, ""):
                    missing.append(label)
        if missing:
            raise PaymentConfigurationError("微信支付未配置：" + ", ".join(missing))

    @property
    def notify_url(self) -> str:
        base_url = self.settings.payment_public_base_url.rstrip("/")
        return f"{base_url}/api/v1/scenic/payments/wechat/notify"

    def create_native_order(self, request: WechatNativeOrderRequest) -> WechatNativeOrderResponse:
        self._validate_config()
        expires_at = request.time_expire or (_utc_now() + datetime.timedelta(hours=2))
        body = {
            "appid": self.settings.wechat_pay_appid,
            "mchid": self.settings.wechat_pay_mchid,
            "description": request.description[:127],
            "out_trade_no": request.order_no,
            "time_expire": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "notify_url": self.notify_url,
            "amount": {"total": int(request.amount_cents), "currency": request.currency.upper()},
        }
        if request.attach:
            body["attach"] = request.attach[:128]
        payload = self._request("POST", "/v3/pay/transactions/native", body)
        code_url = str(payload.get("code_url") or "")
        if not code_url:
            raise PaymentProviderError("微信支付下单成功但未返回 code_url")
        return WechatNativeOrderResponse(code_url=code_url, expires_at=expires_at)

    def query_order(self, out_trade_no: str) -> WechatPaymentStatus:
        return self._query_order(out_trade_no)

    def create_jsapi_order(self, request: WechatNativeOrderRequest, openid: str) -> str:
        self._validate_config(require_notify=True)
        payload = self._request("POST", "/v3/pay/transactions/jsapi", {
            "appid": self.settings.wechat_pay_appid, "mchid": self.settings.wechat_pay_mchid,
            "description": request.description[:127], "out_trade_no": request.order_no,
            "notify_url": self.settings.payment_public_base_url.rstrip("/") + "/api/v1/scenic/visitor/payments/wechat/notify", "payer": {"openid": openid},
            "amount": {"total": request.amount_cents, "currency": request.currency},
        })
        prepay_id = payload.get("prepay_id")
        if not isinstance(prepay_id, str) or not prepay_id:
            raise PaymentProviderError("微信未返回预支付凭据")
        return prepay_id

    def jsapi_parameters(self, prepay_id: str) -> dict:
        appid = self.settings.wechat_pay_appid
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        package = f"prepay_id={prepay_id}"
        return {"appId": appid, "timeStamp": timestamp, "nonceStr": nonce,
            "package": package, "signType": "RSA",
            "paySign": self._sign(f"{appid}\n{timestamp}\n{nonce}\n{package}\n".encode())}

    def _query_order(self, out_trade_no: str) -> WechatPaymentStatus:
        self._validate_config()
        path = f"/v3/pay/transactions/out-trade-no/{quote(out_trade_no, safe='')}?mchid={quote(self.settings.wechat_pay_mchid, safe='')}"
        payload = self._request("GET", path, None)
        amount_cents, currency = _wechat_amount(payload)
        return WechatPaymentStatus(
            out_trade_no=str(payload.get("out_trade_no") or out_trade_no),
            trade_state=str(payload.get("trade_state") or ""),
            transaction_id=str(payload.get("transaction_id") or ""),
            amount_cents=amount_cents,
            currency=currency,
            raw=payload,
        )

    def parse_notification(self, headers: dict[str, str], body: bytes) -> WechatPaymentNotification:
        self._validate_config(require_notify=True)
        self._verify_notification_signature(headers, body)
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaymentVerificationError("微信支付通知不是有效的 JSON") from exc
        if not isinstance(event, dict):
            raise PaymentVerificationError("微信支付通知格式无效")
        event_id = str(event.get("id") or "")
        resource = event.get("resource") or {}
        plaintext = self._decrypt_resource(resource)
        amount_cents, currency = _wechat_amount(plaintext)
        return WechatPaymentNotification(
            event_id=event_id,
            out_trade_no=str(plaintext.get("out_trade_no") or ""),
            trade_state=str(plaintext.get("trade_state") or ""),
            transaction_id=str(plaintext.get("transaction_id") or ""),
            amount_cents=amount_cents,
            currency=currency,
            raw=plaintext,
        )

    def _request(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        body_text = _compact_json(body) if body is not None else ""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": self._authorization(method, path, body_text),
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.request(method, f"{WECHAT_API_BASE_URL}{path}", content=body_text.encode("utf-8"), headers=headers)
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"微信支付请求失败：{exc}") from exc
        if response.status_code >= 400:
            raise PaymentProviderError(f"微信支付接口错误 {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}

    def _authorization(self, method: str, path: str, body_text: str) -> str:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_text}\n"
        signature = self._sign(message.encode("utf-8"))
        return (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.settings.wechat_pay_mchid}",'
            f'nonce_str="{nonce}",'
            f'signature="{signature}",'
            f'timestamp="{timestamp}",'
            f'serial_no="{self.settings.wechat_pay_merchant_serial_no}"'
        )

    def _sign(self, message: bytes) -> str:
        private_key = self._merchant_private_key()
        signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode("ascii")

    def _merchant_private_key(self):
        if self._private_key is None:
            path = self.settings.wechat_pay_merchant_private_key_path
            try:
                with open(path, "rb") as handle:
                    self._private_key = serialization.load_pem_private_key(handle.read(), password=None)
            except Exception as exc:
                raise PaymentConfigurationError(f"无法读取微信支付商户私钥：{path}") from exc
        return self._private_key

    def _platform_public_key(self):
        if self._platform_public_key is None:
            path = self.settings.wechat_pay_platform_public_key_path
            try:
                with open(path, "rb") as handle:
                    self._platform_public_key = serialization.load_pem_public_key(handle.read())
            except Exception as exc:
                raise PaymentConfigurationError(f"无法读取微信支付平台公钥：{path}") from exc
        return self._platform_public_key

    def _verify_notification_signature(self, headers: dict[str, str], body: bytes) -> None:
        normalized = {key.lower(): value for key, value in headers.items()}
        serial = normalized.get("wechatpay-serial", "")
        if not serial:
            raise PaymentVerificationError("微信支付通知缺少平台公钥序列号")
        if serial != self.settings.wechat_pay_platform_public_key_id:
            raise PaymentVerificationError("微信支付通知平台公钥序列号不匹配")
        timestamp = normalized.get("wechatpay-timestamp")
        nonce = normalized.get("wechatpay-nonce")
        signature = normalized.get("wechatpay-signature")
        if not timestamp or not nonce or not signature:
            raise PaymentVerificationError("微信支付通知缺少签名头")
        try:
            timestamp_value = int(timestamp)
            body_text = body.decode("utf-8")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise PaymentVerificationError("微信支付通知签名头或请求体格式无效") from exc
        if abs(int(time.time()) - timestamp_value) > 300:
            raise PaymentVerificationError("微信支付通知时间戳已过期")
        message = f"{timestamp}\n{nonce}\n{body_text}\n".encode("utf-8")
        try:
            self._platform_public_key().verify(base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, ValueError) as exc:
            raise PaymentVerificationError("微信支付通知签名校验失败") from exc

    def _decrypt_resource(self, resource: dict[str, Any]) -> dict[str, Any]:
        try:
            nonce = str(resource["nonce"]).encode("utf-8")
            associated_data = str(resource.get("associated_data") or "").encode("utf-8")
            ciphertext = base64.b64decode(str(resource["ciphertext"]))
            plaintext = AESGCM(self.settings.wechat_pay_api_v3_key.encode("utf-8")).decrypt(nonce, ciphertext, associated_data)
            return json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise PaymentVerificationError("微信支付通知资源解密失败") from exc


class FakeWechatNativePaymentClient:
    def __init__(self) -> None:
        self.orders = FAKE_WECHAT_ORDERS

    def create_native_order(self, request: WechatNativeOrderRequest) -> WechatNativeOrderResponse:
        expires_at = request.time_expire or (_utc_now() + datetime.timedelta(hours=2))
        self.orders[request.order_no] = WechatPaymentStatus(
            out_trade_no=request.order_no,
            trade_state="NOTPAY",
            amount_cents=request.amount_cents,
            currency=request.currency.upper(),
        )
        return WechatNativeOrderResponse(
            code_url=f"weixin://wxpay/bizpayurl?pr={request.order_no}",
            expires_at=expires_at,
        )

    def query_order(self, out_trade_no: str) -> WechatPaymentStatus:
        return self.orders.get(out_trade_no) or WechatPaymentStatus(out_trade_no=out_trade_no, trade_state="NOTPAY")

    def parse_notification(self, headers: dict[str, str], body: bytes) -> WechatPaymentNotification:
        normalized = {key.lower(): value for key, value in headers.items()}
        if normalized.get("wechatpay-signature") != "VALID":
            raise PaymentVerificationError("fake 微信支付通知签名校验失败")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaymentVerificationError("fake 微信支付通知不是有效的 JSON") from exc
        if not isinstance(payload, dict):
            raise PaymentVerificationError("fake 微信支付通知格式无效")
        if "resource" in payload and isinstance(payload["resource"], dict) and "plaintext" in payload["resource"]:
            payload = payload["resource"]["plaintext"]
        amount_cents, currency = _wechat_amount(payload)
        return WechatPaymentNotification(
            event_id=str(payload.get("event_id") or payload.get("id") or ""),
            out_trade_no=str(payload.get("out_trade_no") or ""),
            trade_state=str(payload.get("trade_state") or ""),
            transaction_id=str(payload.get("transaction_id") or ""),
            amount_cents=amount_cents,
            currency=currency,
            raw=payload,
        )


def get_wechat_native_payment_client(settings: Settings | None = None):
    settings = settings or get_settings()
    provider = (settings.payment_provider or "").lower()
    environment = settings.app_env.lower()
    production_like = environment in {"production", "prod", "staging", "stage"}
    local_like = environment in {"development", "dev", "local", "test"}
    fake_provider = provider in {"fake_wechat", "wechat_fake"}

    if production_like and fake_provider:
        raise PaymentConfigurationError("生产或预发布环境禁止使用 fake 微信支付客户端")
    if environment == "test" or (local_like and (not provider or fake_provider)):
        return FakeWechatNativePaymentClient()
    if provider not in {WECHAT_NATIVE_PROVIDER, "wechat"}:
        raise PaymentConfigurationError("真实微信支付未启用，请设置 PAYMENT_PROVIDER=wechat_native")
    return WechatNativePaymentClient(settings)
