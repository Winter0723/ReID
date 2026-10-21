"""Read the Windows capture agent's atomic registry; never expose RTSP secrets."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def enabled():
    return bool(os.getenv("MYREID_CAMERA_BRIDGE_DIR"))


def _read(name):
    try:
        return json.loads((Path(os.environ["MYREID_CAMERA_BRIDGE_DIR"]) / name).read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError):
        return {}


def devices():
    registry = _read("registry.json")
    fresh = 0 <= time.time() - registry.get("updated_at", 0) < 20
    return [{**item, "online": fresh and item.get("online", False),
             "output_fps": item.get("output_fps", 0) if fresh and item.get("online") else 0,
             "nonduplicate_fps": item.get("nonduplicate_fps", 0) if fresh and item.get("online") else 0,
             "status": item.get("status", "offline") if fresh else "agent_offline"}
            for item in registry.get("devices", [])]


def resolve(source):
    if enabled() and str(source) == "0":
        # Keep the chosen default even when unplugged: never switch identities silently.
        default = _read("registry.json").get("default_source")
        if default:
            return default
    return source


def capture_url(source):
    source = resolve(source)
    parsed = urlsplit(str(source))
    if enabled() and parsed.hostname == "mediamtx" and parsed.path.startswith("/local/"):
        password = _read("credentials.json").get("reader")
        if not password:
            raise ValueError("摄像头桥接读取凭据缺失")
        return urlunsplit(parsed._replace(netloc=f"reader:{password}@mediamtx:8554"))
    return source


def scenic_points_for_environment(points):
    """Hide unavailable host devices and aliases of the same camera in bridge mode.

    User-configured external RTSP cameras remain independent of local USB discovery.
    Persisted rows are retained for historical media and explicit reconfiguration.
    """
    if not enabled():
        return points
    available = {item["source"] for item in devices() if item.get("online")}
    selected = {}
    for point in points:
        source = str(point.camera_source or point.camera_id or "0").strip()
        canonical = resolve(source)
        local = source.isdigit() or urlsplit(source).hostname == "mediamtx"
        if local and canonical not in available:
            continue
        existing = selected.get(canonical)
        if existing is None or (not existing.is_active and point.is_active):
            selected[canonical] = point
    return list(selected.values())
