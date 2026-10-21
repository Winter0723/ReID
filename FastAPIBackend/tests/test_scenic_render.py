"""Real FFmpeg coverage: composition, sound, previews and immutable sources."""

import hashlib
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import wave

import numpy as np
from PIL import Image, ImageDraw
import pytest

from app.services import scenic_render, scenic_templates


@pytest.fixture(autouse=True)
def reset_backend_state(monkeypatch, tmp_path):
    # These integration tests exercise actual media binaries, with no database,
    # queue, authentication or model-service side effects.
    monkeypatch.setattr(scenic_render, "storage", SimpleNamespace(path_for=lambda key: tmp_path / key))
    monkeypatch.setattr(scenic_templates, "get_settings", lambda: SimpleNamespace(object_storage_dir=tmp_path / "cache"))
    monkeypatch.setenv("SCENIC_VIDEO_ENCODER", "libx264")


@pytest.fixture
def sources(tmp_path):
    ffmpeg = scenic_render.ffmpeg_executable()
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("Full FFmpeg/FFprobe installation required")
    video = tmp_path / "人物 'camera';.mp4"
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=480x320:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
    ], check=True, capture_output=True)
    photo = tmp_path / "旅途; 'photo'.png"
    image = Image.new("RGB", (480, 320), "#88aab1")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 90, 300), fill="#FFDD22")
    draw.rectangle((185, 60, 280, 295), fill="#EF7777")
    draw.rectangle((390, 20, 460, 300), fill="#22DD99")
    image.save(photo)

    def clip(path, kind, identifier):
        return {
            "id": identifier, "media_id": 1, "object_key": path.name,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "kind": kind, "start_seconds": 0, "end_seconds": 2,
            "included": True, "locked": True, "keep_audio": kind == "video",
            "crop_mode": "fit", "location_name": "湖边漫步", "recorded_at": "2026-09-07T10:00:00",
        }

    return [clip(video, "video", "video-1"), clip(photo, "image", "photo-1")]


def _snapshot(clips, template="documentary"):
    return {
        "template_id": template, "template_version": "1", "title": "秋日旅途 {你好} \\N",
        "brand": "山水景区", "music_id": scenic_templates.TEMPLATES[template]["music_id"],
        "music_volume": .3, "original_volume": .7,
        "variants": {"landscape": clips, "portrait": clips},
    }


@pytest.mark.parametrize("template,orientation,mode", [
    ("documentary", "landscape", "fit"),
    ("memory", "portrait", "focus"),
    ("upbeat", "portrait", "track"),
])
def test_real_mixed_render_has_sound_correct_frame_previews_and_cover(tmp_path, sources, template, orientation, mode):
    for clip in sources:
        clip["crop_mode"] = mode
        clip["focus_x"] = .48
        clip["focus_y"] = .55
        if mode == "track":
            clip["trajectory"] = [{"time_seconds": 0, "x": .46, "y": .5}, {"time_seconds": 2, "x": .6, "y": .6}]
    updates = []
    snapshot = _snapshot(sources, template)
    snapshot["cover_clip_id"] = "photo-1"
    result = scenic_render.render_variant(snapshot, orientation, tmp_path / "render", lambda *value: updates.append(value))
    full = scenic_render.probe_media(result["full_path"])
    draft = scenic_render.probe_media(result["draft_preview_path"])
    preview = scenic_render.probe_media(result["preview_path"])
    dimensions = (1920, 1080) if orientation == "landscape" else (1080, 1920)
    assert (full["width"], full["height"]) == dimensions
    assert full["video_codec"] == "h264"
    assert full["frame_rate"] == "30/1"
    assert any(stream["codec_name"] == "aac" for stream in full["streams"])
    assert full["duration_seconds"] == pytest.approx(result["duration_seconds"], abs=.06)
    assert draft["duration_seconds"] == pytest.approx(full["duration_seconds"], abs=.1)
    assert preview["duration_seconds"] < full["duration_seconds"] * .5
    assert result["cover_clip_id"] == "photo-1"
    assert result["cover_time_seconds"] > 2
    assert max(draft["width"], draft["height"]) == 960
    with Image.open(result["cover_path"]) as cover:
        assert cover.size == dimensions
        assert np.asarray(cover).mean() > 12
    sound = subprocess.run([
        scenic_render.ffmpeg_executable(), "-v", "error", "-i", str(result["full_path"]),
        "-vn", "-f", "s16le", "-ac", "1", "-ar", "8000", "-",
    ], capture_output=True, check=True).stdout
    assert np.sqrt(np.mean(np.frombuffer(sound, dtype="<i2").astype(float) ** 2)) > 100
    assert updates[0][0] < updates[-1][0] == 1
    assert not list((tmp_path / "render").glob("scenic_render_*"))


def test_real_render_music_off_does_not_leak_original_audio(tmp_path, sources):
    clip = sources[0]
    clip["keep_audio"] = False
    clip["end_seconds"] = 1
    snapshot = _snapshot([clip])
    snapshot["music_id"] = "none"
    result = scenic_render.render_variant(snapshot, "landscape", tmp_path / "muted")
    sound = subprocess.run([
        scenic_render.ffmpeg_executable(), "-v", "error", "-i", str(result["full_path"]),
        "-vn", "-f", "s16le", "-ac", "1", "-ar", "8000", "-",
    ], capture_output=True, check=True).stdout
    assert np.max(np.abs(np.frombuffer(sound, dtype="<i2"))) == 0


@pytest.mark.parametrize("failure", ["mutated", "missing", "range", "missing_hash", "template"])
def test_submitted_snapshot_fails_without_silently_skipping(tmp_path, sources, failure):
    snapshot = _snapshot(sources)
    if failure == "mutated":
        (tmp_path / sources[0]["object_key"]).write_bytes(b"Changed since submission")
    elif failure == "missing":
        (tmp_path / sources[0]["object_key"]).unlink()
    elif failure == "range":
        sources[0]["end_seconds"] = 8
    elif failure == "missing_hash":
        sources[0].pop("source_sha256")
    else:
        snapshot["template_version"] = "no-longer-available"
    with pytest.raises(scenic_render.ScenicRenderError):
        scenic_render.render_variant(snapshot, "portrait", tmp_path / "invalid")
    assert not (tmp_path / "invalid" / "full.mp4").exists()


def test_original_music_catalog_reproducible_licensed_and_distinct(tmp_path):
    catalog = [item for item in scenic_templates.music_catalog() if item["origin"] == "generated"]
    assert len(catalog) == 3
    assert len({item["sha256"] for item in catalog}) == 3
    for item in catalog:
        assert item["license"] == "CC0-1.0"
        assert item["beat_source"] == "composition_score"
        assert len(item["beats"]) == 64
        with wave.open(str(scenic_templates.music_path(item["id"])), "rb") as source:
            assert source.getnchannels() == 2
            assert source.getframerate() == 48000
    original = scenic_templates.music_path(catalog[0]["id"])
    fingerprint = hashlib.sha256(original.read_bytes()).hexdigest()
    original.unlink()
    regenerated = scenic_templates.music_path(catalog[0]["id"])
    assert hashlib.sha256(regenerated.read_bytes()).hexdigest() == fingerprint


@pytest.mark.parametrize("music_id,orientation", [
    ("oga_revelation", "landscape"),
    ("oga_happy_beat", "portrait"),
    ("oga_happy_moments", "portrait"),
    ("scenic_morning", "landscape"),
])
def test_cc0_music_and_legacy_track_are_mixed_into_real_film(tmp_path, sources, music_id, orientation):
    # A silent photo timeline proves that the actual downloaded file supplies
    # the rendered soundtrack, rather than the source video's existing audio.
    track = scenic_templates.music_metadata(music_id)
    snapshot = _snapshot([dict(sources[1], end_seconds=4, keep_audio=False)])
    snapshot.update(music_id=music_id, music_version=track["version"], music_sha256=track["sha256"])
    result = scenic_render.render_variant(snapshot, orientation, tmp_path / music_id)
    output = scenic_render.probe_media(result["full_path"])
    assert output["has_audio"]
    sound = subprocess.run([
        scenic_render.ffmpeg_executable(), "-v", "error", "-i", str(result["full_path"]),
        "-vn", "-f", "s16le", "-ac", "1", "-ar", "8000", "-",
    ], capture_output=True, check=True).stdout
    assert np.sqrt(np.mean(np.frombuffer(sound, dtype="<i2").astype(float) ** 2)) > 100


def test_bundled_music_catalog_matches_distributed_files_and_license_records():
    catalog = [item for item in scenic_templates.music_catalog() if item["origin"] == "download"]
    assert len(catalog) == 3
    for track in catalog:
        path = scenic_templates.music_path(track["id"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == track["sha256"]
        assert scenic_templates.music_version(track["id"]) == track["version"]
        assert track["license"] == "CC0-1.0"
        assert track["attribution_required"] is False
        assert track["source_url"].startswith("https://opengameart.org/content/")
        assert track["author"] and track["source_sha256"] and track["downloaded_at"]
        assert track["duration_seconds"] > 30
        assert "filename" not in track
        assert "beats" not in track  # No invented synth grid for recordings.


@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_bundled_music_cannot_silently_change_or_fall_back(tmp_path, monkeypatch, failure):
    music_id = "oga_revelation"
    track = scenic_templates.DOWNLOADED_MUSIC[music_id]
    root = tmp_path / "music-assets"
    path = root / track["filename"]
    path.parent.mkdir(parents=True)
    if failure == "changed":
        path.write_bytes(b"replaced audio")
    monkeypatch.setattr(scenic_templates, "MUSIC_ASSET_DIR", root)
    with pytest.raises(RuntimeError, match="资源缺失|资源校验失败"):
        scenic_templates.music_path(music_id)


def test_changed_music_resource_fails(tmp_path, sources):
    snapshot = _snapshot(sources)
    snapshot["music_sha256"] = "0" * 64
    with pytest.raises(scenic_render.ScenicRenderError, match="配乐已发生变化"):
        scenic_render.render_variant(snapshot, "landscape", tmp_path / "invalid_music")


def test_rotated_variable_rate_video_without_audio_renders(tmp_path, sources):
    ffmpeg = scenic_render.ffmpeg_executable()
    variable = tmp_path / "variable.mp4"
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
        "-vf", "select='if(lt(t,1),not(mod(n,2)),1)'", "-fps_mode", "vfr", "-an", "-c:v", "libx264", str(variable),
    ], capture_output=True, check=True)
    rotated = tmp_path / "rotated.mp4"
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-display_rotation", "90", "-i", str(variable), "-c", "copy", str(rotated),
    ], capture_output=True, check=True)
    metadata = scenic_render.probe_media(rotated)
    assert (metadata["width"], metadata["height"]) == (180, 320)
    assert metadata["frame_rate"] != "30/1"
    clip = dict(sources[0], object_key=rotated.name, source_sha256=hashlib.sha256(rotated.read_bytes()).hexdigest())
    result = scenic_render.render_variant(_snapshot([clip]), "portrait", tmp_path / "rotated_output")
    output = scenic_render.probe_media(result["full_path"])
    assert output["frame_rate"] == "30/1"
    assert output["has_audio"]
    assert (output["width"], output["height"]) == (1080, 1920)


def test_photo_only_film_and_very_short_group_boundaries(tmp_path, sources):
    clips = [dict(sources[1], id=f"photo-{index}", end_seconds=.6 if index in {5, 6} else .9) for index in range(7)]
    result = scenic_render.render_variant(_snapshot(clips, "memory"), "landscape", tmp_path / "photo_album")
    expected = sum(clip["end_seconds"] for clip in clips) - sum(
        min(.45, left["end_seconds"] / 3, right["end_seconds"] / 3) for left, right in zip(clips, clips[1:])
    )
    assert result["duration_seconds"] == pytest.approx(expected, abs=.2)
    assert scenic_render.probe_media(result["full_path"])["has_audio"]


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_real_render_omits_excluded_clips_and_cover_in_both_variants(tmp_path, sources, orientation):
    sources[0]["end_seconds"] = 1
    sources[1].update(included=False, locked=False)
    # An excluded source must not be loaded or make its way into the film.
    (tmp_path / sources[1]["object_key"]).unlink()
    snapshot = _snapshot(sources)
    snapshot["music_id"] = "none"
    snapshot["cover_clip_id"] = sources[1]["id"]
    result = scenic_render.render_variant(snapshot, orientation, tmp_path / orientation)
    assert result["duration_seconds"] == pytest.approx(1, abs=.1)
    assert result["cover_clip_id"] == sources[0]["id"]
    assert scenic_render.probe_media(result["draft_preview_path"])["duration_seconds"] == pytest.approx(1, abs=.1)
