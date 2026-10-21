"""Real-frame regressions for safe tracking and display aspect ratio."""

import subprocess

import numpy as np
from PIL import Image, ImageDraw
import pytest

from app.services import scenic_render, scenic_templates
from test_scenic_render import reset_backend_state, sources, _snapshot


def _frame(path, clip, width, height, *, sample_aspect_ratio=None):
    ffmpeg = scenic_render.ffmpeg_executable()
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    graph = scenic_render._framing(clip, width, height, 1, scenic_templates.TEMPLATES["documentary"])
    if sample_aspect_ratio:
        graph = graph.replace("[0:v]", f"[0:v]setsar={sample_aspect_ratio},", 1)
    result = subprocess.run([
        ffmpeg, "-v", "error", "-filter_complex_threads", "1", "-i", str(path),
        "-filter_complex", graph, "-map", "[v]", "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)


@pytest.mark.parametrize("point_count,start", [(1, 5.0), (2, 5.0), (121, 5.125)])
def test_portrait_tracking_starts_at_confirmed_subject_not_default_center(tmp_path, point_count, start):
    source = tmp_path / "off-center-person.png"
    image = Image.new("RGB", (1000, 500), "#002255")
    ImageDraw.Draw(image).rectangle((750, 50, 850, 450), fill="#EE2211")
    image.save(source)
    clip = {"kind": "video", "crop_mode": "track", "focus_x": .5, "focus_y": .5,
            "start_seconds": start, "end_seconds": start + 1,
            "trajectory": [{"time_seconds": 5 + index / 4, "x": .8, "y": .5} for index in range(point_count)]}
    frame = _frame(source, clip, 180, 320)
    center = frame[160, 90]
    assert center[0] > 180 and center[2] < 60, "confirmed person was cropped out"


@pytest.mark.parametrize("mode", ["fit", "focus"])
def test_anamorphic_material_preserves_display_proportions(tmp_path, mode):
    source = tmp_path / "anamorphic.png"
    image = Image.new("RGB", (320, 240), "#AA1122")
    # These 60 x 80 stored samples display as an 80 x 80 square with SAR 4:3.
    ImageDraw.Draw(image).rectangle((120, 80, 179, 159), fill="#11DD22")
    image.save(source)
    clip = {"kind": "video", "crop_mode": mode, "start_seconds": 0, "end_seconds": 1}
    frame = _frame(source, clip, 320, 180, sample_aspect_ratio="4/3")
    ys, xs = np.where((frame[:, :, 1] > 150) & (frame[:, :, 0] < 70))
    assert xs.size and ys.size
    assert (xs.max() - xs.min()) / (ys.max() - ys.min()) == pytest.approx(1, abs=.06)


@pytest.mark.parametrize("field,value", [("x", float("nan")), ("time_seconds", float("inf")), ("y", -1)])
def test_corrupt_frozen_tracking_values_fail_clearly(field, value):
    point = {"time_seconds": 0, "x": .8, "y": .5}
    point[field] = value
    clip = {"crop_mode": "track", "start_seconds": 0, "trajectory": [point]}
    with pytest.raises(scenic_render.ScenicRenderError, match="跟踪"):
        scenic_render._tracking_expression(clip, "y" if field == "y" else "x")


def test_off_center_tracking_renders_all_assets_with_real_ffmpeg(tmp_path, sources):
    clip = dict(sources[0], crop_mode="track", start_seconds=.5, end_seconds=1.5,
                trajectory=[{"time_seconds": .25 + index / 4, "x": .75 + index * .01, "y": .5} for index in range(7)])
    snapshot = _snapshot([clip])
    snapshot["music_id"] = "none"
    result = scenic_render.render_variant(snapshot, "portrait", tmp_path / "tracking-film")
    for name in ("full", "draft_preview", "preview"):
        assert scenic_render.probe_media(result[f"{name}_path"])["has_audio"]
    assert result["duration_seconds"] == pytest.approx(1, abs=.1)


def test_long_verified_trajectory_encodes_without_windows_command_line_overflow(tmp_path):
    ffmpeg = scenic_render.ffmpeg_executable()
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    source = tmp_path / "long-track.png"
    image = Image.new("RGB", (1000, 500), "#002255")
    ImageDraw.Draw(image).rectangle((750, 50, 850, 450), fill="#EE2211")
    image.save(source)
    clip = {"kind": "video", "crop_mode": "track", "start_seconds": 0, "end_seconds": 120,
            "trajectory": [{"time_seconds": index / 4, "x": .8, "y": .5} for index in range(481)]}
    graph = scenic_render._framing(clip, 180, 320, 120, scenic_templates.TEMPLATES["documentary"])
    assert len(graph) > 32767
    output = tmp_path / "long-track.mp4"
    scenic_render._encode(ffmpeg, ["-loop", "1", "-framerate", "30", "-i", str(source)],
                          ["-filter_complex", graph, "-map", "[v]", "-t", "0.1", "-an"], output, tmp_path)
    metadata = scenic_render.probe_media(output)
    assert metadata["width"] == 180 and metadata["height"] == 320


@pytest.mark.parametrize("help_text,option", [
    ("-filter_complex_script filename  read complex filtergraph from a file", "-filter_complex_script"),
    ("-filter_complex_script <filename>  deprecated, use -/filter_complex instead", "-/filter_complex"),
    ("-filter_complex <graph_description>  create a complex filtergraph", "-/filter_complex"),
])
def test_filter_files_support_old_and_new_ffmpeg_without_reprobing(monkeypatch, tmp_path, help_text, option):
    from pathlib import Path

    graph = "[0:v]" + "null," * 8000 + "null[v]"
    probes, encodes = [], []

    def run(arguments, **kwargs):
        if "-h" in arguments:
            probes.append(arguments)
            return help_text
        encodes.append(arguments)
        assert "-filter_complex" not in arguments
        assert graph not in arguments
        assert Path(arguments[arguments.index(option) + 1]).read_text(encoding="utf-8") == graph
        return ""

    monkeypatch.setattr(scenic_render, "_run", run)
    scenic_render._filter_file_option.cache_clear()
    try:
        for index in range(2):
            scenic_render._encode("test-ffmpeg", [], ["-filter_complex", graph, "-map", "[v]"],
                                  tmp_path / f"film-{index}.mp4", tmp_path)
        assert len(probes) == 1
        assert len(encodes) == 2
    finally:
        scenic_render._filter_file_option.cache_clear()


def test_new_filter_file_argument_survives_encoder_fallback(monkeypatch, tmp_path):
    graph = "[0:v]null[v]"
    attempts = []

    def run(arguments, **kwargs):
        if "-h" in arguments:
            return "-filter_complex <graph_description>  create a complex filtergraph"
        attempts.append(arguments)
        if arguments[arguments.index("-c:v") + 1] == "h264_nvenc":
            raise scenic_render.ScenicRenderError("GPU encoder unavailable")
        raise scenic_render.ScenicRenderError("invalid media")

    monkeypatch.setattr(scenic_render, "_run", run)
    monkeypatch.setenv("SCENIC_VIDEO_ENCODER", "h264_nvenc")
    scenic_render._filter_file_option.cache_clear()
    try:
        with pytest.raises(scenic_render.ScenicRenderError, match="invalid media"):
            scenic_render._encode("test-ffmpeg", [], ["-filter_complex", graph, "-map", "[v]"],
                                  tmp_path / "film.mp4", tmp_path)
        assert len(attempts) == 2
        for arguments in attempts:
            assert arguments[arguments.index("-/filter_complex") + 1] == str(tmp_path / "film.filters")
        assert (tmp_path / "film.filters").read_text(encoding="utf-8") == graph
    finally:
        scenic_render._filter_file_option.cache_clear()
