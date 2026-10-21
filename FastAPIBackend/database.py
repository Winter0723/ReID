import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from app.core.config import get_settings
from app.db.orm_mixins import CreatedAtMixin, IntegerIdMixin, TimestampMixin
from app.db.runtime import (
    check_connection,
    configure_sqlite_engine,
    database_info,
    initialize_domain_database,
    session_dependency,
)
from app.services.time_utils import utc_now

SQLALCHEMY_DATABASE_URL = get_settings().database_url
IS_SQLITE = SQLALCHEMY_DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {}
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
configure_sqlite_engine(engine)

DEFAULT_ROLES = ("admin", "operator", "viewer")


class Role(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "roles"
    name = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(Text, default="", nullable=False)

    users = relationship("User", back_populates="role_ref")


class User(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "users"
    public_id = Column(String(36), unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(128), unique=True, nullable=False, index=True)
    email = Column(String(254), unique=True, nullable=True, index=True)
    avatar_url = Column(String(1024), default="", nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(64), ForeignKey("roles.name"), default="operator", nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)

    role_ref = relationship("Role", back_populates="users")
    projects = relationship("Project", back_populates="owner")
    media_files = relationship("MediaFile", back_populates="uploader")
    search_tasks = relationship("SearchTask", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")

class SearchHistory(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "search_histories"
    user_identifier = Column(String(255), nullable=False, index=True)
    session_id = Column(String(255), nullable=False, index=True)
    query_type = Column(String(64), nullable=False)
    query_content = Column(Text, nullable=False)
    results_json = Column(Text, nullable=False)


class Project(IntegerIdMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, default="", nullable=False)
    project_type = Column(String(32), default="media", nullable=False, index=True)
    camera_source = Column(String(128), default="", nullable=False)
    owner_username = Column(String(128), ForeignKey("users.username"), nullable=False, index=True)

    owner = relationship("User", back_populates="projects")
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    media_files = relationship("MediaFile", back_populates="project", cascade="all, delete-orphan")
    detection_targets = relationship("DetectionTarget", back_populates="project", cascade="all, delete-orphan")
    processing_jobs = relationship("ProcessingJob", back_populates="project", cascade="all, delete-orphan")
    search_tasks = relationship("SearchTask", back_populates="project", cascade="all, delete-orphan")
    scenic_camera_points = relationship("ScenicCameraPoint", back_populates="project", cascade="all, delete-orphan")
    tourist_sessions = relationship("TouristSession", back_populates="project", cascade="all, delete-orphan")


class MediaFile(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "media_files"
    __table_args__ = (
        Index(
            "uq_media_files_project_sha_active",
            "project_id",
            "sha256",
            unique=True,
            sqlite_where=text("sha256 IS NOT NULL AND status NOT IN ('failed', 'duplicate')"),
            postgresql_where=text("sha256 IS NOT NULL AND status NOT IN ('failed', 'duplicate')"),
        ),
    )
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_username = Column(String(128), ForeignKey("users.username"), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    object_key = Column(String(1024), unique=True, nullable=False, index=True)
    content_type = Column(String(128), default="", nullable=False)
    size_bytes = Column(BigInteger, default=0, nullable=False)
    sha256 = Column(String(64), nullable=True, index=True)
    duplicate_of_media_id = Column(Integer, ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True, index=True)
    media_type = Column(String(32), nullable=False, index=True)
    status = Column(String(32), default="uploaded", nullable=False, index=True)

    project = relationship("Project", back_populates="media_files")
    uploader = relationship("User", back_populates="media_files")
    duplicate_of = relationship("MediaFile", remote_side="MediaFile.id")
    detection_targets = relationship("DetectionTarget", back_populates="media_file", cascade="all, delete-orphan")
    processing_jobs = relationship("ProcessingJob", back_populates="media_file")


class DetectionTarget(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "detection_targets"
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id = Column(Integer, ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False, index=True)
    crop_path = Column(String(1024), nullable=False)
    crop_object_key = Column(String(1024), nullable=True, index=True)
    source_info = Column(Text, default="", nullable=False)
    camera_id = Column(String, nullable=True, index=True)
    timestamp = Column(String, nullable=True, index=True)
    vector_id = Column(String(128), unique=True, nullable=False, index=True)

    project = relationship("Project", back_populates="detection_targets")
    media_file = relationship("MediaFile", back_populates="detection_targets")
    search_results = relationship("SearchResult", back_populates="target")
    scenic_segments = relationship("TouristVisitSegment", back_populates="target")


class ScenicCameraPoint(IntegerIdMixin, TimestampMixin, Base):
    __tablename__ = "scenic_camera_points"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_scenic_camera_points_project_name"),)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    camera_source = Column(String(128), default="", nullable=False, index=True)
    camera_id = Column(String(128), default="", nullable=False, index=True)
    location = Column(String(255), default="", nullable=False)
    role = Column(String(32), default="play", nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    project = relationship("Project", back_populates="scenic_camera_points")


class TouristSession(IntegerIdMixin, TimestampMixin, Base):
    __tablename__ = "tourist_sessions"
    __table_args__ = (UniqueConstraint("project_id", "ticket_code", name="uq_tourist_sessions_project_ticket"),)

    public_id = Column(String(36), unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    ticket_code = Column(String(128), nullable=False, index=True)
    visitor_label = Column(String(128), default="", nullable=False)
    contact_hint = Column(String(64), default="", nullable=False)
    consent_granted = Column(Boolean, default=True, nullable=False)
    entry_image_object_key = Column(String(1024), nullable=False)
    entry_image_filename = Column(String(512), default="", nullable=False)
    status = Column(String(32), default="registered", nullable=False, index=True)
    segment_count = Column(Integer, default=0, nullable=False)
    best_similarity = Column(Float, default=0.0, nullable=False)
    created_by_username = Column(String(128), ForeignKey("users.username"), nullable=False, index=True)
    checked_out_at = Column(DateTime, nullable=True, index=True)

    project = relationship("Project", back_populates="tourist_sessions")
    created_by = relationship("User")
    segments = relationship("TouristVisitSegment", back_populates="session", cascade="all, delete-orphan")
    vlog_jobs = relationship("ScenicVlogJob", back_populates="session", cascade="all, delete-orphan")
    orders = relationship("ScenicOrder", back_populates="session", cascade="all, delete-orphan")


class TouristVisitSegment(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "tourist_visit_segments"

    session_id = Column(Integer, ForeignKey("tourist_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("detection_targets.id", ondelete="SET NULL"), nullable=True, index=True)
    media_id = Column(Integer, ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True, index=True)
    camera_id = Column(String(128), default="", nullable=False, index=True)
    camera_name = Column(String(128), default="", nullable=False)
    timestamp = Column(String(128), default="", nullable=False, index=True)
    start_seconds = Column(Float, default=0.0, nullable=False)
    end_seconds = Column(Float, default=0.0, nullable=False)
    similarity = Column(Float, default=0.0, nullable=False, index=True)
    thumbnail_object_key = Column(String(1024), default="", nullable=False)
    original_media_object_key = Column(String(1024), default="", nullable=False)
    included = Column(Boolean, default=True, nullable=False, index=True)
    sort_order = Column(Integer, default=0, nullable=False, index=True)

    session = relationship("TouristSession", back_populates="segments")
    target = relationship("DetectionTarget", back_populates="scenic_segments")
    media_file = relationship("MediaFile")
    project = relationship("Project")


class ScenicVlogJob(IntegerIdMixin, TimestampMixin, Base):
    __tablename__ = "scenic_vlog_jobs"

    session_id = Column(Integer, ForeignKey("tourist_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    processing_job_id = Column(String(128), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(32), default="queued", nullable=False, index=True)
    preview_object_key = Column(String(1024), default="", nullable=False)
    full_object_key = Column(String(1024), default="", nullable=False)
    preview_filename = Column(String(255), default="", nullable=False)
    full_filename = Column(String(255), default="", nullable=False)
    segment_count = Column(Integer, default=0, nullable=False)
    duration_seconds = Column(Float, default=0.0, nullable=False)
    error = Column(Text, default="", nullable=False)
    created_by_username = Column(String(128), ForeignKey("users.username"), nullable=False, index=True)

    session = relationship("TouristSession", back_populates="vlog_jobs")
    processing_job = relationship("ProcessingJob")
    created_by = relationship("User")
    orders = relationship("ScenicOrder", back_populates="vlog_job")


class ScenicOrder(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "scenic_orders"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_scenic_orders_amount_positive"),
    )

    session_id = Column(Integer, ForeignKey("tourist_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    vlog_job_id = Column(Integer, ForeignKey("scenic_vlog_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    order_no = Column(String(64), unique=True, nullable=False, index=True)
    amount_cents = Column(Integer, default=1999, nullable=False)
    currency = Column(String(16), default="CNY", nullable=False)
    status = Column(String(32), default="pending", nullable=False, index=True)
    payment_method = Column(String(64), default="manual", nullable=False)
    payment_provider = Column(String(64), default="", nullable=False, index=True)
    provider_trade_no = Column(String(128), default="", nullable=False, index=True)
    provider_trade_state = Column(String(64), default="", nullable=False, index=True)
    payment_code_url = Column(Text, default="", nullable=False)
    payment_qr_object_key = Column(String(1024), default="", nullable=False)
    payment_expires_at = Column(DateTime, nullable=True, index=True)
    payment_error = Column(Text, default="", nullable=False)
    last_payment_sync_at = Column(DateTime, nullable=True, index=True)
    notify_event_id = Column(String(128), default="", nullable=False, index=True)
    delivery_token = Column(String(64), unique=True, nullable=False, index=True)
    paid_at = Column(DateTime, nullable=True, index=True)
    delivered_at = Column(DateTime, nullable=True, index=True)

    session = relationship("TouristSession", back_populates="orders")
    vlog_job = relationship("ScenicVlogJob", back_populates="orders")


class ProjectMember(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "username", name="uq_project_members_project_username"),)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(128), ForeignKey("users.username", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(32), default="viewer", nullable=False, index=True)

    project = relationship("Project", back_populates="members")
    user = relationship("User")


class RefreshToken(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "refresh_tokens"
    jti = Column(String(128), unique=True, nullable=False, index=True)
    username = Column(String(128), ForeignKey("users.username", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)

    user = relationship("User", back_populates="refresh_tokens")


class EmailLoginCode(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "email_login_codes"

    email = Column(String(254), nullable=False, index=True)
    purpose = Column(String(32), default="login", nullable=False, index=True)
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True, index=True)


class ProcessingJob(TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    id = Column(String(128), primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id = Column(Integer, ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True, index=True)
    job_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    progress = Column(Float, default=0.0, nullable=False)
    message = Column(Text, default="", nullable=False)
    error = Column(Text, nullable=True)

    project = relationship("Project", back_populates="processing_jobs")
    media_file = relationship("MediaFile", back_populates="processing_jobs")


class SearchTask(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "search_tasks"
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(128), ForeignKey("users.username"), nullable=False, index=True)
    query_type = Column(String(64), nullable=False, index=True)
    query_content = Column(Text, nullable=False)
    query_object_key = Column(String(1024), nullable=True, index=True)
    filters_json = Column(Text, default="{}", nullable=False)
    top_k = Column(Integer, default=10, nullable=False)

    project = relationship("Project", back_populates="search_tasks")
    user = relationship("User", back_populates="search_tasks")
    results = relationship("SearchResult", back_populates="search_task", cascade="all, delete-orphan")


class SearchResult(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "search_results"
    search_task_id = Column(Integer, ForeignKey("search_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("detection_targets.id", ondelete="SET NULL"), nullable=True, index=True)
    rank = Column(Integer, default=0, nullable=False)
    path = Column(String(1024), nullable=False)
    similarity = Column(Float, nullable=False)
    source_info = Column(Text, default="", nullable=False)

    search_task = relationship("SearchTask", back_populates="results")
    target = relationship("DetectionTarget", back_populates="search_results")


class AuditLog(IntegerIdMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"
    username = Column(String(128), ForeignKey("users.username"), nullable=False, index=True)
    action = Column(String(128), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False, index=True)
    resource_id = Column(String, nullable=True)
    detail = Column(Text, default="", nullable=False)

    user = relationship("User", back_populates="audit_logs")


def init_database():
    initialize_domain_database(
        database_url=SQLALCHEMY_DATABASE_URL,
        engine=engine,
        metadata=Base.metadata,
        scope="core",
        session_factory=SessionLocal,
        role_model=Role,
        user_model=User,
        project_model=Project,
        project_member_model=ProjectMember,
    )


def check_database_connection():
    check_connection(engine)


def get_database_info():
    return database_info(engine, SQLALCHEMY_DATABASE_URL)


def get_db():
    yield from session_dependency(SessionLocal)
