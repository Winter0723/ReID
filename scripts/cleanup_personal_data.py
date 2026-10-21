"""Preview or apply visitor retention. Run with API/coordinator/workers stopped.

Default is a dry run. Objects are deleted before rows: a storage failure leaves
database references intact for retry. Backups require a separate retention policy.
"""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FastAPIBackend"))


def expired_session(session, cutoff, now, ttl_hours):
    from app.services.time_utils import as_utc
    if as_utc(session.created_at) >= cutoff:
        return False
    if any(v.status in {"queued", "running"} for v in session.vlog_jobs):
        return False
    if any(child.status in {"queued", "running"} for v in session.vlog_jobs for child in v.variants):
        return False
    return not any(as_utc(o.paid_at or o.created_at) + timedelta(hours=ttl_hours) > now for o in session.orders)


def session_objects(session):
    keys = {session.entry_image_object_key}
    for vlog in session.vlog_jobs:
        keys.update((vlog.preview_object_key, vlog.full_object_key))
        for variant in vlog.variants:
            keys.update(getattr(variant, kind + "_object_key") for kind in ("full", "preview", "draft_preview", "cover"))
    # Source videos and matching thumbnails belong to project media, not visitors.
    return sorted(filter(None, keys))


def cleanup(*, apply=False):
    import database
    import scenic_database as scenic
    from app.core.config import get_settings
    from app.services.storage import storage
    from app.services.time_utils import utc_now
    settings, now = get_settings(), utc_now()
    cutoff = now - timedelta(days=settings.visitor_retention_days)
    report = {"apply": apply, "sessions": [], "auth_rows": 0}
    with scenic.SessionLocal() as db:
        cursor = 0
        while True:
            sessions = db.query(scenic.TouristSession).filter(
                scenic.TouristSession.id > cursor, scenic.TouristSession.created_at < cutoff,
            ).order_by(scenic.TouristSession.id).limit(100).all()
            if not sessions:
                break
            cursor = sessions[-1].id
            for session in sessions:
                if not expired_session(session, cutoff, now, settings.scenic_delivery_token_ttl_hours):
                    continue
                keys = session_objects(session)
                report["sessions"].append({"id": session.id, "objects": len(keys)})
                if apply:
                    for key in keys:
                        storage.delete(key)
                    db.query(scenic.VisitorVisit).filter_by(session_id=session.id).delete(synchronize_session=False)
                    db.delete(session)
                    db.commit()
        expired = db.query(scenic.VisitorSession).filter(scenic.VisitorSession.expires_at < now)
        report["auth_rows"] += expired.count()
        if apply:
            expired.delete(synchronize_session=False)
            # No active session or visit remains: discard OAuth identity as well.
            db.query(scenic.VisitorAccount).filter(
                ~scenic.VisitorAccount.id.in_(db.query(scenic.VisitorVisit.account_id)),
                ~scenic.VisitorAccount.id.in_(db.query(scenic.VisitorSession.account_id)),
                scenic.VisitorAccount.created_at < cutoff,
            ).delete(synchronize_session=False)
            db.commit()
    for module in (database, scenic):
        with module.SessionLocal() as db:
            for model in (module.EmailLoginCode, module.RefreshToken):
                rows = db.query(model).filter(model.expires_at < now)
                report["auth_rows"] += rows.count()
                if apply:
                    rows.delete(synchronize_session=False)
            if apply:
                db.commit()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true", help="Confirm maintenance window with all writers stopped")
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error("--apply requires --writers-stopped")
    print(json.dumps(cleanup(apply=args.apply), ensure_ascii=False, indent=2))
