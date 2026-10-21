"""Latest-only MJPEG delivery, independent of inference and status polling."""
import asyncio
import time

from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.services.central_camera_service import get_camera_service


async def preview_frames(service, *, duration: float):
    index = -1
    deadline = time.monotonic() + duration
    # Capture already caps encoding at target FPS. Poll the immutable pointer
    # more frequently so a second 30Hz clock cannot alias and drop fresh frames.
    interval = min(0.01, 1 / get_settings().camera_preview_fps / 3)
    # No capture lease: viewing must not restart a stopped ingestion camera.
    # Slow clients resume at the newest frame; no per-viewer frame queue.
    while time.monotonic() < deadline:
        jpeg, version = service.latest_preview()
        if jpeg and version != index:
            index = version
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
        await asyncio.sleep(interval)


def preview_response(source: str):
    return StreamingResponse(
        preview_frames(get_camera_service(source), duration=get_settings().asset_url_ttl_seconds),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
    )
