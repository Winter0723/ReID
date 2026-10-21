"""Exercise visitor HTTP guards against PostgreSQL without persisting fixtures.

Run with the backend environment (or pipe into `docker exec -i ... python -`).
An outer transaction contains all fixture/request writes; route commits release
savepoints only. No migrations, database resets, workers or model loading run.
PostgreSQL sequences may advance, as with any rolled-back insert.
"""
from datetime import timedelta
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FastAPIBackend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import scenic_database as database
from app.api.scenic.visitor_routes import router
from app.core.config import get_settings
from app.services.time_utils import utc_now
from app.services.visitor_auth import COOKIE, PREFIX, digest


def main():
    settings = get_settings()
    if database.engine.dialect.name != "postgresql":
        raise RuntimeError("This check requires PostgreSQL")
    if settings.app_env.lower() not in {"local", "dev", "development", "test"}:
        raise RuntimeError("Run this regression check in a local/test environment")
    app = FastAPI()
    app.include_router(router)
    tag = "visitor-pg-check-" + secrets.token_hex(8)
    visit_id = None
    with database.engine.connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            db.add(database.User(username=tag, hashed_password="not-a-login", role="operator"))
            db.flush()
            project = database.Project(name=tag, owner_username=tag)
            db.add(project)
            db.flush()
            account = database.VisitorAccount(appid="postgres-regression", openid=tag)
            other = database.VisitorAccount(appid="postgres-regression", openid=tag + "-other")
            db.add_all([account, other])
            db.flush()
            tour = database.TouristSession(project_id=project.id, ticket_code=tag,
                entry_image_object_key="regression/no-photo.jpg", created_by_username=tag)
            db.add(tour)
            db.flush()
            visit = database.VisitorVisit(account_id=account.id, session_id=tour.id,
                request_key=tag, consent_text="regression fixture", price_cents=1990,
                verified_at=utc_now())
            db.add(visit)
            tokens = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
            for owner, token in zip((account, other), tokens):
                db.add(database.VisitorSession(token_hash=digest(token), account_id=owner.id,
                    expires_at=utc_now() + timedelta(minutes=10)))
            db.commit()
            visit_id = visit.id

            def test_db():
                yield db

            app.dependency_overrides[database.get_db] = test_db
            client = TestClient(app)
            client.cookies.set(COOKIE, tokens[0])
            headers = {"X-Visitor-Request": "1"}
            url = f"{PREFIX}/visits/{visit_id}"
            assert client.post(url + "/finish").status_code == 403
            first = client.post(url + "/finish", headers=headers)
            assert first.status_code == 200, first.text
            assert first.json()["stage"] == "sealing"
            second = client.post(url + "/finish", headers=headers)
            assert second.status_code == 200, second.text
            assert first.json()["checked_out_at"] == second.json()["checked_out_at"]
            assert client.get(url).json()["stage"] == "sealing"
            for action in ("retry", "pay", "payment-status", "claim", "render", "draft"):
                result = client.post(url + "/" + action, headers=headers,
                    json={"revision": 1, "segment_ids": [1], "match_revision": "stale"})
                expected = 200 if action == "payment-status" else 403 if action == "claim" else 409
                assert result.status_code == expected, (action, result.status_code, result.text)
                print(f"PASS {action}: expected HTTP {result.status_code}")
            assert client.get(url + "/segments/999999/preview").status_code == 404
            assert client.post(PREFIX + "/visits/not-an-integer/finish", headers=headers).status_code == 422
            client.cookies.clear()
            client.cookies.set(COOKIE, tokens[1])
            assert client.post(url + "/finish", headers=headers).status_code == 404
            assert client.get(url).status_code == 404
            client.cookies.clear()
            assert client.post(url + "/finish", headers=headers).status_code == 401
            print("PASS PostgreSQL finish, repeat finish, reload, preview, invalid ID, CSRF and ownership")
        finally:
            app.dependency_overrides.clear()
            db.close()
            transaction.rollback()
    with database.SessionLocal() as check:
        assert check.query(database.User).filter_by(username=tag).first() is None
        if visit_id is not None:
            assert check.get(database.VisitorVisit, visit_id) is None
    print("PASS all regression fixtures rolled back")


if __name__ == "__main__":
    main()
