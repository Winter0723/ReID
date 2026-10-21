import hashlib
import hmac
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.services.storage import InvalidObjectKeyError, storage


def signed_asset_url(path: str) -> str:
    """Create a short-lived, path-bound URL for browser media elements."""

    normalized_path = path if path.startswith("/") else f"/{path}"
    settings = get_settings()
    expires = int(time.time()) + settings.asset_url_ttl_seconds
    payload = f"{normalized_path}\n{expires}".encode("utf-8")
    signature = hmac.new(settings.secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    separator = "&" if "?" in normalized_path else "?"
    return f"{normalized_path}{separator}asset_expires={expires}&asset_signature={signature}"


def has_valid_asset_signature(request: Request) -> bool:
    expires_text = request.query_params.get("asset_expires", "")
    provided = request.query_params.get("asset_signature", "")
    if not expires_text.isdigit() or not provided:
        return False
    expires = int(expires_text)
    settings = get_settings()
    now = int(time.time())
    if expires < now or expires > now + settings.asset_url_ttl_seconds + 30:
        return False
    payload = f"{request.url.path}\n{expires}".encode("utf-8")
    expected = hmac.new(settings.secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def deliver_object(
    object_key: str,
    *,
    filename: str = "",
    content_type: str = "",
    download: bool = False,
):
    """Serve a private local or cached S3 file through the authorized API."""

    if not object_key:
        raise HTTPException(status_code=404, detail="资源不存在")
    headers = {"Cache-Control": "private, no-store"}
    try:
        # Resolve S3 through the server's cache: its endpoint may only be reachable
        # inside Docker. FileResponse also preserves byte-range video playback.
        path = Path(storage.path_for(object_key))
        if not path.is_file():
            raise HTTPException(status_code=404, detail="资源不存在")
        return FileResponse(
            path,
            media_type=content_type or None,
            filename=(filename or path.name) if download else None,
            headers=headers,
        )
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code", "")) in {"404", "NoSuchKey", "NotFound"}:
            raise HTTPException(status_code=404, detail="资源不存在") from exc
        raise
    except (InvalidObjectKeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="资源不存在") from exc
