"""Measure shared capture and MJPEG delivery without recording camera images."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="0", help="Allowlisted camera source")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--viewers", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.seconds < 1 or not 1 <= args.viewers <= 16:
        parser.error("seconds >= 1 and 1 <= viewers <= 16 required")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "FastAPIBackend"))
    os.environ.update(APP_ENV="test", OBJECT_STORAGE_MODE="local",
                      OBJECT_STORAGE_DIR=str(root / "tmp/camera-benchmark/storage"),
                      GALLERY_DIR=str(root / "tmp/camera-benchmark/gallery"))
    from app.services.central_camera_service import CentralCameraService
    from app.services.camera_preview import preview_frames
    service = CentralCameraService(args.source, use_environment_source=False)
    # Benchmark does not write images, recordings, or change the running app.
    service._save_central_latest_frame = lambda frame: None
    service.acquire(record=False)
    try:
        deadline = time.monotonic() + 15
        while not service.latest_preview()[0] and time.monotonic() < deadline:
            time.sleep(0.05)
        if not service.latest_preview()[0]:
            raise SystemExit("Camera did not produce a frame within 15 seconds")
        time.sleep(1)
        async def viewer():
            frames, size = 0, 0
            started = time.monotonic()
            async for packet in preview_frames(service, duration=args.seconds):
                frames += 1
                size += len(packet)
            elapsed = time.monotonic() - started
            return {"fps": round(frames / elapsed, 2), "frames": frames,
                    "mbps": round(size * 8 / elapsed / 1e6, 2)}
        async def run():
            return await asyncio.gather(*(viewer() for _ in range(args.viewers)))
        viewers = asyncio.run(run())
        status = service.status()
        result = {key: status[key] for key in ("width", "height", "input_width", "input_height", "capture_fps", "preview_fps", "target_fps")}
        result.update(seconds=args.seconds, viewers=viewers)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        service.release(record=False)


if __name__ == "__main__":
    main()
