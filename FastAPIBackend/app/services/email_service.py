import datetime
import hashlib
import hmac
import secrets
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import HTTPException

from app.core.config import get_settings
from app.services.time_utils import as_utc, utc_now


EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_MIN_INTERVAL_SECONDS = 60
EMAIL_CODE_PURPOSES = {"login", "register"}


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def email_code_hash(email: str, code: str, purpose: str) -> str:
    settings = get_settings()
    payload = f"{normalize_email(email)}:{purpose}:{code}:{settings.secret_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_email_code_request_allowed(
    db,
    user_model,
    email: str,
    purpose: str,
    username: str | None = None,
) -> None:
    if purpose not in EMAIL_CODE_PURPOSES:
        raise HTTPException(status_code=400, detail="验证码用途无效")
    if purpose == "register":
        if db.query(user_model).filter(user_model.email == email).first():
            raise HTTPException(status_code=400, detail="邮箱已被注册")
        normalized_username = (username or "").strip()
        if normalized_username and db.query(user_model).filter(user_model.username == normalized_username).first():
            raise HTTPException(status_code=400, detail="用户名已被注册")
        return
    user = db.query(user_model).filter(user_model.email == normalize_email(email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="该邮箱尚未注册，请先注册账号")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="用户已禁用")


def send_email_code(email: str, code: str, purpose: str) -> dict:
    settings = get_settings()
    action_text = "注册" if purpose == "register" else "登录"
    subject = f"MyReID {action_text}验证码"
    body = f"你的 MyReID {action_text}验证码是：{code}\n\n验证码 {settings.email_code_ttl_minutes} 分钟内有效。"

    if settings.email_code_dev_mode:
        print(f"[MyReID] email {purpose} code for {email}: {code}")
        return {"message": "验证码已生成，可在后端控制台查看", "debug_code": code}
    if not settings.smtp_host:
        raise HTTPException(status_code=500, detail="未配置 SMTP_HOST，无法发送邮箱验证码")
    sender = settings.smtp_from or settings.smtp_username
    if not sender:
        raise HTTPException(status_code=500, detail="未配置 SMTP_FROM 或 SMTP_USERNAME，无法发送邮箱验证码")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = email
    message.set_content(body)
    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ssl.create_default_context()) as smtp:
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"验证码邮件发送失败: {exc}") from exc
    return {"message": "验证码已发送，请查收邮箱"}


def create_email_code(db, code_model, email: str, purpose: str):
    email = normalize_email(email)
    now = utc_now()
    recent = (
        db.query(code_model)
        .filter(
            code_model.email == email,
            code_model.purpose == purpose,
            code_model.created_at >= now - datetime.timedelta(seconds=EMAIL_CODE_MIN_INTERVAL_SECONDS),
        )
        .order_by(code_model.created_at.desc())
        .first()
    )
    if recent:
        raise HTTPException(status_code=429, detail="验证码发送过于频繁，请稍后再试")
    db.query(code_model).filter(
        code_model.email == email,
        code_model.purpose == purpose,
        code_model.consumed_at.is_(None),
    ).update({"consumed_at": now}, synchronize_session=False)
    code = f"{secrets.randbelow(1_000_000):06d}"
    record = code_model(
        email=email,
        purpose=purpose,
        code_hash=email_code_hash(email, code, purpose),
        expires_at=now + datetime.timedelta(minutes=get_settings().email_code_ttl_minutes),
    )
    db.add(record)
    db.flush()
    return code, record


def delete_email_code_record(db, code_model, record_id: int, *, domain: str = "core") -> None:
    try:
        db.query(code_model).filter(code_model.id == record_id).delete()
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"{domain} 验证码邮件发送失败后清理验证码记录失败 id={record_id}: {exc}")


def verify_email_code(db, code_model, email: str, code: str, purpose: str):
    email = normalize_email(email)
    now = utc_now()
    record = (
        db.query(code_model)
        .filter(
            code_model.email == email,
            code_model.purpose == purpose,
            code_model.consumed_at.is_(None),
        )
        .order_by(code_model.created_at.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=400, detail="请先获取邮箱验证码")
    if as_utc(record.expires_at) <= now:
        record.consumed_at = now
        db.commit()
        raise HTTPException(status_code=400, detail="验证码已过期，请重新获取")
    if record.attempts >= EMAIL_CODE_MAX_ATTEMPTS:
        record.consumed_at = now
        db.commit()
        raise HTTPException(status_code=400, detail="验证码尝试次数过多，请重新获取")
    valid = hmac.compare_digest(record.code_hash, email_code_hash(email, code.strip(), purpose))
    updates = {"attempts": code_model.attempts + 1}
    if valid:
        updates["consumed_at"] = now
    claimed = db.query(code_model).filter(
        code_model.id == record.id, code_model.consumed_at.is_(None),
        code_model.expires_at > now, code_model.attempts < EMAIL_CODE_MAX_ATTEMPTS,
    ).update(updates, synchronize_session=False)
    if not claimed:
        db.rollback()
        raise HTTPException(status_code=400, detail="验证码已失效，请重新获取")
    if not valid:
        db.commit()
        raise HTTPException(status_code=401, detail="验证码错误")
    db.refresh(record)
    return record
