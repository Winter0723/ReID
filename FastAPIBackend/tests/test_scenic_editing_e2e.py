"""Real media-to-delivery integration; source identity is a reviewed test fixture."""

import shutil
import subprocess

import pytest

from test_scenic_editing_api import seed, auto


def test_real_dual_render_checkout_payment_and_delivery(client, register_scenic_user, monkeypatch, tmp_path):
    import scenic_database as database
    from app.services.job_manager import job_manager
    from app.services.scenic_render import probe_media
    from app.services.ffmpeg import ffmpeg_executable
    from app.services.storage import storage
    from app.workers.scenic_editing import render_scenic_variant
    if not ffmpeg_executable() or not shutil.which("ffprobe"):
        pytest.skip("Full FFmpeg/FFprobe is required for enhanced render integration")
    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, project, tourist = seed(client, register_scenic_user)
    # Sources are explicitly reviewed fixtures, not a claimed real ReID result.
    video = tmp_path / "tour-motion.mp4"
    subprocess.run([ffmpeg_executable(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "2", "-c:v", "libx264",
                    "-c:a", "aac", str(video)], capture_output=True, check=True)
    stored = storage.save_path(f"scenic/{project}", video, "tour-motion.mp4", category="media")
    with database.SessionLocal() as db:
        media = database.MediaFile(project_id=project, uploader_username=user["username"], filename=stored.filename,
            object_key=stored.object_key, media_type="video", content_type="video/mp4", status="indexed")
        db.add(media)
        db.flush()
        db.add(database.TouristVisitSegment(session_id=tourist, project_id=project, media_id=media.id,
            original_media_object_key=stored.object_key, start_seconds=0, end_seconds=2, included=True, sort_order=5))
        db.commit()
    base = f"/api/v1/scenic/tourists/{tourist}"
    assert client.post(base + "/checkout", headers=user["headers"]).status_code == 200
    draft = auto(client, user, tourist)
    for orientation in ("landscape", "portrait"):
        draft["data"]["variants"][orientation] = draft["data"]["variants"][orientation][:2] + [next(c for c in draft["data"]["sources"] if c["kind"] == "video")]
        # Deduplicate if the selected highlight was the video.
        draft["data"]["variants"][orientation] = list({c["id"]: c for c in draft["data"]["variants"][orientation]}.values())
        for clip in draft["data"]["variants"][orientation]:
            clip["end_seconds"] = clip["start_seconds"] + min(2, clip["end_seconds"] - clip["start_seconds"])
    saved = client.put(base + f"/drafts/{draft['id']}", headers=user["headers"], json={"revision": 1, "data": draft["data"]})
    assert saved.status_code == 200, saved.text
    for source in saved.json()["data"]["sources"]:
        assert client.get(source["thumbnail_url"]).status_code == 200
    submitted = client.post(base + "/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 2})
    assert submitted.status_code == 200, submitted.text
    vlog_id = submitted.json()["vlog"]["id"]
    with database.SessionLocal() as db:
        db.query(database.TouristVisitSegment).filter_by(session_id=tourist).delete()
        db.commit()
        variants = [(v.id, v.processing_job_id) for v in db.get(database.ScenicVlogJob, vlog_id).variants]
    for variant_id, job_id in variants:
        render_scenic_variant(job_id, variant_id, user["username"])
    result = client.get(base + "/vlog", headers=user["headers"]).json()["vlog_jobs"][0]
    assert result["status"] == "succeeded"
    assert len(result["variants"]) == 2
    for output in result["variants"]:
        assert client.get(output["draft_preview_url"]).status_code == 200
        with database.SessionLocal() as db:
            current = db.get(database.ScenicVlogVariant, output["id"])
            probe = probe_media(storage.path_for(current.full_object_key))
        assert probe["video_codec"] == "h264"
        assert any(s["codec_name"] == "aac" for s in probe["streams"])
    paid = client.post(base + "/orders/simulate-payment", headers=user["headers"], json={"payment_method": "simulated", "vlog_job_id": vlog_id})
    assert paid.status_code == 200, paid.text
    order = paid.json()
    for variant in order["variants"]:
        assert client.get(variant["full_url"]).status_code == 200
    page = client.get(order["delivery_page_url"])
    assert "横版游览纪念片" in page.text and "竖版分享短片" in page.text
    assert client.get(order["delivery_qr_url"]).status_code == 200
