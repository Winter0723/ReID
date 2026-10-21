import hashlib
import mimetypes
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Optional
from uuid import uuid4
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings


def read_upload_limited(file_obj: BinaryIO, max_bytes: int) -> bytes:
    """Read a small upload into memory while enforcing the limit during streaming."""

    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = file_obj.read(min(1024 * 1024, max_bytes + 1 - size))
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise UploadTooLargeError(f"文件超过最大限制 {max_bytes // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


def copy_upload_limited(file_obj: BinaryIO, target: Path, max_bytes: int) -> int:
    """Stream an upload to an atomic local file without exceeding the configured limit."""

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(delete=False, dir=target.parent)
    temp_path = Path(temp_file.name)
    size = 0
    try:
        with temp_file:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError(
                        f"文件超过最大限制 {max_bytes // (1024 * 1024)} MB"
                    )
                temp_file.write(chunk)
        temp_path.replace(target)
        return size
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@dataclass
class StoredObject:
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str


class StorageError(RuntimeError):
    """Base error for object-storage operations."""


class UploadTooLargeError(StorageError):
    pass


class InvalidObjectKeyError(StorageError):
    pass


class ObjectStorage:
    def save_upload(self, project_id: int, filename: str, file_obj: BinaryIO, category: str = "originals") -> StoredObject:
        raise NotImplementedError

    def save_path(self, project_id: int, source_path: Path, filename: str, category: str) -> StoredObject:
        with source_path.open("rb") as file_obj:
            return self.save_upload(project_id, filename, file_obj, category=category)

    def path_for(self, object_key: str) -> Path:
        raise NotImplementedError

    def url_for(self, object_key: str) -> str:
        raise NotImplementedError

    def signed_url_for(self, object_key: str) -> str:
        return self.url_for(object_key)

    def delete(self, object_key: str) -> None:
        raise NotImplementedError

    @staticmethod
    def _safe_filename(filename: str) -> str:
        # Treat both slash styles as separators regardless of the host OS.
        # This keeps Windows-originated names safe when the API runs on Linux.
        normalized = (filename or "upload.bin").replace("\\", "/")
        safe_name = PurePosixPath(normalized).name
        return safe_name if safe_name not in {"", ".", ".."} else "upload.bin"

    @staticmethod
    def _spool(file_obj: BinaryIO, staging_dir: Path) -> tuple[Path, int, str]:
        digest = hashlib.sha256()
        size = 0
        max_upload_mb = get_settings().max_upload_mb
        max_bytes = max_upload_mb * 1024 * 1024
        staging_dir.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(delete=False, dir=staging_dir)
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                while True:
                    chunk = file_obj.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLargeError(
                            f"文件超过最大限制 {max_upload_mb} MB"
                        )
                    digest.update(chunk)
                    tmp.write(chunk)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return tmp_path, size, digest.hexdigest()

    @staticmethod
    def _content_type(filename: str) -> str:
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"

    @staticmethod
    def _object_key(project_id: int, category: str, filename: str, sha256: str) -> str:
        safe_name = ObjectStorage._safe_filename(filename)
        return f"projects/{project_id}/{category}/{sha256[:16]}_{uuid4()}_{safe_name}"

    @staticmethod
    def _normalize_object_key(object_key: str) -> str:
        normalized = (object_key or "").replace("\\", "/").strip("/")
        parts = normalized.split("/")
        if not normalized or any(part in {"", ".", ".."} for part in parts):
            raise InvalidObjectKeyError("无效的对象存储路径")
        return "/".join(parts)

    @classmethod
    def _path_under(cls, root: Path, object_key: str) -> Path:
        root = root.resolve()
        target = (root / cls._normalize_object_key(object_key)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise InvalidObjectKeyError("对象存储路径越界") from exc
        return target


class LocalObjectStorage(ObjectStorage):
    def __init__(self) -> None:
        self.root = get_settings().object_storage_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.root / ".staging"

    def save_upload(self, project_id: int, filename: str, file_obj: BinaryIO, category: str = "originals") -> StoredObject:
        safe_name = self._safe_filename(filename)
        tmp_path, size, sha256 = self._spool(file_obj, self.staging_dir)
        try:
            object_key = self._normalize_object_key(
                self._object_key(project_id, category, safe_name, sha256)
            )
            target = self._path_under(self.root, object_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.replace(target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return StoredObject(
            object_key=object_key,
            filename=safe_name,
            content_type=self._content_type(safe_name),
            size_bytes=size,
            sha256=sha256,
        )

    def path_for(self, object_key: str) -> Path:
        return self._path_under(self.root, object_key)

    def url_for(self, object_key: str) -> str:
        from app.services.asset_delivery import signed_asset_url
        return signed_asset_url(f"/objects/{self._normalize_object_key(object_key)}")

    def delete(self, object_key: str) -> None:
        self._path_under(self.root, object_key).unlink(missing_ok=True)


class S3ObjectStorage(ObjectStorage):
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.cache_dir = settings.object_storage_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = settings.object_storage_dir / ".staging"
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )
        self._ensure_bucket()

    def save_upload(self, project_id: int, filename: str, file_obj: BinaryIO, category: str = "originals") -> StoredObject:
        safe_name = self._safe_filename(filename)
        tmp_path, size, sha256 = self._spool(file_obj, self.staging_dir)
        try:
            object_key = self._normalize_object_key(
                self._object_key(project_id, category, safe_name, sha256)
            )
            content_type = self._content_type(safe_name)
            with tmp_path.open("rb") as body:
                self.client.upload_fileobj(
                    body,
                    self.bucket,
                    object_key,
                    ExtraArgs={
                        "ContentType": content_type,
                        # S3 metadata travels in ASCII-only HTTP headers. Keep
                        # Unicode in the object key/DB and encode only this boundary.
                        "Metadata": {
                            "sha256": sha256,
                            "filename": quote(safe_name, safe="", encoding="utf-8"),
                            "filename-encoding": "utf-8-percent",
                        },
                    },
                )
        finally:
            tmp_path.unlink(missing_ok=True)
        return StoredObject(
            object_key=object_key,
            filename=safe_name,
            content_type=content_type,
            size_bytes=size,
            sha256=sha256,
        )

    def publish_path(self, object_key: str, source_path: Path, content_type: str) -> None:
        """Publish a finalized camera alias without changing its stable object key."""
        object_key = self._normalize_object_key(object_key)
        with source_path.open("rb") as body:
            self.client.upload_fileobj(body, self.bucket, object_key,
                                       ExtraArgs={"ContentType": content_type})
        # An earlier live download must not shadow the finalized object.
        self._path_under(self.cache_dir, object_key).unlink(missing_ok=True)

    def path_for(self, object_key: str) -> Path:
        object_key = self._normalize_object_key(object_key)
        # Capture aliases are shared by API/worker. Only explicitly sealed,
        # unchanged files may bypass S3; never return a live AVI this way.
        local = self._path_under(get_settings().object_storage_dir, object_key)
        marker = local.with_suffix(local.suffix + ".sealed")
        if local.is_file() and marker.is_file():
            try:
                if int(marker.read_text(encoding="utf-8")) == local.stat().st_size:
                    return local
            except (OSError, ValueError):
                pass
        target = self._path_under(self.cache_dir, object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            # Do not duplicate the original filename in the temporary name:
            # long upload names can otherwise exceed Windows' path limit.
            temp_target = target.with_name(f".{uuid4().hex}.tmp")
            try:
                try:
                    self.client.download_file(self.bucket, object_key, str(temp_target))
                except ClientError as exc:
                    if str(exc.response.get("Error", {}).get("Code", "")) in {"404", "NoSuchKey", "NotFound"}:
                        raise FileNotFoundError("素材文件未上传或已移除") from exc
                    raise
                temp_target.replace(target)
            finally:
                temp_target.unlink(missing_ok=True)
        return target

    def url_for(self, object_key: str) -> str:
        object_key = self._normalize_object_key(object_key)
        settings = get_settings()
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=settings.s3_presigned_url_seconds,
        )

    def signed_url_for(self, object_key: str) -> str:
        object_key = self._normalize_object_key(object_key)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=get_settings().s3_presigned_url_seconds,
        )

    def delete(self, object_key: str) -> None:
        object_key = self._normalize_object_key(object_key)
        self.client.delete_object(Bucket=self.bucket, Key=object_key)
        self._path_under(self.cache_dir, object_key).unlink(missing_ok=True)

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)


def build_storage() -> ObjectStorage:
    mode = get_settings().object_storage_mode.lower()
    if mode in {"s3", "minio"}:
        return S3ObjectStorage()
    if mode in {"local", "filesystem", "file"}:
        return LocalObjectStorage()
    raise RuntimeError(f"Unsupported OBJECT_STORAGE_MODE: {get_settings().object_storage_mode}")


storage = build_storage()
