"""Additive visitor tables; staff accounts and historical sessions remain intact."""
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.orm_mixins import IntegerIdMixin, TimestampMixin


def register_models(Base):
    class VisitorAccount(IntegerIdMixin, TimestampMixin, Base):
        __tablename__ = "visitor_accounts"
        __table_args__ = (UniqueConstraint("appid", "openid", name="uq_visitor_wechat"),)
        appid = Column(String(128), nullable=False)
        openid = Column(String(128), nullable=False)

    class VisitorSession(TimestampMixin, Base):
        __tablename__ = "visitor_auth_sessions"
        token_hash = Column(String(64), primary_key=True)
        account_id = Column(Integer, ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False)
        expires_at = Column(DateTime, nullable=False)

    class VisitorService(TimestampMixin, Base):
        __tablename__ = "visitor_services"
        project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
        enabled = Column(Boolean, default=False, nullable=False)
        description = Column(Text, default="留下游览中的精彩画面，制作属于你的旅行短片。", nullable=False)
        consent_text = Column(Text, default="我同意本景区在本次游览期间使用我的照片及拍摄画面进行人物匹配、视频制作与交付。", nullable=False)
        consent_version = Column(String(32), default="visitor-v1", nullable=False)
        price_cents = Column(Integer, nullable=False)
        project = relationship("Project")

    class VisitorVisit(IntegerIdMixin, TimestampMixin, Base):
        __tablename__ = "visitor_visits"
        __table_args__ = (UniqueConstraint("account_id", "request_key", name="uq_visitor_registration"),)
        account_id = Column(Integer, ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
        session_id = Column(Integer, ForeignKey("tourist_sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
        request_key = Column(String(64), nullable=False)
        consent_text = Column(Text, nullable=False)
        price_cents = Column(Integer, nullable=False)
        stage = Column(String(32), default="visiting", nullable=False, index=True)
        reason = Column(String(64), default="", nullable=False)
        internal_error = Column(Text, default="", nullable=False)
        requested_at = Column(DateTime, nullable=True)
        verified_at = Column(DateTime, nullable=True)
        verified_by = Column(String(128), nullable=False, default="")
        draft_id = Column(Integer, ForeignKey("scenic_edit_drafts.id", ondelete="SET NULL"), nullable=True)
        vlog_id = Column(Integer, ForeignKey("scenic_vlog_jobs.id", ondelete="SET NULL"), nullable=True)
        order_id = Column(Integer, ForeignKey("scenic_orders.id", ondelete="SET NULL"), nullable=True, unique=True)
        session = relationship("TouristSession")
        draft = relationship("ScenicEditDraft")
        vlog = relationship("ScenicVlogJob")
        order = relationship("ScenicOrder")

    return VisitorAccount, VisitorSession, VisitorService, VisitorVisit
