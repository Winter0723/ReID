"""Project-scoped work queues derived from saved drafts, versions and orders."""

import datetime

from fastapi import HTTPException
from sqlalchemy import and_, case, func, select

import scenic_database as database
from app.core.config import get_settings
from app.services.time_utils import as_utc, utc_now


def filter_created_at(query, column, date_from=None, date_to=None):
    start, end = as_utc(date_from), as_utc(date_to)
    if start is not None and end is not None and start >= end:
        raise HTTPException(422, "开始时间必须早于结束时间")
    # Scenic DateTime columns persist UTC without a timezone. Normalize before
    # binding so SQLite and PostgreSQL implement the same half-open interval.
    if start is not None:
        query = query.filter(column >= start.replace(tzinfo=None))
    if end is not None:
        query = query.filter(column < end.replace(tzinfo=None))
    return query


def work_queue_expressions():
    tourist = database.TouristSession
    vlog = database.ScenicVlogJob
    order = database.ScenicOrder
    latest = (select(vlog.id).where(vlog.session_id == tourist.id)
              .order_by(vlog.created_at.desc(), vlog.id.desc()).limit(1)
              .correlate(tourist).scalar_subquery())
    latest_status = (select(vlog.status).where(vlog.id == latest)
                     .correlate(tourist).scalar_subquery())
    has_draft = (select(database.ScenicEditDraft.id)
                 .where(database.ScenicEditDraft.session_id == tourist.id)
                 .correlate(tourist).exists())
    draft = database.ScenicEditDraft
    latest_draft = (select(draft.id).where(draft.session_id == tourist.id)
                    .order_by(draft.updated_at.desc(), draft.id.desc()).limit(1)
                    .correlate(tourist).scalar_subquery())
    submitted_revision = (select(vlog.id).where(vlog.session_id == tourist.id,
                          vlog.draft_id == draft.id, vlog.draft_revision == draft.revision)
                          .correlate(tourist, draft).exists())
    pending_draft = (select(draft.id).where(draft.id == latest_draft, ~submitted_revision)
                     .correlate(tourist).exists())
    has_segments = (select(database.TouristVisitSegment.id)
                    .where(database.TouristVisitSegment.session_id == tourist.id,
                           database.TouristVisitSegment.included.is_(True))
                    .correlate(tourist).exists())
    paid_latest = (select(order.id).where(order.session_id == tourist.id,
                   order.vlog_job_id == latest, order.status == "paid")
                   .correlate(tourist).exists())
    valid_since = (utc_now() - datetime.timedelta(
        hours=get_settings().scenic_delivery_token_ttl_hours)).replace(tzinfo=None)
    awaiting_receipt = (select(order.id).join(vlog, vlog.id == order.vlog_job_id)
        .where(order.session_id == tourist.id, order.status == "paid",
               order.claimed_at.is_(None), func.coalesce(order.paid_at, order.created_at) > valid_since,
               vlog.status == "succeeded", vlog.full_object_key != "")
        .correlate(tourist).exists())
    return {
        "needs_review": and_(latest.is_(None), ~has_draft, has_segments),
        "needs_vlog": pending_draft,
        "failed": latest_status.in_(("failed", "partial")),
        "awaiting_payment": and_(latest_status == "succeeded", ~paid_latest),
        "ready_delivery": awaiting_receipt,
    }


def work_queue_counts(db, project_id, expressions=None):
    expressions = expressions or work_queue_expressions()
    row = (db.query(*[func.coalesce(func.sum(case((predicate, 1), else_=0)), 0)
                      for predicate in expressions.values()])
           .filter(database.TouristSession.project_id == project_id).one())
    return {name: int(value) for name, value in zip(expressions, row)}
