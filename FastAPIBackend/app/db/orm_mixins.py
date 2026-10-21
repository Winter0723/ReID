from sqlalchemy import Column, DateTime, Integer

from app.services.time_utils import utc_now


class IntegerIdMixin:
    id = Column(Integer, primary_key=True, index=True)


class CreatedAtMixin:
    created_at = Column(DateTime, default=utc_now, nullable=False)


class UpdatedAtMixin:
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class TimestampMixin(CreatedAtMixin, UpdatedAtMixin):
    pass
