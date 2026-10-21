from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field
from pydantic import ConfigDict, field_validator, field_serializer
from app.services.camera_policy import validate_camera_source


class CameraInput(BaseModel):
    @field_validator("camera_source", check_fields=False)
    @classmethod
    def authorized_camera(cls, value):
        return validate_camera_source(value) if value else value



class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=4, max_length=12)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class EmailCodeRequest(BaseModel):
    email: EmailStr
    purpose: str = Field(default="login", pattern="^(register|login)$")
    username: Optional[str] = Field(default=None, min_length=3, max_length=64)


class EmailCodeLoginRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class UserAdminUpdate(BaseModel):
    role: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


class UserAdminCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: Optional[EmailStr] = None
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(default="operator", pattern="^(admin|operator|viewer)$")
    is_active: bool = True


class UserPasswordReset(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    username: str
    email: Optional[str] = None
    avatar_url: str = ""
    role: str
    is_active: bool
    created_at: datetime
    active_refresh_tokens: int = 0
    project_count: int = 0


class ProjectCreate(CameraInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    project_type: str = Field(default="media", pattern="^(media|camera)$")
    camera_source: str = Field(default="", max_length=128)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None


class ProjectOut(BaseModel):
    @field_serializer("camera_source")
    def public_source(self, value):
        from app.services.camera_policy import public_camera_source
        return public_camera_source(value)

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    project_type: str = "media"
    camera_source: str = ""
    owner_username: str
    created_at: datetime


class ProjectMemberUpsert(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=64)
    role: str = Field(default="viewer", pattern="^(owner|operator|viewer)$")


class ProjectMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str
    role: str
    created_at: datetime


class JobOut(BaseModel):
    id: str
    type: str
    status: str
    progress: float
    message: str = ""
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SearchFilters(BaseModel):
    camera_id: Optional[str] = None
    media_id: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    min_similarity: Optional[float] = Field(default=None, ge=-1.0, le=1.0)


class TextSearchRequest(BaseModel):
    project_id: int
    query_text: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=50, ge=1, le=500)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=12, ge=1, le=100)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class SearchResultOut(BaseModel):
    id: Optional[int] = None
    rank: Optional[int] = None
    target_id: Optional[int] = None
    path: str
    thumbnail_url: Optional[str] = None
    similarity: float
    source_info: str
    media_id: Optional[int] = None
    media_filename: Optional[str] = None
    media_type: Optional[str] = None
    camera_id: Optional[str] = None
    timestamp: Optional[str] = None
    timestamp_seconds: Optional[float] = None
    original_media_url: Optional[str] = None
    original_media_path: Optional[str] = None
    video_seek_seconds: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchPagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_previous: bool
    has_next: bool


class SearchResponse(BaseModel):
    status: str = "success"
    task_id: Optional[int] = None
    query_object_url: Optional[str] = None
    pagination: Optional[SearchPagination] = None
    results: List[SearchResultOut]


class CameraStartRequest(CameraInput):
    camera_source: str = Field(default="0", max_length=128)
    sample_interval_seconds: float = Field(default=1.0, ge=0.2, le=10.0)
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)


class ScenicCameraPointCreate(CameraInput):
    name: str = Field(min_length=1, max_length=128)
    camera_source: str = Field(default="0", max_length=128)
    camera_id: str = Field(default="", max_length=128)
    location: str = Field(default="", max_length=255)
    role: str = Field(default="play", pattern="^(entry|play|exit)$")
    is_active: bool = True


class ScenicCameraPointUpdate(CameraInput):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    camera_source: Optional[str] = Field(default=None, max_length=128)
    camera_id: Optional[str] = Field(default=None, max_length=128)
    location: Optional[str] = Field(default=None, max_length=255)
    role: Optional[str] = Field(default=None, pattern="^(entry|play|exit)$")
    is_active: Optional[bool] = None


class TouristMatchRequest(BaseModel):
    top_k: int = Field(default=80, ge=1, le=500)
    min_similarity: float = Field(default=0.2, ge=-1.0, le=1.0)
    clip_padding_seconds: float = Field(default=2.0, ge=0.0, le=20.0)
    min_clip_seconds: float = Field(default=3.0, ge=1.0, le=30.0)
    max_clip_seconds: float = Field(default=8.0, ge=1.0, le=60.0)


class TouristSegmentUpdate(BaseModel):
    included: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0)


class TouristSegmentBulkUpdate(BaseModel):
    segment_ids: List[int] = Field(default_factory=list)
    included: bool


class ScenicOrderCreate(BaseModel):
    vlog_job_id: Optional[int] = Field(default=None, gt=0)
    # Compatibility fields for older desktop clients. Trusted pricing is
    # resolved from server configuration when the order is created.
    amount_cents: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=16)
    payment_method: str = Field(default="manual", pattern="^(manual|wechat|wechat_native|simulated)$")


class ScenicOrderPaymentResult(BaseModel):
    result: str = Field(pattern="^(success|failed)$")
