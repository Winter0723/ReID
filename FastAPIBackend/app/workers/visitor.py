"""Run alongside API and RQ: python -m app.workers.visitor.

Database requests are the durable outbox. Restarting this coordinator resumes
the last committed stage; rendering uses the existing RQ queue and snapshots.
"""
import logging
import time
import scenic_database as database
from app.services.visitor_production import ACTIVE_STAGES, production_step
from app.services.job_manager import KeyedTaskPool


def tick(pool, after_id=0):
    with database.SessionLocal() as db:
        query = db.query(database.VisitorVisit.id).filter(database.VisitorVisit.stage.in_(ACTIVE_STAGES))
        ids = [r[0] for r in query.filter(database.VisitorVisit.id > after_id).order_by(database.VisitorVisit.id).limit(100)]
        if not ids:
            ids = [r[0] for r in query.order_by(database.VisitorVisit.id).limit(100)]
    # Bounded keyset pages avoid scanning every active visit on each tick.
    for visit_id in ids:
        if pool.submit(visit_id, production_step, visit_id):
            after_id = visit_id
    return after_id


if __name__ == "__main__":
    database.init_database()
    pool = KeyedTaskPool(4, "visitor-materials")
    cursor = 0
    try:
        while True:
            try:
                cursor = tick(pool, cursor)
            except Exception:
                logging.exception("Visitor coordinator tick failed; retrying")
            time.sleep(1)
    finally:
        pool.shutdown(wait=True)
