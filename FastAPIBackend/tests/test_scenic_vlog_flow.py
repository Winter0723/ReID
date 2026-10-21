import datetime as dt
from io import BytesIO
from pathlib import Path

import pytest
import torch
from PIL import Image


def _image_bytes(color="blue"):
    image = Image.new("RGB", (96, 160), color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_core_and_scenic_tokens_are_not_interchangeable(client, register_user, register_scenic_user):
    core_user = register_user("shared_user", "core-shared@example.com")
    scenic_user = register_scenic_user("shared_user", "scenic-shared@example.com")

    scenic_with_core_token = client.get("/api/v1/scenic/projects", headers=core_user["headers"])
    assert scenic_with_core_token.status_code == 401

    core_with_scenic_token = client.get("/api/v1/projects", headers=scenic_user["headers"])
    assert core_with_scenic_token.status_code == 401


def test_scenic_preview_plan_skims_video_timeline():
    from app.workers import tasks

    class Segment:
        def __init__(self, camera_id):
            self.camera_id = camera_id

    segments = [Segment("cam-a"), Segment("cam-b"), Segment("cam-c")]
    full_plan = [
        (segments[0], Path("a.mp4"), 0.0, 20.0),
        (segments[1], Path("b.mp4"), 20.0, 20.0),
        (segments[2], Path("c.mp4"), 40.0, 20.0),
    ]

    preview_plan = tasks._build_preview_render_plan(full_plan)
    preview_duration = sum(item[3] for item in preview_plan)

    assert 5.0 <= preview_duration <= 10.0
    assert preview_plan[0][0] is segments[0]
    assert preview_plan[0][2] == 0.0
    assert preview_plan[-1][0] is segments[2]
    assert preview_plan[-1][2] + preview_plan[-1][3] == 60.0


def test_scenic_vlog_business_flow(client, register_scenic_user, monkeypatch, tmp_path):
    from app.api import common as main
    import scenic_database as database
    from app.api.scenic import camera_routes, system_routes as scenic, vlog_routes
    from app.services.vector_store import VectorPoint
    from app.services.vector_store import scenic_vector_store
    from app.workers import tasks

    user = register_scenic_user("scenic_user", "scenic@example.com")
    project_response = client.post(
        "/api/v1/scenic/projects",
        headers=user["headers"],
        json={"name": "Scenic Park", "description": "vlog business"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]

    monkeypatch.setattr(
        scenic,
        "list_camera_devices",
        lambda: [
            {"label": "OBS Virtual Camera", "source": "0", "type": "camera"},
            {"label": "iPhone 模拟相机", "source": "2", "type": "iphone"},
        ],
    )
    discovered = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points/discover",
        headers=user["headers"],
    )
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["created"] == 1
    assert discovered.json()["camera_points"][0]["camera_source"] == "2"
    assert discovered.json()["camera_points"][0]["is_active"] is True

    camera_status = client.get(f"/api/v1/scenic/projects/{project_id}/camera/status", headers=user["headers"])
    assert camera_status.status_code == 200, camera_status.text
    assert camera_status.json()["camera_source"]

    obs_point = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points",
        headers=user["headers"],
        json={"name": "OBS Virtual Camera", "camera_source": "0", "camera_id": "0", "role": "play"},
    )
    assert obs_point.status_code == 200, obs_point.text

    started_sources = []
    started_by_stream = {}
    stopped_stream_keys = []

    def fake_camera_start(**kwargs):
        stream_key = kwargs.get("stream_key") or kwargs["project_id"]
        camera_source = kwargs.get("camera_source", "0")
        started_sources.append(camera_source)
        started_by_stream[stream_key] = camera_source
        return {
            "running": bool(camera_source),
            "project_id": kwargs["project_id"],
            "stream_key": stream_key,
            "media_id": 1,
            "job_id": "camera-job",
            "camera_source": camera_source,
            "captured_frames": 3,
            "indexed_targets": 2,
            "latest_frame_url": "",
            "latest_frame_version": 1,
            "recording_url": "",
            "recording_filename": "",
            "message": "test camera running",
            "error": "",
        }

    def fake_camera_status(project_id, stream_key=None):
        resolved_stream_key = stream_key or project_id
        camera_source = started_by_stream.get(resolved_stream_key, "")
        return {
            "running": bool(camera_source),
            "project_id": project_id,
            "stream_key": resolved_stream_key,
            "media_id": 1,
            "job_id": "camera-job",
            "camera_source": camera_source,
            "captured_frames": 3,
            "indexed_targets": 2 if camera_source else 0,
            "latest_frame_url": "",
            "latest_frame_version": 1,
            "recording_url": "",
            "recording_filename": "",
            "message": "test camera running" if camera_source else "not started",
            "error": "",
        }

    def fake_camera_stop(project_id, wait=True, stream_key=None):
        resolved_stream_key = stream_key or project_id
        stopped_stream_keys.append(resolved_stream_key)
        started_by_stream.pop(resolved_stream_key, None)
        return fake_camera_status(project_id, stream_key)

    monkeypatch.setattr(scenic.camera_stream_manager, "start", fake_camera_start)
    monkeypatch.setattr(scenic.camera_stream_manager, "status", fake_camera_status)
    monkeypatch.setattr(scenic.camera_stream_manager, "stop", fake_camera_stop)
    monkeypatch.setattr(scenic.camera_stream_manager, "is_running", lambda *args, **kwargs: False)
    wall = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points/start-all",
        headers=user["headers"],
        json={"camera_source": "0", "sample_interval_seconds": 1.0, "min_confidence": 0.35},
    )
    assert wall.status_code == 200, wall.text
    assert wall.json()["camera_wall"][0]["running"] is True
    assert wall.json()["camera_wall"][0]["stream_key"]
    assert wall.json()["camera_wall"][0]["camera_source"] == "2"
    assert started_sources == ["2", "0"]

    wall_stream_keys = {row["stream_key"] for row in wall.json()["camera_wall"]}
    stopped = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points/stop-all",
        headers=user["headers"],
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["message"] == "景区监控墙已停止"
    assert all(row["running"] is False for row in stopped.json()["camera_wall"])
    assert wall_stream_keys.issubset(set(stopped_stream_keys))

    point = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points",
        headers=user["headers"],
        json={"name": "湖边栈道", "camera_source": "cam-1", "camera_id": "cam-1", "role": "play"},
    )
    assert point.status_code == 200, point.text

    source_path = tmp_path / "source.jpg"
    crop_path = tmp_path / "crop.jpg"
    Image.new("RGB", (320, 180), "green").save(source_path)
    Image.new("RGB", (96, 160), "red").save(crop_path)
    captured_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)

    db = database.SessionLocal()
    try:
        source_stored = main.storage.save_path(f"scenic/{project_id}", source_path, "source.jpg", category="originals")
        crop_stored = main.storage.save_path(f"scenic/{project_id}", crop_path, "crop.jpg", category="media/1/crops")
        media = database.MediaFile(
            project_id=project_id,
            uploader_username=user["username"],
            filename="source.jpg",
            object_key=source_stored.object_key,
            content_type=source_stored.content_type,
            size_bytes=source_stored.size_bytes,
            sha256=source_stored.sha256,
            media_type="image",
            status="indexed",
        )
        db.add(media)
        db.flush()
        db.add(database.ScenicMediaMetadata(media_id=media.id, recorded_at=captured_at))
        target = database.DetectionTarget(
            project_id=project_id,
            media_id=media.id,
            crop_path=crop_stored.object_key,
            crop_object_key=crop_stored.object_key,
            source_info="湖边栈道 00:00:01 video_seek_seconds=1.000",
            camera_id="cam-1",
            timestamp=captured_at.isoformat(),
            vector_id="scenic-vector-1",
        )
        db.add(target)
        db.commit()
        media_id = media.id
        target_id = target.id
    finally:
        db.close()

    scenic_vector_store.upsert([
        VectorPoint(
            id="scenic-vector-1",
            vector=torch.tensor([1.0, 0.0], dtype=torch.float32),
            path=crop_stored.object_key,
            metadata={
                "target_id": target_id,
                "project_id": project_id,
                "media_id": media_id,
                "camera_id": "cam-1",
                "timestamp": captured_at.isoformat(),
                "source_info": "湖边栈道 00:00:01 video_seek_seconds=1.000",
            },
        )
    ])
    monkeypatch.setattr(scenic, "clip_image_feature", lambda image: torch.tensor([[1.0, 0.0]], dtype=torch.float32))
    monkeypatch.setattr(scenic, "detect_people", lambda *args, **kwargs: [])
    def run_job_now(job_id, task_path, *args):
        module_name, function_name = task_path.rsplit(".", 1)
        assert module_name == "app.workers.tasks"
        getattr(tasks, function_name)(*args)

    def enqueue_job_now(_db, job_id, task_path, *args, **_kwargs):
        run_job_now(job_id, task_path, *args)

    monkeypatch.setattr(camera_routes, "_enqueue_job_or_503", enqueue_job_now)
    monkeypatch.setattr(vlog_routes, "_enqueue_job_or_503", enqueue_job_now)

    tourist = client.post(
        f"/api/v1/scenic/projects/{project_id}/tourists",
        headers=user["headers"],
        data={"consent_granted": "true", "ticket_code": "TICKET-001", "visitor_label": "测试游客", "contact_hint": "1234"},
        files={"file": ("entry.jpg", BytesIO(_image_bytes("yellow")), "image/jpeg")},
    )
    assert tourist.status_code == 200, tourist.text
    session_id = tourist.json()["id"]
    with database.SessionLocal() as db:
        db.get(database.TouristSession, session_id).created_at = captured_at - dt.timedelta(seconds=10)
        db.commit()

    match = client.post(
        f"/api/v1/scenic/tourists/{session_id}/match",
        headers=user["headers"],
        json={"top_k": 20, "min_similarity": 0.1},
    )
    assert match.status_code == 200, match.text
    assert match.json()["tourist"]["segment_count"] == 1
    assert match.json()["segments"][0]["camera_name"] == "湖边栈道"
    segment_id = match.json()["segments"][0]["id"]

    history = client.get("/api/v1/scenic/history", headers=user["headers"])
    assert history.status_code == 200, history.text
    assert any(row["type"] == "scenic" and "TICKET-001" in row["content"] for row in history.json()["history"])
    assert any(row["query_object_url"] for row in history.json()["history"])

    segment_update = client.patch(
        f"/api/v1/scenic/tourists/{session_id}/segments/{segment_id}",
        headers=user["headers"],
        json={"included": False},
    )
    assert segment_update.status_code == 200, segment_update.text
    assert segment_update.json()["included"] is False
    segment_restore = client.patch(
        f"/api/v1/scenic/tourists/{session_id}/segments/{segment_id}",
        headers=user["headers"],
        json={"included": True, "sort_order": 1},
    )
    assert segment_restore.status_code == 200, segment_restore.text
    assert segment_restore.json()["included"] is True
    segment_bulk = client.patch(
        f"/api/v1/scenic/tourists/{session_id}/segments",
        headers=user["headers"],
        json={"segment_ids": [segment_id], "included": True},
    )
    assert segment_bulk.status_code == 200, segment_bulk.text
    assert segment_bulk.json()["updated"] == 1

    vlog_response = client.post(f"/api/v1/scenic/tourists/{session_id}/vlog", headers=user["headers"])
    assert vlog_response.status_code == 200, vlog_response.text

    detail = client.get(f"/api/v1/scenic/tourists/{session_id}", headers=user["headers"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["vlog_jobs"][0]["status"] == "succeeded"
    assert detail.json()["vlog_jobs"][0]["preview_url"]

    order = client.post(
        f"/api/v1/scenic/tourists/{session_id}/orders",
        headers=user["headers"],
        json={"amount_cents": 1, "currency": "USD"},
    )
    assert order.status_code == 200, order.text
    assert order.json()["status"] == "pending"
    assert order.json()["amount_cents"] == 990
    assert order.json()["currency"] == "CNY"

    delivery = client.get(f"/api/v1/scenic/deliveries/{order.json()['delivery_token']}")
    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["paid"] is False
    assert delivery.json()["order"]["preview_url"]
    assert not delivery.json()["order"]["full_url"]
    preview = client.get(delivery.json()["order"]["preview_url"])
    assert preview.status_code == 200, preview.text
    assert preview.headers["cache-control"] == "private, no-store"
    unpaid_full = client.get(
        f"/api/v1/scenic/deliveries/{order.json()['delivery_token']}/full"
    )
    assert unpaid_full.status_code == 402

    manual_paid = client.post(
        f"/api/v1/scenic/orders/{order.json()['id']}/mark-paid",
        headers=user["headers"],
    )
    assert manual_paid.status_code == 200, manual_paid.text
    assert manual_paid.json()["status"] == "paid"
    assert manual_paid.json()["full_url"]
    paid_full = client.get(manual_paid.json()["full_url"])
    assert paid_full.status_code == 200, paid_full.text

    wechat_order = client.post(
        f"/api/v1/scenic/tourists/{session_id}/orders",
        headers=user["headers"],
        json={"amount_cents": 0, "currency": "USD", "payment_method": "wechat_native"},
    )
    assert wechat_order.status_code == 200, wechat_order.text
    assert wechat_order.json()["status"] == "pending"
    assert wechat_order.json()["payment_provider"] == "wechat_native"
    assert wechat_order.json()["amount_cents"] == 990
    assert wechat_order.json()["currency"] == "CNY"
    assert wechat_order.json()["provider_trade_state"] == "NOTPAY"
    assert wechat_order.json()["payment_code_url"].startswith("weixin://")
    assert wechat_order.json()["payment_qr_url"]

    wrong_amount_notify = client.post(
        "/api/v1/scenic/payments/wechat/notify",
        headers={"Wechatpay-Signature": "VALID"},
        json={
            "event_id": "wrong-amount-event",
            "out_trade_no": wechat_order.json()["order_no"],
            "trade_state": "SUCCESS",
            "transaction_id": "WX-WRONG-AMOUNT",
            "amount": {"total": 1, "currency": "CNY"},
        },
    )
    assert wrong_amount_notify.status_code == 400

    missing_amount_notify = client.post(
        "/api/v1/scenic/payments/wechat/notify",
        headers={"Wechatpay-Signature": "VALID"},
        json={
            "event_id": "missing-amount-event",
            "out_trade_no": wechat_order.json()["order_no"],
            "trade_state": "SUCCESS",
            "transaction_id": "WX-MISSING-AMOUNT",
        },
    )
    assert missing_amount_notify.status_code == 400

    failed = client.post(
        f"/api/v1/scenic/orders/{wechat_order.json()['id']}/simulate-payment-result",
        headers=user["headers"],
        json={"result": "failed"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "payment_failed"
    assert "请重试" in failed.json()["payment_error"]

    paid = client.post(
        f"/api/v1/scenic/orders/{wechat_order.json()['id']}/simulate-payment-result",
        headers=user["headers"],
        json={"result": "success"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    assert paid.json()["provider_trade_no"].startswith("SIM-")

    paid_delivery = client.get(f"/api/v1/scenic/deliveries/{paid.json()['delivery_token']}")
    assert paid_delivery.status_code == 200, paid_delivery.text
    assert paid_delivery.json()["paid"] is True
    assert paid_delivery.json()["order"]["full_url"]

    invalid_notify = client.post(
        "/api/v1/scenic/payments/wechat/notify",
        headers={"Wechatpay-Signature": "BAD"},
        json={"out_trade_no": wechat_order.json()["order_no"], "trade_state": "SUCCESS"},
    )
    assert invalid_notify.status_code == 400

    audit = client.get(f"/api/v1/scenic/projects/{project_id}/audit", headers=user["headers"])
    assert audit.status_code == 200, audit.text
    actions = {row["action"] for row in audit.json()["audit"]}
    assert "scenic.tourist_registered" in actions

    import datetime

    db = database.SessionLocal()
    try:
        expired_order = (
            db.query(database.ScenicOrder)
            .filter(database.ScenicOrder.id == paid.json()["id"])
            .first()
        )
        expired_order.paid_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=31)
        db.commit()
    finally:
        db.close()
    expired_delivery = client.get(
        f"/api/v1/scenic/deliveries/{paid.json()['delivery_token']}"
    )
    assert expired_delivery.status_code == 410
    assert "scenic.tourist_matched" in actions
    assert "scenic.order_paid.manual_admin" in actions
    assert "scenic.order_payment_failed.local_simulation" in actions
    assert "scenic.order_paid.local_simulation" in actions


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_scenic_simulated_payment_is_disabled_in_production_like_environments(
    client, register_scenic_user, monkeypatch, app_env
):
    from types import SimpleNamespace

    from app.api.scenic import system_routes as scenic

    user = register_scenic_user("scenic_prod_pay_user", "scenic-prod-pay@example.com")
    monkeypatch.setattr(scenic, "get_settings", lambda: SimpleNamespace(app_env=app_env))
    response = client.post(
        "/api/v1/scenic/tourists/1/orders/simulate-payment",
        headers=user["headers"],
        json={"amount_cents": 1999, "currency": "CNY"},
    )
    assert response.status_code == 403


def test_scenic_simulation_video_indexes_and_matches(client, register_scenic_user, monkeypatch, tmp_path):
    import cv2
    import numpy as np
    import torch

    from app.api import common as main
    import scenic_database as database
    from app.api.scenic import camera_routes, system_routes as scenic, vlog_routes
    from app.services.model_runtime import Detection
    from app.services.vector_store import scenic_vector_store
    from app.workers import tasks

    user = register_scenic_user("scenic_sim_user", "scenic-sim@example.com")
    project_response = client.post(
        "/api/v1/scenic/projects",
        headers=user["headers"],
        json={"name": "Simulation Park", "description": "mock camera uploads"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]

    point_response = client.post(
        f"/api/v1/scenic/projects/{project_id}/camera-points",
        headers=user["headers"],
        json={"name": "入口长廊", "camera_source": "sim-1", "camera_id": "sim-1", "role": "play"},
    )
    assert point_response.status_code == 200, point_response.text
    point_id = point_response.json()["id"]

    video_path = tmp_path / "visitor_walk.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (120, 180))
    assert writer.isOpened()
    for frame_index in range(16):
        frame = np.zeros((180, 120, 3), dtype=np.uint8)
        frame[:] = (20 + frame_index * 8, 80, 40 + frame_index * 5)
        cv2.rectangle(frame, (35, 18), (86, 168), (230 - frame_index * 4, 230, 230), -1)
        writer.write(frame)
    writer.release()
    assert video_path.exists() and video_path.stat().st_size > 0

    detection = Detection(30, 12, 92, 174, confidence=0.95)
    monkeypatch.setattr(tasks, "detect_people", lambda *args, **kwargs: [detection])
    monkeypatch.setattr(scenic, "detect_people", lambda *args, **kwargs: [detection])
    monkeypatch.setattr(tasks, "clip_image_feature", lambda image: torch.tensor([[1.0, 0.0]], dtype=torch.float32))
    monkeypatch.setattr(scenic, "clip_image_feature", lambda image: torch.tensor([[1.0, 0.0]], dtype=torch.float32))

    def run_job_now(job_id, task_path, *args):
        module_name, func_name = task_path.rsplit(".", 1)
        assert module_name == "app.workers.tasks"
        getattr(tasks, func_name)(*args)

    def enqueue_job_now(_db, job_id, task_path, *args, **_kwargs):
        run_job_now(job_id, task_path, *args)

    monkeypatch.setattr(camera_routes, "_enqueue_job_or_503", enqueue_job_now)
    monkeypatch.setattr(vlog_routes, "_enqueue_job_or_503", enqueue_job_now)

    with video_path.open("rb") as file_obj:
        upload = client.post(
            f"/api/v1/scenic/projects/{project_id}/camera-points/{point_id}/simulate-video",
            headers=user["headers"],
            files={"file": ("visitor_walk.mp4", file_obj, "video/mp4")},
        )
    assert upload.status_code == 200, upload.text
    assert upload.json()["status"] == "accepted"

    db = database.SessionLocal()
    try:
        media = db.query(database.MediaFile).filter(database.MediaFile.id == upload.json()["media_id"]).first()
        assert media is not None
        assert media.media_type == "camera"
        assert media.status == "indexed"
        targets = db.query(database.DetectionTarget).filter(database.DetectionTarget.media_id == media.id).all()
        assert targets
        assert {target.camera_id for target in targets} == {"sim-1"}
    finally:
        db.close()

    wall = client.get(f"/api/v1/scenic/projects/{project_id}/camera-wall", headers=user["headers"])
    assert wall.status_code == 200, wall.text
    assert wall.json()["camera_wall"][0]["indexed_targets"] > 0

    status = client.get(f"/api/v1/scenic/projects/{project_id}/status", headers=user["headers"])
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "ready"
    assert status.json()["indexed_targets"] > 0
    assert status.json()["indexed_media"] == 1
    assert status.json()["latest_job"]["message"] == "媒体处理完成"

    assert scenic_vector_store.search(
        torch.tensor([[1.0, 0.0]], dtype=torch.float32),
        top_k=5,
        project_id=project_id,
        filters={"min_similarity": 0.1},
    )

    tourist = client.post(
        f"/api/v1/scenic/projects/{project_id}/tourists",
        headers=user["headers"],
        data={"consent_granted": "true", "ticket_code": "SIM-001", "visitor_label": "模拟游客", "contact_hint": "test"},
        files={"file": ("entry.jpg", BytesIO(_image_bytes("yellow")), "image/jpeg")},
    )
    assert tourist.status_code == 200, tourist.text
    session_id = tourist.json()["id"]

    # The simulated recording belongs to this visit, including its full video
    # duration (indexing an upload does not wait for real-time playback).
    with database.SessionLocal() as db:
        session = db.get(database.TouristSession, session_id)
        recording = db.get(database.MediaFile, upload.json()["media_id"])
        session.created_at = recording.created_at - dt.timedelta(seconds=10)
        session.checked_out_at = recording.created_at + dt.timedelta(seconds=10)
        db.commit()

    match = client.post(
        f"/api/v1/scenic/tourists/{session_id}/match",
        headers=user["headers"],
        json={"top_k": 20, "min_similarity": 0.1, "clip_padding_seconds": 1.0},
    )
    assert match.status_code == 200, match.text
    assert match.json()["tourist"]["segment_count"] >= 1
    assert match.json()["segments"][0]["camera_name"] == "入口长廊"
    assert match.json()["segments"][0]["original_media_url"]

    vlog_response = client.post(f"/api/v1/scenic/tourists/{session_id}/vlog", headers=user["headers"])
    assert vlog_response.status_code == 200, vlog_response.text
    detail = client.get(f"/api/v1/scenic/tourists/{session_id}", headers=user["headers"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["vlog_jobs"][0]["status"] == "succeeded"

    db = database.SessionLocal()
    try:
        vlog = (
            db.query(database.ScenicVlogJob)
            .filter(database.ScenicVlogJob.session_id == session_id)
            .order_by(database.ScenicVlogJob.created_at.desc())
            .first()
        )
        assert vlog is not None
        full_path = main.storage.path_for(vlog.full_object_key)
    finally:
        db.close()
    cap = cv2.VideoCapture(str(full_path))
    sampled_means = []
    try:
        for _ in range(12):
            ok, frame = cap.read()
            if ok:
                sampled_means.append(float(frame.mean()))
    finally:
        cap.release()
    assert len(sampled_means) >= 4
    assert max(sampled_means) - min(sampled_means) > 1.0

    history = client.get("/api/v1/scenic/history", headers=user["headers"])
    assert history.status_code == 200, history.text
    assert any(row["type"] == "scenic" and row["result_count"] > 0 for row in history.json()["history"])

    audit = client.get(f"/api/v1/scenic/projects/{project_id}/audit", headers=user["headers"])
    assert audit.status_code == 200, audit.text
    actions = {row["action"] for row in audit.json()["audit"]}
    assert "scenic.simulation_video_uploaded" in actions
    assert "media.indexed" in actions
    assert "scenic.tourist_matched" in actions
