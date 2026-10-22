from datetime import datetime, timedelta, timezone
from typing import Optional
import warnings

import jwt
from jwt.exceptions import InvalidTokenError as JWTError

# Passlib probes the deprecated Unix crypt backend during import on Python 3.11.
# This application uses only bcrypt; retain its legacy hash compatibility.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="'crypt' is deprecated and slated for removal in Python 3.13",
        category=DeprecationWarning,
    )
    from passlib.context import CryptContext

from app.core.config import get_settings


pwd_context = CryptContext(
    # bcrypt_sha256 pre-hashes the UTF-8 password before bcrypt, so passwords
    # longer than bcrypt's 72-byte input limit cannot be silently truncated.
    # Keep bcrypt enabled for verification of hashes created by older releases.
    schemes=["bcrypt_sha256", "bcrypt"],
    deprecated="auto",
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not isinstance(plain_password, str) or not isinstance(hashed_password, str):
        return False
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (TypeError, ValueError):
        # Malformed hashes and legacy bcrypt inputs over 72 bytes are invalid
        # credentials, not server errors.
        return False


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_token(
    subject: str,
    token_type: str = "access",
    expires_minutes: Optional[int] = None,
    jti: Optional[str] = None,
    scope: Optional[str] = None,
) -> str:
    settings = get_settings()
    if expires_minutes is None:
        expires_minutes = (
            settings.refresh_token_expire_minutes
            if token_type == "refresh"
            else settings.access_token_expire_minutes
        )
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    payload = {"sub": subject, "type": token_type, "exp": expire}
    if jti:
        payload["jti"] = jti
    if scope:
        payload["scope"] = scope
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str, expected_type: Optional[str] = None) -> dict:
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    if expected_type and payload.get("type") != expected_type:
        raise JWTError("invalid token type")
    return payload
