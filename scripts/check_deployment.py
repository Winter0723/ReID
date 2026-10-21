"""Validate portable Docker configuration without importing the app or contacting services."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Unsupported env syntax; use the provided templates")
        if key in values:
            raise ValueError(f"Duplicate configuration key: {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        def expand(match):
            if match[1] not in values:
                raise ValueError(f"{key} references an undefined variable")
            return values[match[1]]
        values[key] = re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", expand, value)
    return values


def validate(values, *, staff_only=False):
    errors = []
    def need(key, minimum=1, real=False):
        value = values.get(key, "")
        if len(value) < minimum or value.startswith(("replace-", "change-me")) or (real and ("example." in value or ".invalid" in value)):
            errors.append(f"{key}: supply a non-placeholder value (minimum {minimum} characters)")
    production = values.get("APP_ENV") in {"production", "prod", "staging"}
    if values.get("APP_ENV") not in {"production", "prod", "staging", "development", "dev", "local", "test"}:
        errors.append("APP_ENV: unknown environment name")
    for key in ("STAFF_DOMAIN", "VISITOR_DOMAIN"):
        domain = values.get(key, "")
        if domain and not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
            errors.append(f"{key}: use a DNS hostname without scheme, port or path")
    for key in ("SECRET_KEY", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD"):
        need(key, 32)
    for key, dbname in (("DATABASE_URL", "POSTGRES_DB"), ("SCENIC_DATABASE_URL", "SCENIC_POSTGRES_DB")):
        url = urlsplit(values.get(key, ""))
        if (url.scheme != "postgresql+psycopg" or url.hostname != "postgres" or
                unquote(url.username or "") != values.get("POSTGRES_USER") or
                unquote(url.password or "") != values.get("POSTGRES_PASSWORD") or
                url.path.lstrip("/") != values.get(dbname)):
            errors.append(f"{key}: must match the Compose PostgreSQL user/password/database and postgres hostname")
    if values.get("POSTGRES_DB") == values.get("SCENIC_POSTGRES_DB"):
        errors.append("DATABASE_URL and SCENIC_DATABASE_URL must use separate databases")
    if values.get("REDIS_URL") != "redis://redis:6379/0":
        errors.append("REDIS_URL: portable stack expects redis://redis:6379/0")
    if values.get("OBJECT_STORAGE_MODE") in {"s3", "minio"}:
        for key in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL"):
            need(key)
        if values.get("S3_ENDPOINT_URL") == "http://minio:9000" and (
            values.get("S3_ACCESS_KEY_ID") != values.get("MINIO_ROOT_USER") or
            values.get("S3_SECRET_ACCESS_KEY") != values.get("MINIO_ROOT_PASSWORD")):
            errors.append("S3 credentials must match the bundled MinIO credentials")
    if production:
        need("METRICS_TOKEN", 32)
        for key in ("BOOTSTRAP_ADMIN_EMAIL", "SMTP_HOST", "SMTP_FROM", "SMTP_USERNAME", "SMTP_PASSWORD"):
            need(key, real=True)
        for key, expected in (("EMAIL_CODE_DEV_MODE", "0"), ("ALLOW_LOCAL_JOB_FALLBACK", "0"),
                              ("VISITOR_COOKIE_SECURE", "1"), ("VISITOR_ALLOW_LAN_HTTP", "0")):
            if values.get(key) != expected:
                errors.append(f"{key}: production requires {expected}")
        if values.get("SMTP_USE_TLS") != "1" and values.get("SMTP_USE_SSL") != "1":
            errors.append("SMTP requires STARTTLS or SSL")
        for key in ("VISITOR_AUTH_PROVIDER", "VISITOR_PAYMENT_PROVIDER"):
            if values.get(key) == "test":
                errors.append(f"{key}: test mode is forbidden in production")
        if not staff_only:
            for key in ("VISITOR_WECHAT_APPID", "VISITOR_WECHAT_SECRET", "VISITOR_PUBLIC_BASE_URL"):
                need(key, real=True)
            if not values.get("VISITOR_PUBLIC_BASE_URL", "").startswith("https://"):
                errors.append("VISITOR_PUBLIC_BASE_URL: full production visitor service requires HTTPS")
            if values.get("VISITOR_DOMAIN") and values.get("VISITOR_PUBLIC_BASE_URL") != "https://" + values["VISITOR_DOMAIN"]:
                errors.append("VISITOR_PUBLIC_BASE_URL must match VISITOR_DOMAIN")
        if values.get("STAFF_DOMAIN") and "https://" + values["STAFF_DOMAIN"] not in values.get("REALTIME_ORIGINS", "").split(","):
            errors.append("REALTIME_ORIGINS must include the HTTPS staff origin")
    elif not staff_only:
        if values.get("VISITOR_AUTH_PROVIDER") == "test":
            need("VISITOR_TEST_INVITE_CODE", 16)
            if values.get("VISITOR_COOKIE_SECURE") != "0":
                errors.append("VISITOR_COOKIE_SECURE: use 0 for local HTTP test login")
        need("VISITOR_PUBLIC_BASE_URL")
    if values.get("PAYMENT_PROVIDER") != "manual" or values.get("VISITOR_PAYMENT_PROVIDER") != "manual":
        errors.append("This deployment guide supports manual payment only; see WECHAT_PAYMENT_INTEGRATION.md")
    try:
        cameras = json.loads(values.get("CAMERA_SOURCES", "{}"))
        if not isinstance(cameras, dict) or not all(isinstance(value, str) and value for value in cameras.values()):
            errors.append("CAMERA_SOURCES must contain a JSON object mapping IDs to non-empty source strings")
        elif values.get("MYREID_CAMERA_SOURCE", "0") not in cameras:
            errors.append("MYREID_CAMERA_SOURCE must exist in CAMERA_SOURCES")
    except (TypeError, ValueError):
        errors.append("CAMERA_SOURCES must contain a JSON object")
    return errors


def check_models():
    errors = []
    for item in json.loads((ROOT / "model/checkpoints.json").read_text(encoding="utf-8")):
        path = ROOT / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            errors.append(f"Missing or wrong-size model: {item['path']}")
            continue
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                errors.append(f"Wrong model SHA256: {item['path']}")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--staff-only", action="store_true", help="Skip visitor OAuth prerequisites")
    parser.add_argument("--skip-models", action="store_true", help="Configuration-only check; not a deployment acceptance")
    args = parser.parse_args()
    try:
        errors = validate(read_env(args.env_file), staff_only=args.staff_only)
        if not args.skip_models:
            errors.extend(check_models())
    except (ValueError, OSError) as exc:
        errors = [str(exc)]
    if errors:
        raise SystemExit("\n".join(errors))
    print("Deployment preflight passed; SMTP/OAuth/network availability still require live acceptance.")
