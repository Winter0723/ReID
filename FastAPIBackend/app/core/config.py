import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
PRODUCTION_LIKE_ENVIRONMENTS = frozenset({"production", "prod", "staging"})
INSECURE_SECRET_KEYS = frozenset(
    {
        "",
        "change-me-before-production",
        "your-super-secret-key-for-interview-demo",
        "replace-with-long-random-production-secret",
        "replace-with-staging-secret",
    }
)


ENV_FILES = (
    PROJECT_ROOT / ".env",
    BACKEND_DIR / ".env",
    PROJECT_ROOT / ".env.local",
    BACKEND_DIR / ".env.local",
)


def _expanded_path(value: os.PathLike | str) -> Path:
    return Path(os.path.expandvars(os.fspath(value))).expanduser()


def resolve_runtime_path(value: os.PathLike | str, base_dir: Path = BACKEND_DIR) -> Path:
    path = _expanded_path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def resolve_existing_file(value: str) -> str:
    if not value:
        return ""
    raw = _expanded_path(value)
    if raw.is_absolute():
        return str(raw)
    for base_dir in (PROJECT_ROOT, BACKEND_DIR, Path.cwd()):
        candidate = (base_dir / raw).resolve()
        if candidate.exists():
            return str(candidate)
    return os.fspath(raw)


def resolve_database_url(value: str, base_dir: Path = BACKEND_DIR) -> str:
    value = value.strip()
    if not value.startswith("sqlite:///") or value == "sqlite:///:memory:":
        return value
    raw_path = value.removeprefix("sqlite:///")
    db_path = _expanded_path(raw_path)
    if not db_path.is_absolute():
        db_path = (base_dir / db_path).resolve()
    return f"sqlite:///{db_path.as_posix()}"


def redact_database_url(value: str) -> str:
    """Return an operator-friendly URL without credentials or local directories."""

    url = make_url(value)
    if url.get_backend_name() == "sqlite":
        if url.database in {None, "", ":memory:"}:
            database_name = url.database or ""
        else:
            database_name = Path(url.database).name
        return f"{url.drivername}:///{database_name}"
    return url.render_as_string(hide_password=True)


def resolve_scenic_database_url(
    value: str,
    *,
    primary_database_url: str,
    app_env: str,
    base_dir: Path = BACKEND_DIR,
) -> str:
    """Resolve the independent scenic database and reject unsafe production fallbacks."""

    production_like = app_env.strip().lower() in PRODUCTION_LIKE_ENVIRONMENTS
    raw_value = value.strip()
    if raw_value:
        resolved = resolve_database_url(raw_value, base_dir)
    elif production_like:
        raise RuntimeError("SCENIC_DATABASE_URL is required in staging and production")
    elif primary_database_url.startswith("sqlite:///") and primary_database_url != "sqlite:///:memory:":
        primary_path = Path(primary_database_url.removeprefix("sqlite:///"))
        scenic_path = primary_path.with_name("scenic_system.db")
        resolved = f"sqlite:///{scenic_path.as_posix()}"
    else:
        resolved = resolve_database_url("sqlite:///./scenic_system.db", base_dir)

    if production_like and resolved.startswith("sqlite"):
        raise RuntimeError("SCENIC_DATABASE_URL must use a production database in staging and production")
    return resolved


def ensure_external_tool_env() -> None:
    if not os.getenv("YOLO_CONFIG_DIR"):
        object_storage_dir = get_settings().object_storage_dir
        object_storage_dir.mkdir(parents=True, exist_ok=True)
        os.environ["YOLO_CONFIG_DIR"] = str(object_storage_dir)


def _validate_production_settings(
    *,
    app_env: str,
    secret_key: str,
    scenic_order_price_raw: str | None,
    scenic_order_currency_raw: str | None,
    allow_local_job_fallback: bool,
    email_code_dev_mode: bool,
    database_url: str,
    bootstrap_admin_email: str,
    metrics_token: str,
) -> None:
    if app_env not in PRODUCTION_LIKE_ENVIRONMENTS:
        return
    normalized_secret = secret_key.strip()
    if normalized_secret in INSECURE_SECRET_KEYS or len(normalized_secret.encode("utf-8")) < 32:
        raise RuntimeError(f"SECRET_KEY must contain at least 32 bytes of non-placeholder data in {app_env}")
    if secret_key != normalized_secret:
        raise RuntimeError(f"SECRET_KEY must not have leading or trailing whitespace in {app_env}")
    if scenic_order_price_raw is None or not scenic_order_price_raw.strip():
        raise RuntimeError(f"SCENIC_ORDER_PRICE_CENTS is required in {app_env}")
    if scenic_order_currency_raw is None or not scenic_order_currency_raw.strip():
        raise RuntimeError(f"SCENIC_ORDER_CURRENCY is required in {app_env}")
    if allow_local_job_fallback:
        raise RuntimeError(f"ALLOW_LOCAL_JOB_FALLBACK must be disabled in {app_env}")
    if email_code_dev_mode:
        raise RuntimeError(f"EMAIL_CODE_DEV_MODE must be disabled in {app_env}")
    if database_url.startswith("sqlite:"):
        raise RuntimeError(f"DATABASE_URL must use a server database in {app_env}")
    if not bootstrap_admin_email:
        raise RuntimeError(f"BOOTSTRAP_ADMIN_EMAIL is required in {app_env}")
    if len(metrics_token.encode("utf-8")) < 32 or metrics_token.lower().startswith("replace-"):
        raise RuntimeError(f"METRICS_TOKEN must contain at least 32 bytes of non-placeholder data in {app_env}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=() if os.getenv("APP_ENV") == "test" else tuple(str(path) for path in ENV_FILES),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "行人重识别系统"
    app_env: Literal["development", "dev", "local", "test", "staging", "production", "prod"] = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./reid_system.db"
    scenic_database_url: str = ""
    secret_key: str = "your-super-secret-key-for-interview-demo"
    bootstrap_admin_email: str = ""
    public_registration_role: Literal["viewer", "operator"] = "viewer"
    camera_sources: dict[str, str] = Field(default_factory=lambda: {"0": "0"})
    visitor_require_staff_verification: bool = True
    visitor_operations_per_hour: int = Field(default=30, ge=1, le=1000)
    visitor_project_operations_per_hour: int = Field(default=300, ge=1, le=10000)
    visitor_retention_days: int = Field(default=30, ge=1, le=365)
    metrics_token: str = ""
    enable_legacy_api: bool = False
    allow_local_job_fallback: bool = True
    camera_auto_start: bool = False
    core_camera_auto_start: bool = True
    scenic_camera_auto_start: bool = True
    camera_capture_width: int = Field(default=640, ge=160, le=3840)
    camera_capture_height: int = Field(default=480, ge=120, le=2160)
    camera_preview_fps: int = Field(default=30, ge=1, le=60)
    camera_preview_width: int = Field(default=640, ge=160, le=1920)
    camera_jpeg_quality: int = Field(default=75, ge=30, le=95)
    camera_recording_retention_hours: float = Field(default=12, ge=1, le=8760)
    camera_recording_segment_seconds: float = Field(default=10, ge=1, le=3600)
    camera_recording_segment_targets: int = Field(default=0, ge=0, le=100000)
    camera_recording_segment_max_bytes: int = Field(default=512 * 1024 * 1024, ge=1024 * 1024)
    scenic_editing_enabled: bool = True
    scenic_tracking_enabled: bool = False
    visitor_auth_provider: Literal["wechat", "test", "browser"] = "wechat"
    visitor_payment_provider: Literal["wechat", "test", "manual"] = "wechat"
    visitor_test_invite_code: str = ""
    visitor_wechat_appid: str = ""
    visitor_wechat_secret: str = ""
    visitor_public_base_url: str = ""
    visitor_cookie_secure: bool = True
    visitor_allow_lan_http: bool = False
    visitor_auto_publish: bool = False
    visitor_auto_similarity: float = Field(default=0.9, ge=0, le=1)
    visitor_index_wait_seconds: int = Field(default=600, ge=30, le=3600)
    scenic_public_base_url: str = ""
    algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0, le=60 * 24 * 7)
    refresh_token_expire_minutes: int = Field(default=60 * 24 * 7, gt=0, le=60 * 24 * 90)
    gallery_dir: Path = Path("./gallery")
    object_storage_mode: Literal["local", "s3", "minio"] = "local"
    object_storage_dir: Path = Path("./object_storage")
    s3_endpoint_url: str = ""
    s3_bucket: str = "myreid-media"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"
    s3_public_base_url: str = ""
    s3_presigned_url_seconds: int = 60 * 60
    asset_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    vector_backend: str = "milvus"
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "reid_clip_embeddings"
    redis_url: str = "redis://localhost:6379/0"
    redis_connect_timeout_seconds: float = Field(default=1.0, gt=0, le=30)
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0, le=60)
    rq_queue_name: str = "media"
    rq_job_timeout_seconds: int = 60 * 60 * 6
    clip_model_id: str = "openai/clip-vit-base-patch32"
    yolo_model_path: str = "yolov8n.pt"
    model_service_backend: Literal["baseline"] = "baseline"
    embedding_backend: Literal["clipreid"] = "clipreid"
    detection_backend: Literal["yolo"] = "yolo"
    embedding_dimension: int = 512
    model_device: str = "auto"
    yolo_precision: Literal["auto", "fp32", "fp16"] = "auto"
    yolo_image_size: int = Field(default=640, ge=320, le=1280, multiple_of=32)
    clipreid_precision: Literal["auto", "fp32", "fp16", "int8"] = "auto"
    realtime_enter_threshold: float = Field(default=0.65, ge=-1, le=1)
    realtime_camera_inference_fps: int = Field(default=30, ge=1, le=60)
    realtime_exit_threshold: float = Field(default=0.55, ge=-1, le=1)
    realtime_margin: float = Field(default=0.08, ge=0, le=2)
    realtime_origins: str = "http://localhost:35173,http://127.0.0.1:35173,http://localhost:5173,http://127.0.0.1:5173,http://tauri.localhost,https://tauri.localhost,tauri://localhost"

    custom_reid_model_path: str = ""
    clipreid_model_path: str = "model/ViT-MSMT17-60.pth"
    clipreid_model_name: str = "ViT-B-16"
    clipreid_dataset_name: str = "MSMT17"
    clipreid_num_classes: int = 1041
    clipreid_camera_num: int = 15
    clipreid_view_num: int = 1
    clipreid_image_height: int = 336
    clipreid_image_width: int = 160
    clipreid_stride_size: int = 16
    custom_detection_model_path: str = ""
    onnx_model_path: str = ""
    onnx_detection_model_path: str = ""
    tensorrt_engine_path: str = ""
    tensorrt_detection_engine_path: str = ""
    gpu_inference_url: str = ""
    max_upload_mb: int = 1024
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    email_code_ttl_minutes: int = 10
    email_code_dev_mode: bool = False
    scenic_order_price_cents: int = Field(default=990, gt=0)
    scenic_order_currency: str = Field(default="CNY", min_length=3, max_length=16)
    scenic_delivery_token_ttl_hours: int = Field(default=24 * 30, gt=0, le=24 * 365)
    payment_provider: str = ""
    payment_public_base_url: str = ""
    wechat_pay_appid: str = ""
    wechat_pay_mchid: str = ""
    wechat_pay_api_v3_key: str = ""
    wechat_pay_merchant_serial_no: str = ""
    wechat_pay_merchant_private_key_path: str = ""
    wechat_pay_platform_public_key_id: str = ""
    wechat_pay_platform_public_key_path: str = ""
    log_level: str = "INFO"
    disk_alert_free_percent: float = 10.0
    disk_alert_free_bytes: int = 1024 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    app_env = settings.app_env.strip().lower()
    if settings.visitor_auth_provider == "browser" and app_env not in {"development", "dev", "local", "test"}:
        raise RuntimeError("游客浏览器演示登录仅允许在开发或测试环境启用")
    if "test" in {settings.visitor_auth_provider, settings.visitor_payment_provider}:
        if app_env not in {"development", "dev", "local", "test"}:
            raise RuntimeError("游客联调模式仅允许在开发或测试环境启用")
        if len(settings.visitor_test_invite_code) < 16:
            raise RuntimeError("游客联调模式需要至少 16 位 VISITOR_TEST_INVITE_CODE")
    if app_env in PRODUCTION_LIKE_ENVIRONMENTS:
        if not settings.visitor_cookie_secure or settings.visitor_allow_lan_http:
            raise RuntimeError("Production requires VISITOR_COOKIE_SECURE=1 and VISITOR_ALLOW_LAN_HTTP=0")
        if settings.smtp_host and not (settings.smtp_use_tls or settings.smtp_use_ssl or settings.smtp_port == 465):
            raise RuntimeError("Production SMTP requires verified TLS")
    database_url = resolve_database_url(settings.database_url)
    scenic_database_url = resolve_scenic_database_url(
        settings.scenic_database_url,
        primary_database_url=database_url,
        app_env=app_env,
    )
    if settings.enable_legacy_api:
        raise RuntimeError(
            "ENABLE_LEGACY_API is no longer supported; use the archived legacy documentation instead"
        )

    fields_set = settings.model_fields_set
    _validate_production_settings(
        app_env=app_env,
        secret_key=settings.secret_key,
        scenic_order_price_raw=(
            str(settings.scenic_order_price_cents)
            if "scenic_order_price_cents" in fields_set
            else None
        ),
        scenic_order_currency_raw=(
            settings.scenic_order_currency
            if "scenic_order_currency" in fields_set
            else None
        ),
        allow_local_job_fallback=settings.allow_local_job_fallback,
        email_code_dev_mode=settings.email_code_dev_mode,
        database_url=database_url,
        bootstrap_admin_email=settings.bootstrap_admin_email.strip().lower(),
        metrics_token=settings.metrics_token.strip(),
    )

    smtp_use_ssl = settings.smtp_use_ssl
    smtp_use_tls = settings.smtp_use_tls
    if settings.smtp_port == 465 and "smtp_use_ssl" not in fields_set:
        smtp_use_ssl = True
        if "smtp_use_tls" not in fields_set:
            smtp_use_tls = False

    path_updates = {
        "gallery_dir": resolve_runtime_path(settings.gallery_dir),
        "object_storage_dir": resolve_runtime_path(settings.object_storage_dir),
        "yolo_model_path": resolve_existing_file(settings.yolo_model_path),
        "custom_reid_model_path": resolve_existing_file(settings.custom_reid_model_path),
        "clipreid_model_path": resolve_existing_file(settings.clipreid_model_path),
        "custom_detection_model_path": resolve_existing_file(settings.custom_detection_model_path),
        "onnx_model_path": resolve_existing_file(settings.onnx_model_path),
        "onnx_detection_model_path": resolve_existing_file(settings.onnx_detection_model_path),
        "tensorrt_engine_path": resolve_existing_file(settings.tensorrt_engine_path),
        "tensorrt_detection_engine_path": resolve_existing_file(settings.tensorrt_detection_engine_path),
        "wechat_pay_merchant_private_key_path": resolve_existing_file(
            settings.wechat_pay_merchant_private_key_path
        ),
        "wechat_pay_platform_public_key_path": resolve_existing_file(
            settings.wechat_pay_platform_public_key_path
        ),
    }
    return settings.model_copy(
        update={
            **path_updates,
            "app_env": app_env,
            "database_url": database_url,
            "scenic_database_url": scenic_database_url,
            "bootstrap_admin_email": settings.bootstrap_admin_email.strip().lower(),
            "metrics_token": settings.metrics_token.strip(),
            "scenic_order_currency": settings.scenic_order_currency.strip().upper(),
            "payment_public_base_url": settings.payment_public_base_url.rstrip("/"),
            "smtp_use_ssl": smtp_use_ssl,
            "smtp_use_tls": smtp_use_tls,
        }
    )
