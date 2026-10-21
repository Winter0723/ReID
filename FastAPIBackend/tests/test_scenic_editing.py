"""Real media and database coverage of preflight and immutable story plans."""

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scenic_database as database
from app.services import scenic_editing as editing
from app.services import scenic_templates, scenic_tracking


@pytest.fixture(autouse=True)
def reset_backend_state(monkeypatch, tmp_path):
    # Use actual ORM rows and media, without loading models, a live database or RQ.
    settings = SimpleNamespace(object_storage_dir=tmp_path / "cache", scenic_tracking_enabled=False)
    monkeypatch.setattr(editing, "get_settings", lambda: settings)
    monkeypatch.setattr(scenic_templates, "get_settings", lambda: settings)
    monkeypatch.setattr(scenic_tracking, "get_settings", lambda: settings)
    def save_path(project, source, filename, category):
        # Match the real store's bounded content-hash naming rather than copying
        # the full analysis-cache stem into a nested path (Windows MAX_PATH).
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        key = f"projects/{project}/{category}/{digest}_{filename}"
        destination = tmp_path / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return SimpleNamespace(object_key=key)

    monkeypatch.setattr(editing, "storage", SimpleNamespace(path_for=lambda key: tmp_path / key, save_path=save_path))
    monkeypatch.setattr(scenic_tracking, "storage", SimpleNamespace(path_for=lambda key: tmp_path / key))
    (tmp_path / "yolo").mkdir()
    monkeypatch.setenv("YOLO_CONFIG_DIR", str(tmp_path / "yolo"))
    monkeypatch.setenv("YOLO_AUTOINSTALL", "false")


@pytest.fixture
def scene(tmp_path):
    engine = create_engine("sqlite://")
    database.Base.metadata.create_all(engine)
    db = Session(engine)
    user = database.User(username="editor", hashed_password="unused", role="operator")
    project = database.Project(name="湖山公园", owner=user, project_type="scenic")
    visitor = database.TouristSession(project=project, ticket_code="test", created_by=user,
                                     visitor_label="旅人", entry_image_object_key="entry.png")
    db.add_all([user, project, visitor])
    db.flush()
    Image.new("RGB", (80, 160), "green").save(tmp_path / "entry.png")

    def add(name, kind="video", *, start=0, end=3, order=1, timestamp="", location="", recorded_at=None, quality_color="steelblue"):
        path = tmp_path / name
        if not path.exists():
            if kind == "image":
                image = Image.new("RGB", (160, 100), quality_color)
                ImageDraw.Draw(image).rectangle((30, 10, 50, 90), fill="gold")
                image.save(path)
            else:
                writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 100))
                assert writer.isOpened()
                for frame_id in range(40):
                    frame = np.full((100, 160, 3), (70 + order * 3, 105, 130), dtype=np.uint8)
                    cv2.rectangle(frame, (15 + frame_id, 10), (40 + frame_id, 90), (240, 190, 40), -1)
                    writer.write(frame)
                writer.release()
        media = database.MediaFile(project=project, uploader=user, filename=name, object_key=name,
                                   media_type=kind, content_type="image/png" if kind == "image" else "video/x-msvideo", status="completed")
        if kind == "camera":
            media.created_at = dt.datetime(2026, 9, 7, 1, 0)
        db.add(media)
        db.flush()
        segment = database.TouristVisitSegment(session=visitor, project=project, media_file=media,
                   original_media_object_key=name, start_seconds=start, end_seconds=end, sort_order=order,
                   camera_name=location, camera_id=str(order), timestamp=timestamp, similarity=0.99,
                   thumbnail_object_key="entry.png", included=True)
        db.add(segment)
        if location or recorded_at:
            db.add(database.ScenicMediaMetadata(media_id=media.id, recorded_at=recorded_at, location=location))
        db.flush()
        return segment, media, path

    yield db, visitor, add
    db.close()
    engine.dispose()


def test_preflight_actual_video_and_image_cache_detects_changed_bytes(tmp_path, monkeypatch):
    image = tmp_path / "照片.png"
    Image.new("RGB", (72, 120), "navy").save(image)
    original = editing.analyze_media(image)
    assert original["kind"] == "image" and original["width"] == 72
    assert original["height"] == 120 and not original["has_audio"]
    # A cache read does not have to decode pixels again.
    with monkeypatch.context() as patch:
        patch.setattr(editing.Image, "open", lambda *args: pytest.fail("cache hit decoded image"))
        assert editing.analyze_media(image) == original
    Image.new("RGB", (72, 120), "gold").save(image)
    with pytest.raises(editing.EditingError, match="内容已改变"):
        editing.analyze_media(image, expected_sha256=original["source_sha256"])
    assert editing.analyze_media(image)["source_sha256"] != original["source_sha256"]


def test_real_ffprobe_audio_rotation_and_video_duration(tmp_path):
    executable = editing.ffmpeg_executable()
    if not executable or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")
    original, rotated = tmp_path / "原音频.mp4", tmp_path / "旋转.mp4"
    subprocess.run([executable, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x100:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1.25", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(original)], capture_output=True, check=True)
    subprocess.run([executable, "-v", "error", "-y", "-display_rotation:v:0", "90", "-i", str(original), "-c", "copy", str(rotated)], capture_output=True, check=True)
    report = editing.analyze_media(rotated)
    assert report["has_audio"]
    assert report["duration_seconds"] == pytest.approx(1.25, abs=0.1)
    assert report["fps"] == 24
    assert abs(report["rotation"]) == 90
    assert (report["width"], report["height"]) == (100, 160)


def test_mixed_draft_clamps_merges_and_keeps_original_segments_unchanged(scene):
    db, visitor, add = scene
    video, media, path = add("camera.avi", "camera", start=0, end=3, order=1)
    overlap = database.TouristVisitSegment(session=visitor, project=visitor.project, media_file=media,
               original_media_object_key=path.name, start_seconds=2, end_seconds=1000, sort_order=2, included=True)
    db.add(overlap)
    photo, _, _ = add("photo.png", "image", order=3, location="湖畔", recorded_at=dt.datetime(2026, 9, 7, 2, 0))
    db.flush()
    draft = editing.build_draft(visitor, db, {})
    assert len(draft["sources"]) == 2
    video_clip = next(clip for clip in draft["sources"] if clip["kind"] == "video")
    assert video_clip["end_seconds"] == pytest.approx(4, abs=.1)
    assert set(video_clip["segment_ids"]) == {video.id, overlap.id}
    assert overlap.end_seconds == 1000  # planner never mutates search/selection rows
    assert draft["music_id"] == "oga_revelation"
    assert {clip["kind"] for clip in draft["variants"]["portrait"]} == {"image", "video"}
    assert any("素材不足" in warning for warning in draft["warnings"])
    assert video_clip["recorded_at"].startswith("2026-09-07T01:00:00")
    assert not video_clip["keep_audio"]
    thumbnail = cv2.imread(str(editing.storage.path_for(video_clip["thumbnail_object_key"])))
    assert thumbnail.shape[:2] == (100, 160)  # full source frame, not the person crop
    assert video_clip["identity_thumbnail_object_key"] == "entry.png"
    assert next(clip for clip in draft["sources"] if clip["kind"] == "image")["location_name"] == "湖畔"


def test_corrupt_source_excluded_with_reason_but_never_repeat_fill(scene, tmp_path):
    db, visitor, add = scene
    add("good.png", "image", order=1)
    broken, _, broken_path = add("bad.avi", order=2)
    broken_path.write_bytes(b"broken video data")
    draft = editing.build_draft(visitor, db, {"template_id": "memory"})
    assert draft["result_type"] == "photo_memory"
    assert any(f"片段 {broken.id}" in warning and "排除" in warning for warning in draft["warnings"])
    for clips in draft["variants"].values():
        assert len(clips) == 1
        assert sum(clip["end_seconds"] - clip["start_seconds"] for clip in clips) <= 8


def test_locks_survive_rematch_independently_per_variant_and_source_is_frozen(scene):
    db, visitor, add = scene
    old_segment, media, path = add("retained.avi", start=0, end=4)
    draft = editing.build_draft(visitor, db, {})
    pin = draft["variants"]["portrait"][0]
    pin.update(locked=True, start_seconds=1.0, end_seconds=2.8, focus_x=.7, crop_mode="focus")
    original_pin = copy.deepcopy(pin)
    db.delete(old_segment)
    db.flush()
    add("new.png", "image", order=2)
    revised = editing.build_draft(visitor, db, {"template_id": "memory"}, draft)
    assert {clip["kind"] for clip in revised["sources"]} == {"video", "image"}
    retained = next(clip for clip in revised["variants"]["portrait"] if clip["id"] == pin["id"])
    assert retained == original_pin
    assert not next(clip for clip in revised["variants"]["landscape"] if clip["id"] == pin["id"])["locked"]
    assert draft["template_id"] == "documentary"
    path.write_bytes(b"overwritten after planning")
    with pytest.raises(editing.EditingError, match="内容改变"):
        editing.build_draft(visitor, db, {}, revised)


def test_missing_locked_media_is_error_even_if_file_remains(scene):
    db, visitor, add = scene
    segment, media, path = add("lock.png", "image")
    draft = editing.build_draft(visitor, db, {})
    draft["variants"]["landscape"][0]["locked"] = True
    db.delete(segment)
    db.delete(media)
    db.flush()
    assert path.exists()
    with pytest.raises(editing.EditingError, match="源素材已移除"):
        editing.build_draft(visitor, db, {}, draft)


def _source(index, *, start=0, end=10, quality=.7, camera=None, recorded_at=None):
    return {"id": f"clip-{index}", "media_id": index, "segment_ids": [index], "kind": "video",
            "source_sha256": str(index), "start_seconds": start, "end_seconds": end,
            "included": True, "locked": False, "quality": {"score": quality}, "sort_order": index,
            "camera_name": camera or str(index), "recorded_at": recorded_at, "crop_mode": "fit"}


def test_planner_targets_without_duplicates_or_ranking_by_identity():
    sources = [_source(index, quality=.8 if index % 3 else .4) for index in range(16)]
    for source in sources:
        source["identity_similarity"] = 1 - source["quality"]["score"]
    template = scenic_templates.TEMPLATES["documentary"]
    landscape = editing.plan_variant(sources, template, 75, "landscape")
    portrait = editing.plan_variant(sources, template, 45, "portrait")
    for clips, target in [(landscape, 75), (portrait, 45)]:
        assert scenic_templates.timeline_duration(clips, template) == pytest.approx(target, abs=.025)
        assert len({clip["id"] for clip in clips}) == len(clips)
        assert clips[0]["quality"]["score"] == .8
        assert all(0 <= clip["start_seconds"] < clip["end_seconds"] <= 10 for clip in clips)
    assert landscape != portrait
    assert all(source["start_seconds"] == 0 for source in sources)


def test_oversize_locked_timeline_fails_instead_of_dropping_a_pin():
    sources = [_source(0, end=40), _source(1, end=30)]
    pins = [{**source, "locked": True} for source in sources]
    with pytest.raises(editing.EditingError, match="超过目标"):
        editing.plan_variant(sources, scenic_templates.TEMPLATES["documentary"], 45, "portrait", pins)


def test_known_time_slots_sort_and_unknown_slot_keeps_manual_order():
    sources = [_source(0, recorded_at="2026-09-07T03:00:00+00:00"), _source(1),
               _source(2, recorded_at="2026-09-07T01:00:00+00:00")]
    assert [clip["id"] for clip in editing.merge_candidates(sources)] == ["clip-2", "clip-1", "clip-0"]
    assert editing._date("00:01:30") is None
    assert editing._date("frame 40") is None


def test_optical_quality_is_independent_of_identity():
    frame = np.zeros((160, 160, 3), dtype=np.uint8)
    frame[::8] = 190
    frame[:, ::8] = 220
    blurry = cv2.GaussianBlur(frame, (31, 31), 12)
    assert editing.frame_quality(frame)["score"] > editing.frame_quality(blurry)["score"]


def _observations(*, offset=0, crowd=False, tiny=False):
    return [{"time_seconds": index / 5, "track_id": offset + 1,
             "box": [.45, .15, .47, .23] if tiny else [.44, .15, .54, .85],
             "identity_score": .8, "reference_score": .9, "crowded": crowd} for index in range(20)]


@pytest.mark.parametrize("scenario", ["steady", "crowd", "tiny", "ambiguous", "occluded"])
def test_track_safety_gates_cover_continuity_size_crossing_and_identity(scenario):
    points = _observations(crowd=scenario == "crowd", tiny=scenario == "tiny")
    if scenario == "ambiguous":
        points += _observations(offset=10)
    if scenario == "occluded":
        points = points[:4]
    result = scenic_tracking.assess_track(points, 20, 0, 4, 1920, 1080)
    assert result["reliable"] == (scenario == "steady")
    if result["reliable"]:
        assert result["start_seconds"] == 0
        assert result["end_seconds"] == 4
        assert all(0 <= point["x"] <= 1 for point in result["trajectory"])
    else:
        assert result["reason"]


def test_trajectory_smoothing_limits_motion_per_second():
    result = scenic_tracking.smooth_trajectory([
        {"time_seconds": 0, "x": .1, "y": .3},
        {"time_seconds": .2, "x": .9, "y": .8},
        {"time_seconds": .4, "x": .2, "y": .1},
    ])
    for previous, current in zip(result, result[1:]):
        assert abs(current["x"] - previous["x"]) <= .03601
        assert abs(current["y"] - previous["y"]) <= .03601


def test_composition_beats_are_local_cached_and_template_affects_real_plan(scene):
    db, visitor, add = scene
    add("tempo.avi", start=0, end=4)
    documentary = editing.build_draft(visitor, db, {})
    upbeat = editing.build_draft(visitor, db, {"template_id": "upbeat", "music_id": "auto"})
    assert upbeat["music_id"] == "oga_happy_beat"
    assert upbeat["variants"]["portrait"][0]["end_seconds"] - upbeat["variants"]["portrait"][0]["start_seconds"] < 4
    report = scenic_tracking.analyze_beats(scenic_templates.music_path("scenic_footsteps"))
    assert report["source"] == "composition_score"
    assert report["beats"][1] == pytest.approx(60 / 116, abs=1e-5)
    assert len(report["beats"]) > 100
    assert documentary["variants"] != upbeat["variants"]


def test_real_bytetrack_keeps_motion_id_and_fresh_source_has_fresh_state():
    pytest.importorskip("lap", reason="Optional local ByteTrack dependency not installed")
    from ultralytics.engine.results import Boxes
    from ultralytics.trackers.byte_tracker import BYTETracker

    args = SimpleNamespace(track_high_thresh=.25, track_low_thresh=.1, new_track_thresh=.25,
                           track_buffer=15, match_thresh=.8, fuse_score=True)
    tracker = BYTETracker(args)
    identities = []
    for step in range(12):
        boxes = Boxes(np.array([[40 + step, 15, 65 + step, 90, .92, 0]], dtype=np.float32), orig_shape=(100, 160))
        tracked = tracker.update(boxes)
        assert len(tracked) == 1
        identities.append(int(tracked[0][4]))
    assert len(set(identities)) == 1
    second_source = BYTETracker(args)
    assert second_source.frame_id == 0
    assert second_source.tracked_stracks == []
    distant_boxes = Boxes(np.array([[5, 10, 25, 80, .9, 0]], dtype=np.float32), orig_shape=(100, 160))
    assert len(second_source.update(distant_boxes)) == 1
    assert tracker.frame_id == 12


def test_local_yolo_bytetrack_pipeline_detects_and_tracks_bundled_people():
    pytest.importorskip("lap", reason="Optional local ByteTrack dependency not installed")
    from ultralytics import YOLO
    import ultralytics

    weights = Path(__file__).resolve().parents[1] / "yolov8n.pt"
    fixture_image = Path(ultralytics.__file__).parent / "assets" / "bus.jpg"
    if not weights.is_file() or not fixture_image.is_file():
        pytest.skip("Optional smoke requires local YOLO weights and bundled bus sample")
    frame = cv2.imread(str(fixture_image))
    model = YOLO(str(weights))
    tracked_ids = []
    for index in range(4):
        shifted = cv2.warpAffine(frame, np.array([[1, 0, index * 2], [0, 1, 0]], dtype=np.float32), (frame.shape[1], frame.shape[0]))
        result = model.track(shifted, persist=True, tracker="bytetrack.yaml", classes=[0],
                             conf=.25, verbose=False, device="cpu")[0]
        assert result.boxes.id is not None and len(result.boxes.id) >= 1
        tracked_ids.append(set(result.boxes.id.detach().cpu().numpy().tolist()))
    assert set.intersection(*tracked_ids)


def test_repeated_candidates_hash_each_source_once_in_one_draft(scene, monkeypatch):
    db, visitor, add = scene
    segment, media, path = add("single.avi", start=0, end=1)
    db.add(database.TouristVisitSegment(session=visitor, project=visitor.project, media_file=media,
           original_media_object_key=path.name, start_seconds=2, end_seconds=4, sort_order=2, included=True))
    db.flush()
    original = editing.source_sha256
    calls = []

    def measured(filename):
        calls.append(str(filename))
        return original(filename)

    monkeypatch.setattr(editing, "source_sha256", measured)
    editing.build_draft(visitor, db, {})
    assert calls.count(str(path)) == 1


def test_locked_landscape_composition_is_not_automatically_changed():
    source = _source(0, end=4)
    pin = {**source, "locked": True, "crop_mode": "track", "trajectory": [{"time_seconds": 0, "x": .4, "y": .5}]}
    result = editing.plan_variant([source], scenic_templates.TEMPLATES["documentary"], 75, "landscape", [pin])
    assert result == [pin]


def test_replanning_preserves_explicit_exclusions_per_format_and_restores_on_request(scene):
    db, visitor, add = scene
    add("omitted.png", "image", quality_color="navy")
    add("kept.png", "image", order=2, quality_color="green")
    draft = editing.build_draft(visitor, db, {})
    omitted_id = draft["sources"][0]["id"]
    next(clip for clip in draft["variants"]["portrait"] if clip["id"] == omitted_id)["included"] = False
    pin = next(clip for clip in draft["variants"]["landscape"] if clip["id"] == omitted_id)
    pin.update(locked=True, focus_x=.7, crop_mode="focus")
    revised = editing.build_draft(visitor, db, {}, draft)
    assert next(clip for clip in revised["variants"]["landscape"] if clip["id"] == omitted_id) == pin
    assert not next(clip for clip in revised["variants"]["portrait"] if clip["id"] == omitted_id)["included"]
    assert len([clip for clip in revised["variants"]["portrait"] if clip["included"]]) == 1
    repeated = editing.build_draft(visitor, db, {}, revised)
    restored = next(clip for clip in repeated["variants"]["portrait"] if clip["id"] == omitted_id)
    assert not restored["included"]
    restored["included"] = True
    included = editing.build_draft(visitor, db, {}, repeated)
    assert all(clip["included"] for clip in included["variants"]["portrait"])
    assert included["exclusions"]["portrait"] == []


def test_unselected_candidates_are_available_to_next_automatic_plan(scene):
    db, visitor, add = scene
    add("one.png", "image", quality_color="navy")
    add("two.png", "image", order=2, quality_color="green")
    draft = editing.build_draft(visitor, db, {})
    omitted_id = draft["sources"][0]["id"]
    for clips in draft["variants"].values():
        clips[:] = [clip for clip in clips if clip["id"] != omitted_id]
    revised = editing.build_draft(visitor, db, {}, draft)
    assert all(any(clip["id"] == omitted_id and clip["included"] for clip in clips) for clips in revised["variants"].values())


def test_exclusion_matches_rematched_overlap_unions_without_expanding_its_anchor(scene):
    db, visitor, add = scene
    old, media, path = add("rematched.avi", start=0, end=1.5)
    add("remaining.png", "image", order=2)
    draft = editing.build_draft(visitor, db, {})
    old_id = next(clip["id"] for clip in draft["sources"] if clip["kind"] == "video")
    next(clip for clip in draft["variants"]["portrait"] if clip["id"] == old_id)["included"] = False
    db.delete(old)
    db.flush()

    def candidate(start, end, order):
        segment = database.TouristVisitSegment(session=visitor, project=visitor.project, media_file=media,
            original_media_object_key=path.name, start_seconds=start, end_seconds=end, sort_order=order, included=True)
        db.add(segment)
        db.flush()
        return segment

    first = candidate(.5, 1.2, 3)
    second = candidate(1.0, 2.5, 4)
    revised = editing.build_draft(visitor, db, {}, draft)
    replacement = next(clip for clip in revised["variants"]["portrait"] if clip["kind"] == "video")
    assert replacement["id"] != old_id
    assert not replacement["included"]
    assert next(clip for clip in revised["variants"]["landscape"] if clip["kind"] == "video")["included"]
    repeated = editing.build_draft(visitor, db, {}, revised)
    assert repeated["exclusions"]["portrait"][0]["end_seconds"] == 1.5
    db.delete(first)
    db.delete(second)
    db.flush()
    candidate(2, 4, 5)
    fresh = editing.build_draft(visitor, db, {}, repeated)
    assert next(clip for clip in fresh["variants"]["portrait"] if clip["kind"] == "video")["included"]


def test_photo_exclusion_survives_template_and_absent_rematch_rounds(scene):
    db, visitor, add = scene
    old, media, path = add("excluded.png", "image", quality_color="navy")
    add("remaining.png", "image", order=2, quality_color="green")
    draft = editing.build_draft(visitor, db, {})
    excluded = next(clip for clip in draft["variants"]["portrait"] if clip["media_id"] == media.id)
    excluded["included"] = False
    revised = editing.build_draft(visitor, db, {"template_id": "memory"}, draft)
    replacement = next(clip for clip in revised["variants"]["portrait"] if clip["media_id"] == media.id)
    assert replacement["id"] != excluded["id"]  # Template changes photo candidate duration.
    assert not replacement["included"]
    db.delete(old)
    db.flush()
    absent = editing.build_draft(visitor, db, {}, revised)
    assert len(absent["variants"]["portrait"]) == 1
    db.add(database.TouristVisitSegment(session=visitor, project=visitor.project, media_file=media,
        original_media_object_key=path.name, start_seconds=0, end_seconds=3, sort_order=3, included=True))
    db.flush()
    returned = editing.build_draft(visitor, db, {}, absent)
    assert not next(clip for clip in returned["variants"]["portrait"] if clip["media_id"] == media.id)["included"]
    Image.new("RGB", (160, 100), "orange").save(path)
    changed = editing.build_draft(visitor, db, {}, returned)
    assert next(clip for clip in changed["variants"]["portrait"] if clip["media_id"] == media.id)["included"]


def test_replanning_all_excluded_format_gives_actionable_error(scene):
    db, visitor, add = scene
    add("only.png", "image")
    draft = editing.build_draft(visitor, db, {})
    draft["variants"]["portrait"][0]["included"] = False
    with pytest.raises(editing.EditingError, match="竖版素材已全部排除"):
        editing.build_draft(visitor, db, {}, draft)



def test_replanned_long_window_exclusion_remains_saveable(scene):
    from app.api.scenic.editing_routes import canonical_edit

    db, visitor, add = scene
    _, _, path = add("long-window.avi", start=10, end=80)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 100))
    assert writer.isOpened()
    for index in range(800):
        frame = np.full((100, 160, 3), (70, 105, 130), dtype=np.uint8)
        cv2.rectangle(frame, (index % 80, 10), (index % 80 + 30, 90), (240, 190, 40), -1)
        writer.write(frame)
    writer.release()
    add("kept-photo.png", "image", order=2)
    draft = editing.build_draft(visitor, db, {})
    next(clip for clip in draft["variants"]["portrait"] if clip["kind"] == "video")["included"] = False
    revised = editing.build_draft(visitor, db, {}, draft)
    excluded = next(clip for clip in revised["variants"]["portrait"] if clip["kind"] == "video")
    assert not excluded["included"]
    assert excluded["end_seconds"] - excluded["start_seconds"] <= 7
    assert revised["exclusions"]["portrait"][0]["end_seconds"] == 80
    saved = canonical_edit(revised, revised)
    assert saved["exclusions"] == revised["exclusions"]


def test_many_returning_exclusions_keep_saveable_timeline_and_all_intents(scene):
    from app.api.scenic.editing_routes import canonical_edit

    db, visitor, add = scene
    for index in range(103):
        add(f"candidate-{index}.png", "image", order=index, quality_color=(index * 2, 30, 140))
    draft = editing.build_draft(visitor, db, {})
    # Exclusions can accumulate over multiple rematches even while each saved
    # timeline has at most 100 entries. All those candidates can later return.
    kept = draft["sources"][-1]
    draft["variants"]["portrait"] = [{**copy.deepcopy(kept), "included": True}]
    draft["exclusions"]["portrait"] = [editing._exclusion_source(source) for source in draft["sources"][:-1]]
    revised = editing.build_draft(visitor, db, {}, draft)
    assert len(revised["variants"]["portrait"]) == 100
    assert len(revised["exclusions"]["portrait"]) == 102
    assert any("其余 3 个镜头的排除设置仍已保留" in message for message in revised["warnings"])
    saved = canonical_edit(revised, revised)
    repeated = editing.build_draft(visitor, db, {}, saved)
    assert len(repeated["exclusions"]["portrait"]) == 102
    assert sum(clip["included"] for clip in repeated["variants"]["portrait"]) == 1
