"""Local editing presets, bundled CC0 instrumentals and original demo music.

See assets/scenic_music/catalog.json and README.md for provenance and reuse terms.
No network access is needed to list, preview or render these tracks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import wave

import numpy as np

from app.core.config import get_settings


TEMPLATES = {
    "documentary": {
        "id": "documentary", "name": "旅行纪实", "version": "1",
        "description": "沿游览时间展开，以自然环境声、柔和配乐和地点字幕记录旅程。",
        "music_id": "oga_revelation", "min_clip_seconds": 3.5,
        "max_clip_seconds": 7.0, "color": "#79B6A6", "transition_seconds": 0.25,
        "font_name": "Microsoft YaHei", "photo_zoom": 0.015, "beat_aligned": False,
    },
    "upbeat": {
        "id": "upbeat", "name": "轻快打卡", "version": "1",
        "description": "短镜头、不同点位和轻快节拍，适合分享游览的精彩瞬间。",
        "music_id": "oga_happy_beat", "min_clip_seconds": 1.5,
        "max_clip_seconds": 3.8, "color": "#F0BE62", "transition_seconds": 0.0,
        "font_name": "Microsoft YaHei", "photo_zoom": 0.025, "beat_aligned": True,
    },
    "memory": {
        "id": "memory", "name": "温情回忆", "version": "1",
        "description": "舒缓的钢琴质感、照片缓慢推近和柔和叠化，留下游览纪念。",
        "music_id": "oga_happy_moments", "min_clip_seconds": 4.0,
        "max_clip_seconds": 8.0, "color": "#D4B5CB", "transition_seconds": 0.45,
        "font_name": "Microsoft YaHei", "photo_zoom": 0.035, "beat_aligned": False,
    },
}

MUSIC_VERSION = "1"
MUSIC = {
    "scenic_morning": {"id": "scenic_morning", "name": "山间晨光", "bpm": 82, "seed": 19, "style": "documentary"},
    "scenic_footsteps": {"id": "scenic_footsteps", "name": "轻快脚步", "bpm": 116, "seed": 37, "style": "upbeat"},
    "scenic_keepsake": {"id": "scenic_keepsake", "name": "留念时光", "bpm": 66, "seed": 53, "style": "memory"},
}
MUSIC_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "scenic_music"
DOWNLOADED_MUSIC = {
    track["id"]: track
    for track in json.loads((MUSIC_ASSET_DIR / "catalog.json").read_text(encoding="utf-8"))["tracks"]
}


def music_version(music_id: str) -> str:
    if music_id in DOWNLOADED_MUSIC:
        return DOWNLOADED_MUSIC[music_id]["version"]
    if music_id in MUSIC:
        return MUSIC_VERSION
    raise ValueError("所选配乐不存在，请重新选择配乐")


def template_catalog() -> list[dict]:
    return copy.deepcopy(list(TEMPLATES.values()))


def timeline_duration(clips: list[dict], template: dict) -> float:
    """Net film length, using exactly the renderer's adjacent dissolve policy."""
    durations = [max(0.0, float(c["end_seconds"]) - float(c["start_seconds"]))
                 for c in clips if c.get("included", True)]
    transition = float(template.get("transition_seconds", 0))
    overlap = sum(min(transition, left / 3, right / 3) for left, right in zip(durations, durations[1:]))
    return max(0.0, sum(durations) - overlap)


def _compose(track: dict, destination: Path) -> None:
    """Write a reproducible 16-bar stereo synth instrumental (48 kHz PCM)."""
    rate = 48000
    beat = 60.0 / track["bpm"]
    length = 64 * beat
    samples = np.zeros((int(length * rate), 2), dtype=np.float64)
    rng = np.random.default_rng(track["seed"])

    def tone(midi: int, start: float, duration: float, gain: float, pan: float, pad: bool = False):
        begin = round(start * rate)
        count = min(int(duration * rate), len(samples) - begin)
        if count <= 0:
            return
        t = np.arange(count, dtype=np.float64) / rate
        frequency = 440.0 * 2 ** ((midi - 69) / 12)
        signal = (np.sin(2 * np.pi * frequency * t)
                  + 0.18 * np.sin(4 * np.pi * frequency * t)
                  + 0.045 * np.sin(6 * np.pi * frequency * t))
        attack = np.minimum(t / (0.2 if pad else 0.012), 1)
        decay = np.ones_like(t) if pad else np.exp(-t / max(duration * 0.36, 0.12))
        release = np.minimum(np.maximum(duration - t, 0) / (0.3 if pad else 0.08), 1)
        signal *= attack * decay * release * gain
        samples[begin:begin + count, 0] += signal * np.sqrt((1 - pan) / 2)
        samples[begin:begin + count, 1] += signal * np.sqrt((1 + pan) / 2)

    chords = [(48, 55, 60, 64), (45, 52, 59, 64), (53, 57, 60, 67), (50, 55, 62, 67)]
    melody = [12, 7, 4, 9, 7, 12, 2, 4]
    for bar in range(16):
        chord = chords[(bar + (bar // 8)) % 4]
        at = bar * 4 * beat
        for note in chord:
            tone(note, at, 4.2 * beat, 0.037, (note % 5 - 2) / 4, pad=True)
        tone(chord[0] - 12, at, 3.8 * beat, 0.07, 0)
        subdivisions = 8 if track["style"] == "upbeat" else 4
        for step in range(subdivisions):
            offset = step * 4 * beat / subdivisions
            note = chord[(step + bar) % 4] + 12
            tone(note, at + offset, 1.8 * beat, 0.075, 0.35 * (-1) ** step)
        for step in range(2):
            note = chord[0] + 12 + melody[(2 * bar + step) % len(melody)]
            tone(note, at + (step * 2 + 0.5) * beat, 2.2 * beat, 0.05, -0.15)
        if track["style"] == "upbeat":
            for step in range(4):
                begin = round((at + step * beat) * rate)
                count = min(int(0.12 * rate), len(samples) - begin)
                t = np.arange(count) / rate
                kick = np.sin(2 * np.pi * (58 * t + 9 * (1 - np.exp(-25 * t)))) * np.exp(-35 * t)
                hat = rng.normal(0, 0.016, count) * np.exp(-65 * t)
                samples[begin:begin + count] += (0.07 * kick + hat)[:, None]
    fade = min(rate, len(samples) // 8)
    samples[:fade] *= np.linspace(0, 1, fade)[:, None]
    samples[-fade:] *= np.linspace(1, 0, fade)[:, None]
    peak = max(float(np.abs(samples).max()), 0.01)
    pcm = np.clip(samples * (0.68 / peak), -1, 1)
    with wave.open(str(destination), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes((pcm * 32767).astype("<i2").tobytes())


def music_path(music_id: str) -> Path:
    if music_id in DOWNLOADED_MUSIC:
        track = DOWNLOADED_MUSIC[music_id]
        path = (MUSIC_ASSET_DIR / track["filename"]).resolve()
        if not path.is_relative_to(MUSIC_ASSET_DIR.resolve()) or not path.is_file():
            raise RuntimeError(f"配乐资源缺失：{track['name']}，请恢复随应用附带的音乐文件")
        if hashlib.sha256(path.read_bytes()).hexdigest() != track["sha256"]:
            raise RuntimeError(f"配乐资源校验失败：{track['name']}，请恢复原始音乐文件")
        return path
    if music_id not in MUSIC:
        raise ValueError("所选配乐不存在，请重新选择配乐")
    directory = get_settings().object_storage_dir / ".scenic_music" / MUSIC_VERSION
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{music_id}.wav"
    if not destination.exists():
        descriptor, temporary = tempfile.mkstemp(prefix=f"{music_id}_", suffix=".wav", dir=directory)
        os.close(descriptor)
        temporary_path = Path(temporary)
        try:
            _compose(MUSIC[music_id], temporary_path)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)
    return destination


def music_metadata(music_id: str) -> dict:
    """Resolve only the requested track; unrelated audio cannot block a submit."""
    path = music_path(music_id)
    if music_id in DOWNLOADED_MUSIC:
        track = DOWNLOADED_MUSIC[music_id]
        # Freeze provenance with the actual distributed audio fingerprint.
        # External recordings do not have the synth's exact composition grid.
        return copy.deepcopy({key: value for key, value in track.items() if key != "filename"})
    track = MUSIC[music_id]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    beat = 60.0 / track["bpm"]
    # These scores are synthesized from an exact tempo grid. No uncertain
    # beat estimator is needed, and the grid is cached alongside the audio.
    metadata = {
        "id": track["id"], "name": track["name"], "version": MUSIC_VERSION,
        "origin": "generated", "style": TEMPLATES[track["style"]]["name"],
        "description": "程序合成演示配乐", "author": "MyReID",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "bpm": track["bpm"], "duration_seconds": 64 * beat,
        "beats": [round(index * beat, 6) for index in range(64)],
        "beat_source": "composition_score", "sha256": digest,
        "license": "CC0-1.0", "source": "MyReID 原创程序合成演示配乐，无第三方录音或采样",
        "usage": "可用于演示、剪辑、商用、修改和再分发，无需署名",
    }
    cache = path.with_suffix(".json")
    if not cache.exists():
        cache.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return metadata


def music_catalog() -> list[dict]:
    return [music_metadata(music_id) for music_id in (*DOWNLOADED_MUSIC, *MUSIC)]
