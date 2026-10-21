"""Operational lists must remain complete, project-scoped and version-aware."""

import datetime as dt

import pytest
from sqlalchemy import event

NOW = dt.datetime(2026, 9, 14, 4, 0)


def project(client, user, name="运营验收"):
    response = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def tourist(db, user, project_id, ticket, **kwargs):
    import scenic_database as database
    row = database.TouristSession(project_id=project_id, ticket_code=ticket,
        entry_image_object_key="entry.jpg", created_by_username=user["username"], **kwargs)
    db.add(row)
    db.flush()
    return row


def vlog(db, user, visitor, status="succeeded", **kwargs):
    import scenic_database as database
    row = database.ScenicVlogJob(session_id=visitor.id, created_by_username=user["username"],
        status=status, full_object_key="full.mp4" if status == "succeeded" else "",
        preview_object_key="preview.mp4" if status == "succeeded" else "", **kwargs)
    db.add(row)
    db.flush()
    return row


def order(db, visitor, video, token, status="paid", **kwargs):
    import scenic_database as database
    row = database.ScenicOrder(session_id=visitor.id, vlog_job_id=video.id, order_no=token,
        delivery_token=token, status=status, amount_cents=1999, **kwargs)
    db.add(row)
    db.flush()
    return row


def test_tourists_search_all_pages_literal_text_and_utc_dates(client, register_scenic_user):
    import scenic_database as database
    user = register_scenic_user()
    pid = project(client, user)
    other = project(client, user, "另一景区")
    with database.SessionLocal() as db:
        for index in range(105):
            tourist(db, user, pid, f"T-{index:03}", created_at=NOW, status="registered")
        special = tourist(db, user, pid, "EARLY_10%", visitor_label="纪念游客", contact_hint="13800138000",
                          created_at=NOW - dt.timedelta(days=1), status="checked_out")
        special_id = special.id
        tourist(db, user, other, "EARLY_10%", created_at=NOW)
        db.commit()
    url = f"/api/v1/scenic/projects/{pid}/tourists"
    first = client.get(url, headers=user["headers"]).json()
    assert len(first["tourists"]) == 100
    assert first["pagination"]["total"] == 106
    second = client.get(url, headers=user["headers"], params={"page": 2}).json()
    assert len(second["tourists"]) == 6
    assert not {row["id"] for row in first["tourists"]} & {row["id"] for row in second["tourists"]}
    for q in ("EARLY_", "%", "纪念", "13800138000"):
        result = client.get(url, headers=user["headers"], params={"q": q}).json()
        assert [row["id"] for row in result["tourists"]] == [special_id]
    selected = client.get(url, headers=user["headers"], params={
        "date_from": "2026-09-13T12:00:00+08:00", "date_to": "2026-09-14T12:00:00+08:00",
        "status": "checked_out"})
    assert selected.status_code == 200, selected.text
    assert selected.json()["pagination"]["total"] == 1
    assert client.get(url, headers=user["headers"], params={"date_from": "2026-09-15", "date_to": "2026-09-14"}).status_code == 422
    assert client.get(url, headers=user["headers"], params={"page_size": 101}).status_code == 422
    assert client.get(url, headers=user["headers"], params={"work_queue": "unknown"}).status_code == 422


def test_work_queues_use_version_facts_and_keep_paid_old_version_visible(client, register_scenic_user):
    import scenic_database as database
    from app.services.time_utils import utc_now
    from app.core.config import get_settings
    user = register_scenic_user()
    pid = project(client, user)
    with database.SessionLocal() as db:
        review = tourist(db, user, pid, "review")
        db.add(database.TouristVisitSegment(session_id=review.id, project_id=pid, included=True))
        draft = tourist(db, user, pid, "draft")
        db.add(database.ScenicEditDraft(session_id=draft.id))
        failed = tourist(db, user, pid, "failed", status="matched")
        vlog(db, user, failed, "failed", created_at=NOW)
        partial = tourist(db, user, pid, "partial")
        vlog(db, user, partial, "partial", created_at=NOW)
        awaiting = tourist(db, user, pid, "awaiting")
        vlog(db, user, awaiting)
        paid = tourist(db, user, pid, "paid-old-and-new-failed")
        old = vlog(db, user, paid, created_at=NOW)
        order(db, paid, old, "old-paid")
        vlog(db, user, paid, "failed", created_at=NOW + dt.timedelta(seconds=1))
        expired = tourist(db, user, pid, "expired")
        old = vlog(db, user, expired)
        order(db, expired, old, "expired-paid", created_at=utc_now() - dt.timedelta(hours=get_settings().scenic_delivery_token_ttl_hours + 1))
        claimed = tourist(db, user, pid, "claimed")
        old = vlog(db, user, claimed)
        order(db, claimed, old, "claimed", claimed_at=utc_now())
        for index in range(12):
            tourist(db, user, pid, f"idle-{index}")
        db.commit()
    url = f"/api/v1/scenic/projects/{pid}/tourists"
    statements = []
    def collect(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(database.engine, "before_cursor_execute", collect)
    try:
        response = client.get(url, headers=user["headers"])
    finally:
        event.remove(database.engine, "before_cursor_execute", collect)
    assert response.status_code == 200, response.text
    assert len(statements) <= 7  # independent of row count; no per-tourist order scans
    counts = response.json()["work_queue_counts"]
    assert counts == {"needs_review": 1, "needs_vlog": 1, "failed": 3, "awaiting_payment": 1, "ready_delivery": 1}
    paid_row = next(row for row in response.json()["tourists"] if row["ticket_code"] == "paid-old-and-new-failed")
    assert set(paid_row["work_queues"]) == {"failed", "ready_delivery"}
    for name, count in counts.items():
        filtered = client.get(url, headers=user["headers"], params={"work_queue": name, "page_size": 1}).json()
        assert filtered["pagination"]["total"] == count
        assert filtered["work_queue_counts"] == counts
        assert name in filtered["tourists"][0]["work_queues"]


def test_history_audit_jobs_filter_before_pagination_and_keep_project_scope(client, register_scenic_user):
    import scenic_database as database
    user = register_scenic_user()
    pid = project(client, user)
    other = project(client, user, "其他景区")
    with database.SessionLocal() as db:
        for project_id in (pid, other):
            for index in range(5):
                db.add(database.SearchTask(project_id=project_id, username=user["username"], query_type="scenic",
                    query_content=f"游客 TARGET% {index}", created_at=NOW + dt.timedelta(hours=index)))
                db.add(database.AuditLog(username=user["username"], action="scenic.target", resource_type="project",
                    resource_id=str(project_id), detail=f"TARGET% {index}", created_at=NOW + dt.timedelta(hours=index)))
                db.add(database.ProcessingJob(id=f"job-{project_id}-{index}", project_id=project_id,
                    job_type="scenic_vlog", status="failed", created_at=NOW + dt.timedelta(hours=index)))
        db.commit()
    filters = {"page": 2, "page_size": 1, "date_from": "2026-09-14T04:00:00Z", "date_to": "2026-09-14T07:00:00Z"}
    for url, extra, field in (
        ("/api/v1/scenic/history", {"project_id": pid, "q": "%", "query_type": "scenic"}, "history"),
        (f"/api/v1/scenic/projects/{pid}/audit", {"q": "TARGET%", "action": "scenic.target"}, "audit"),
        (f"/api/v1/scenic/projects/{pid}/jobs", {"status": "failed", "job_type": "scenic_vlog"}, "jobs"),
    ):
        response = client.get(url, headers=user["headers"], params=filters | extra)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["pagination"]["total"] == 3
        assert len(payload[field]) == 1
        assert payload[field][0]["created_at"] == "2026-09-14 05:00:00"
        assert client.get(url, headers=user["headers"], params=extra | {"date_from": "2026-09-15", "date_to": "2026-09-14"}).status_code == 422
    outsider = register_scenic_user("outsider")
    for url in (f"/api/v1/scenic/projects/{pid}/tourists", f"/api/v1/scenic/projects/{pid}/audit", f"/api/v1/scenic/history?project_id={pid}"):
        assert client.get(url, headers=outsider["headers"]).status_code == 403


def test_receipt_is_explicit_idempotent_and_does_not_change_paid_delivery_contract(client, register_scenic_user):
    import scenic_database as database
    from app.services.time_utils import utc_now
    user = register_scenic_user()
    pid = project(client, user)
    with database.SessionLocal() as db:
        visitor = tourist(db, user, pid, "receipt")
        video = vlog(db, user, visitor)
        row = order(db, visitor, video, "receipt-token", paid_at=utc_now(), delivered_at=utc_now())
        order_id, video_id = row.id, video.id
        db.commit()
    base = "/api/v1/scenic/deliveries/receipt-token"
    page = client.get(base + "/page")
    assert page.status_code == 200
    assert "保存视频后" in page.text and "保存两版视频后" not in page.text
    assert 'method="post"' in page.text and "我已保存，确认领取" in page.text
    assert "form-action 'self'" in page.headers["content-security-policy"]
    assert client.get(base).json()["order"]["claimed_at"] == ""
    response = client.post(base + "/confirm-receipt", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == base + "/page"
    data = client.get(base).json()["order"]
    assert data["claimed_at"] and data["paid_at"] and data["delivered_at"]
    assert data["vlog_job_id"] == video_id
    assert client.post(base + "/confirm-receipt", follow_redirects=False).status_code == 303
    assert client.get(base).json()["order"]["claimed_at"] == data["claimed_at"]
    assert "已确认领取" in client.get(base + "/page").text
    with database.SessionLocal() as db:
        assert db.query(database.AuditLog).filter_by(action="scenic.order_receipt_confirmed").count() == 1
        assert db.get(database.ScenicOrder, order_id).status == "paid"
    queues = client.get(f"/api/v1/scenic/projects/{pid}/tourists", headers=user["headers"]).json()
    assert queues["work_queue_counts"]["ready_delivery"] == 0


@pytest.mark.parametrize("kind,expected", [("unpaid", 402), ("expired", 410), ("failed", 409), ("unknown", 404)])
def test_receipt_rejects_unavailable_orders(client, register_scenic_user, kind, expected):
    import scenic_database as database
    from app.core.config import get_settings
    from app.services.time_utils import utc_now
    user = register_scenic_user()
    pid = project(client, user)
    with database.SessionLocal() as db:
        visitor = tourist(db, user, pid, "restricted")
        video = vlog(db, user, visitor, "failed" if kind == "failed" else "succeeded")
        order(db, visitor, video, "restricted", "pending" if kind == "unpaid" else "paid",
              created_at=utc_now() - dt.timedelta(hours=get_settings().scenic_delivery_token_ttl_hours + 1) if kind == "expired" else utc_now())
        db.commit()
    token = "missing" if kind == "unknown" else "restricted"
    response = client.post(f"/api/v1/scenic/deliveries/{token}/confirm-receipt", follow_redirects=False)
    assert response.status_code == expected, response.text
    with database.SessionLocal() as db:
        assert db.query(database.ScenicOrder).first().claimed_at is None


@pytest.mark.parametrize("consent", [None, "false"])
def test_registration_requires_explicit_consent_before_storing_media(client, register_scenic_user, consent):
    import scenic_database as database
    user = register_scenic_user()
    pid = project(client, user)
    data = {"ticket_code": "NO-CONSENT"}
    if consent is not None:
        data["consent_granted"] = consent
    response = client.post(f"/api/v1/scenic/projects/{pid}/tourists", headers=user["headers"], data=data,
                           files={"file": ("entry.jpg", b"not-an-image", "image/jpeg")})
    assert response.status_code == 400
    assert "授权" in response.json()["detail"]
    with database.SessionLocal() as db:
        assert db.query(database.TouristSession).count() == 0



def test_pending_vlog_tracks_latest_saved_revision_after_older_delivery(client, register_scenic_user):
    import scenic_database as database
    user = register_scenic_user()
    pid = project(client, user)
    with database.SessionLocal() as db:
        visitor = tourist(db, user, pid, "revision")
        draft = database.ScenicEditDraft(session_id=visitor.id, revision=1, updated_at=NOW)
        db.add(draft)
        db.flush()
        vlog(db, user, visitor, draft_id=draft.id, draft_revision=1)
        draft_id = draft.id
        db.commit()
    url = f"/api/v1/scenic/projects/{pid}/tourists?work_queue=needs_vlog"
    assert client.get(url, headers=user["headers"]).json()["pagination"]["total"] == 0
    with database.SessionLocal() as db:
        db.get(database.ScenicEditDraft, draft_id).revision = 2
        db.commit()
    assert client.get(url, headers=user["headers"]).json()["pagination"]["total"] == 1
    with database.SessionLocal() as db:
        visitor = db.query(database.TouristSession).filter_by(ticket_code="revision").one()
        vlog(db, user, visitor, "running", draft_id=draft_id, draft_revision=2)
        db.commit()
    assert client.get(url, headers=user["headers"]).json()["pagination"]["total"] == 0



def test_historical_paid_version_receipt_survives_newer_failed_version(client, register_scenic_user):
    import scenic_database as database
    user = register_scenic_user()
    pid = project(client, user)
    with database.SessionLocal() as db:
        visitor = tourist(db, user, pid, "fixed-version")
        first = vlog(db, user, visitor, created_at=NOW)
        first_id = first.id
        order(db, visitor, first, "old-paid-version")
        vlog(db, user, visitor, "failed", created_at=NOW + dt.timedelta(seconds=1))
        db.commit()
    base = "/api/v1/scenic/deliveries/old-paid-version"
    assert client.post(base + "/confirm-receipt", follow_redirects=False).status_code == 303
    paid = client.get(base).json()["order"]
    assert paid["vlog_job_id"] == first_id and paid["claimed_at"]
    queues = client.get(f"/api/v1/scenic/projects/{pid}/tourists", headers=user["headers"]).json()
    assert queues["tourists"][0]["work_queues"] == ["failed"]


def test_pending_vlog_uses_saved_time_when_older_draft_is_reopened(client, register_scenic_user):
    import scenic_database as database
    user = register_scenic_user()
    pid = project(client, user)
    with database.SessionLocal() as db:
        visitor = tourist(db, user, pid, "reopened")
        old = database.ScenicEditDraft(session_id=visitor.id, revision=1, created_at=NOW, updated_at=NOW)
        newer = database.ScenicEditDraft(session_id=visitor.id, revision=1,
            created_at=NOW + dt.timedelta(seconds=1), updated_at=NOW + dt.timedelta(seconds=1))
        db.add_all([old, newer])
        db.flush()
        vlog(db, user, visitor, draft_id=newer.id, draft_revision=1)
        old_id = old.id
        db.commit()
    url = f"/api/v1/scenic/projects/{pid}/tourists?work_queue=needs_vlog"
    assert client.get(url, headers=user["headers"]).json()["pagination"]["total"] == 0
    with database.SessionLocal() as db:
        old = db.get(database.ScenicEditDraft, old_id)
        old.revision = 2
        old.updated_at = NOW + dt.timedelta(seconds=2)
        db.commit()
    assert client.get(url, headers=user["headers"]).json()["pagination"]["total"] == 1
