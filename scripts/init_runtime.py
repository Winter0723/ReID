from pathlib import Path
import argparse
import json
import os
import subprocess
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "FastAPIBackend"
INSECURE_SECRET_KEYS = {"", "change-me-before-production", "your-super-secret-key-for-interview-demo"}
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings, redact_database_url


def configured_paths():
    settings = get_settings()
    return {
        "gallery": settings.gallery_dir,
        "object_storage": settings.object_storage_dir,
    }


def configured_model_paths():
    settings = get_settings()
    values = {
        "YOLO_MODEL_PATH": settings.yolo_model_path,
        "CLIPREID_MODEL_PATH": settings.clipreid_model_path,
        "CUSTOM_REID_MODEL_PATH": settings.custom_reid_model_path,
        "CUSTOM_DETECTION_MODEL_PATH": settings.custom_detection_model_path,
        "ONNX_MODEL_PATH": settings.onnx_model_path,
        "ONNX_DETECTION_MODEL_PATH": settings.onnx_detection_model_path,
        "TENSORRT_ENGINE_PATH": settings.tensorrt_engine_path,
        "TENSORRT_DETECTION_ENGINE_PATH": settings.tensorrt_detection_engine_path,
    }
    return {name: value for name, value in values.items() if value}


def main():
    parser = argparse.ArgumentParser(description="Initialize and validate a MyReID runtime environment.")
    parser.add_argument("--migrate", action="store_true", help="Run Alembic migrations to head.")
    parser.add_argument("--strict-models", action="store_true", help="Fail if any configured model path is missing.")
    args = parser.parse_args()

    import importlib.util
    if importlib.util.find_spec("lap") is None:
        raise SystemExit("Real-time ByteTrack requires lap; install backend requirements")
    settings = get_settings()
    app_env = settings.app_env
    secret_key = settings.secret_key
    if app_env == "production" and secret_key in INSECURE_SECRET_KEYS:
        raise SystemExit("APP_ENV=production requires a non-default SECRET_KEY")

    paths = configured_paths()
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    missing_models = {
        name: value
        for name, value in configured_model_paths().items()
        if value and not Path(value).exists()
    }
    if missing_models and args.strict_models:
        raise SystemExit(f"Missing configured model files: {missing_models}")

    if args.migrate:
        for migration_scope in ("core", "scenic"):
            migration_env = os.environ.copy()
            migration_env["MYREID_MIGRATION_SCOPE"] = migration_scope
            alembic_config = "alembic.ini" if migration_scope == "core" else "alembic.scenic.ini"
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", alembic_config, "upgrade", f"{migration_scope}@head"],
                cwd=BACKEND_DIR,
                env=migration_env,
                check=True,
            )

    print(json.dumps({
        "status": "runtime initialized",
        "environment": app_env,
        "paths": {name: str(path) for name, path in paths.items()},
        "database_url": redact_database_url(settings.database_url),
        "missing_models": missing_models,
        "migrated": args.migrate,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
