"""Planner outputs must remain editable, and tracking crops stay identity-safe."""

import copy

import pytest
from fastapi import HTTPException

from app.api.scenic.editing_routes import canonical_edit
from app.services import scenic_editing as editing, scenic_templates, scenic_tracking
from test_scenic_editing import reset_backend_state, scene


def clip(index, *, start=0.0, end=0.31, locked=False):
    return {"id": f"clip-{index}", "media_id": index, "segment_ids": [index],
            "source_sha256": str(index), "kind": "video", "start_seconds": start,
            "end_seconds": end, "duration_seconds": 100.0, "included": True,
            "locked": locked, "quality": {"score": .7}, "sort_order": index,
            "camera_name": str(index), "recorded_at": None, "crop_mode": "fit"}


def draft_for(sources):
    return {"template_id": "documentary", "music_id": "none", "sources": sources,
            "variants": {name: copy.deepcopy(sources) for name in ("landscape", "portrait")}}


def test_many_short_sources_produce_saveable_bounded_timelines():
    sources = [clip(index) for index in range(400)]
    draft = draft_for(sources)
    for orientation, target in (("landscape", 75), ("portrait", 45)):
        draft["variants"][orientation] = editing.plan_variant(
            sources, scenic_templates.TEMPLATES["documentary"], target, orientation)
        assert len(draft["variants"][orientation]) == 100
        assert all(item["end_seconds"] - item["start_seconds"] >= .3 - 1e-6
                   for item in draft["variants"][orientation])
    result = canonical_edit(draft, draft)
    assert len(result["variants"]["portrait"]) == 100
    assert len(sources) == 400


def test_more_than_one_hundred_locks_fail_without_silently_dropping_shots():
    sources = [clip(index, locked=True) for index in range(101)]
    with pytest.raises(editing.EditingError, match="锁定镜头超过 100"):
        editing.plan_variant(sources, scenic_templates.TEMPLATES["documentary"], 45, "portrait")
    assert all(item["locked"] for item in sources)


@pytest.mark.parametrize("duration", [.001, .029, .299])
def test_unrenderable_manual_cuts_are_rejected_at_save(duration):
    draft = draft_for([clip(1, end=4)])
    edited = copy.deepcopy(draft)
    edited["variants"]["portrait"][0]["end_seconds"] = duration
    with pytest.raises(HTTPException, match="至少保留 0.3 秒") as error:
        canonical_edit(edited, draft)
    assert error.value.status_code == 422


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("start,end", [(0, 25), (20, 35)])
def test_manual_track_edits_cannot_extend_outside_confirmed_coverage(legacy, start, end):
    source = clip(1, start=20, end=25)
    source.update(crop_mode="track", available_start_seconds=0, available_end_seconds=60,
                  trajectory=[{"time_seconds": 20, "x": .5, "y": .5},
                              {"time_seconds": 24.8, "x": .5, "y": .5}])
    if not legacy:
        source.update(tracking_start_seconds=20, tracking_end_seconds=25)
    draft = draft_for([source])
    edited = copy.deepcopy(draft)
    target = edited["variants"]["portrait"][0]
    target.update(start_seconds=start, end_seconds=end,
                  tracking_start_seconds=0, tracking_end_seconds=100)
    with pytest.raises(HTTPException, match="超出跟踪分析区间"):
        canonical_edit(edited, draft)
    target["crop_mode"] = "fit"
    result = canonical_edit(edited, draft)
    assert result["variants"]["portrait"][0]["end_seconds"] == end


def test_build_draft_records_track_coverage_without_losing_manual_source_range(scene, monkeypatch):
    db, visitor, add = scene
    add("tracked.avi", "camera", start=0, end=4)
    monkeypatch.setattr(scenic_tracking, "tracking_enabled", lambda: True)
    monkeypatch.setattr(scenic_tracking, "analyze_candidate", lambda *args: {
        "reliable": True, "start_seconds": 1., "end_seconds": 3., "visible_fraction": .8,
        "trajectory": [{"time_seconds": 1., "x": .5, "y": .5},
                       {"time_seconds": 2.8, "x": .5, "y": .5}],
    })
    draft = editing.build_draft(visitor, db, {"use_tracking": True, "music_id": "none"})
    source = draft["sources"][0]
    assert (source["available_start_seconds"], source["available_end_seconds"]) == (0, 4)
    assert (source["tracking_start_seconds"], source["tracking_end_seconds"]) == (1, 3)
    assert canonical_edit(draft, draft)["variants"]["portrait"][0]["crop_mode"] == "track"


def observations(box):
    return [{"time_seconds": index / 5, "track_id": 1, "box": list(box),
             "identity_score": .8, "reference_score": .9, "crowded": False}
            for index in range(20)]


def test_narrow_portrait_track_rejects_vertical_subject_truncation():
    result = scenic_tracking.assess_track(observations([.3, .1, .6, .9]), 20, 0, 4, 700, 2000)
    assert not result["reliable"] and "无法完整容纳人物" in result["reason"]
    safe = scenic_tracking.assess_track(observations([.3, .3, .6, .65]), 20, 0, 4, 700, 2000)
    assert safe["reliable"]


def test_narrow_portrait_track_rejects_vertical_motion_outpacing_the_crop():
    points = observations([.3, .25, .6, .49])
    for index, item in enumerate(points):
        delta = min(index * .05, .48)
        item["box"][1] += delta
        item["box"][3] += delta
    result = scenic_tracking.assess_track(points, 20, 0, 4, 400, 1600)
    assert not result["reliable"] and "平滑裁切可能丢失主体" in result["reason"]


@pytest.mark.parametrize("field,value", [("reference_score", float("nan")),
                                        ("identity_score", float("inf")),
                                        ("time_seconds", -1)])
def test_invalid_analysis_values_cannot_authorize_a_tracking_crop(field, value):
    points = observations([.44, .15, .54, .85])
    points[3][field] = value
    result = scenic_tracking.assess_track(points, 20, 0, 4, 1920, 1080)
    assert not result["reliable"] and result["reason"]


@pytest.mark.parametrize("template_id", ["documentary", "upbeat", "memory"])
def test_variable_source_lengths_reserve_usable_shots_and_frozen_locks(template_id):
    import random
    rng = random.Random(4312)
    template = scenic_templates.TEMPLATES[template_id]
    for case in range(30):
        sources = [clip(index, end=rng.uniform(.3, 12)) for index in range(30)]
        for source in sources:
            source["quality"]["score"] = rng.random()
        pinned = copy.deepcopy(sources[case % len(sources)])
        pinned["locked"] = True
        pinned["end_seconds"] = 35 + rng.random() * 8
        sources[case % len(sources)]["duration_seconds"] = 100
        planned = editing.plan_variant(sources, template, 45, "portrait", [pinned])
        assert next(item for item in planned if item["id"] == pinned["id"]) == pinned
        assert scenic_templates.timeline_duration(planned, template) <= 45.025
        assert all(item["end_seconds"] - item["start_seconds"] >= .3 - 1e-6 for item in planned)
        assert len({item["id"] for item in planned}) == len(planned)


@pytest.mark.parametrize("aspect", [4 / 3, 3 / 4])
def test_non_square_pixels_use_safe_framing_before_loading_models(aspect, monkeypatch):
    monkeypatch.setattr(scenic_tracking, "tracking_enabled", lambda: True)
    monkeypatch.setattr(scenic_tracking, "_weights_path", lambda: pytest.fail("loaded YOLO for unsafe SAR"))
    result = scenic_tracking.analyze_candidate({"sample_aspect_ratio": aspect}, None, None)
    assert not result["reliable"] and "非方形像素" in result["reason"]


def test_probe_caches_real_sample_aspect_ratio(tmp_path):
    import shutil
    import subprocess
    executable = editing.ffmpeg_executable()
    if not executable or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")
    path = tmp_path / "anamorphic.mp4"
    subprocess.run([executable, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x100:rate=24",
                    "-t", "1.25", "-vf", "setsar=4/3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   capture_output=True, check=True)
    metadata = editing.analyze_media(path)
    assert metadata["sample_aspect_ratio"] == pytest.approx(4 / 3)
    assert metadata["analysis_version"] == 2
    assert editing.analyze_media(path)["sample_aspect_ratio"] == pytest.approx(4 / 3)


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
@pytest.mark.parametrize("start,end", [(5.7, 8.167475999999999), (5.700024, 8.167476), (0.000024, 0.300024)])
def test_fractional_camera_bounds_survive_automatic_planning(orientation, start, end):
    source = clip(1, start=start, end=end)
    result = editing.plan_variant([source], scenic_templates.TEMPLATES["documentary"], 45, orientation)
    assert start <= result[0]["start_seconds"] < result[0]["end_seconds"] <= end
    assert result[0]["end_seconds"] - result[0]["start_seconds"] >= .3 - 1e-6
