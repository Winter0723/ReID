#!/usr/bin/env sh
set -eu

APP_ENV_VALUE="${APP_ENV:-development}"
SECRET_KEY_VALUE="${SECRET_KEY:-}"
GALLERY_DIR_VALUE="${GALLERY_DIR:-./gallery}"
OBJECT_STORAGE_DIR_VALUE="${OBJECT_STORAGE_DIR:-./object_storage}"
MODEL_PATHS="${YOLO_MODEL_PATH:-} ${CLIPREID_MODEL_PATH:-} ${CUSTOM_REID_MODEL_PATH:-} ${CUSTOM_DETECTION_MODEL_PATH:-} ${ONNX_MODEL_PATH:-} ${ONNX_DETECTION_MODEL_PATH:-} ${TENSORRT_ENGINE_PATH:-} ${TENSORRT_DETECTION_ENGINE_PATH:-}"
if [ "${EMBEDDING_BACKEND:-clipreid}" = "clipreid" ]; then
  MODEL_PATHS="$MODEL_PATHS /workspace/model/clip_weights/ViT-B-16.pt"
fi

mkdir -p "$GALLERY_DIR_VALUE" "$OBJECT_STORAGE_DIR_VALUE"

if [ "$APP_ENV_VALUE" = "production" ] && { [ -z "$SECRET_KEY_VALUE" ] || [ "$SECRET_KEY_VALUE" = "change-me-before-production" ] || [ "$SECRET_KEY_VALUE" = "your-super-secret-key-for-interview-demo" ]; }; then
  echo "Refusing to start production with an insecure SECRET_KEY" >&2
  exit 1
fi

for model_path in $MODEL_PATHS; do
  if [ -n "$model_path" ] && [ ! -f "$model_path" ]; then
    echo "Configured model file is not mounted: $model_path" >&2
    exit 1
  fi
  if [ -n "$model_path" ] && grep -q '^version https://git-lfs.github.com/spec/' "$model_path"; then
    echo "Configured model is still a Git LFS pointer; run 'git lfs pull': $model_path" >&2
    exit 1
  fi
done

if [ "${RUN_MIGRATIONS_ON_START:-1}" = "1" ]; then
  MYREID_MIGRATION_SCOPE=core python -m alembic -c alembic.ini upgrade core@head
  MYREID_MIGRATION_SCOPE=scenic python -m alembic -c alembic.scenic.ini upgrade scenic@head
fi

exec "$@"
