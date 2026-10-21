"""Compatibility exports for the archived legacy API and older tests.

Supported routers import the focused service modules directly. Keep this file
small and explicit so new production code cannot accidentally grow another
catch-all dependency surface.
"""

from fastapi import HTTPException

import database
from app.core.config import get_settings
from app.core.security import get_password_hash, verify_password
from app.services.camera_stream import camera_stream_manager
from app.services.email_service import create_email_code, verify_email_code
from app.services.job_manager import LocalJobFallbackDisabled, job_manager
from app.services.legacy_service import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ACTIVE_SESSIONS,
    ALGORITHM,
    GUEST_USAGE,
    SECRET_KEY,
    check_guest_limit,
    get_request_context,
    migrate_guest_data_to_user,
    normalize_email,
    process_gallery_features,
    process_video_and_extract_features,
)
from app.services.media_service import (
    BASE_GALLERY_DIR,
    IMAGE_UPLOAD_EXTENSIONS,
    MAX_AVATAR_BYTES,
    MAX_BATCH_UPLOAD_FILES,
    VIDEO_UPLOAD_EXTENSIONS,
    ImagePixelLimitError,
    _load_verified_rgb_image,
    _media_download_url,
    _media_video_seek_seconds,
    _merge_central_camera_status,
    _public_gallery_path,
    _timestamp_to_seconds_for_payload,
)
from app.services.model_runtime import clip_image_feature, clip_text_feature
from app.services.search_service import _compact_filters, _pagination_payload
from app.services.storage import copy_upload_limited, read_upload_limited, storage
from app.services.vector_store import vector_store


def _create_email_code(db, email: str, purpose: str):
    return create_email_code(db, database.EmailLoginCode, email, purpose)


def _verify_email_code(db, email: str, code: str, purpose: str):
    return verify_email_code(db, database.EmailLoginCode, email, code, purpose)
