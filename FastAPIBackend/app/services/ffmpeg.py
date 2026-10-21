"""Resolve the ffmpeg executable used by API previews and background workers."""

from __future__ import annotations

import shutil
from pathlib import Path


def ffmpeg_executable() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    return bundled if bundled and Path(bundled).is_file() else None
