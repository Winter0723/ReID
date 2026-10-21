"""Only deployment administrators may choose physical camera endpoints."""
import hashlib
import re

from app.core.config import get_settings


def validate_camera_source(source: str) -> str:
    from app.services import camera_bridge
    value = str(source).strip()
    if value in get_settings().camera_sources and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        return value
    # The bridge registry is written by the local capture agent, never by API users.
    if camera_bridge.enabled():
        for device in camera_bridge.devices():
            endpoint = device["source"]
            if value in {endpoint, safe_camera_name(endpoint)}:
                return endpoint
    raise ValueError("相机未授权：请使用管理员在 CAMERA_SOURCES 中配置的设备 ID")


def capture_source(source: str) -> str:
    from app.services import camera_bridge
    identity = validate_camera_source(source)
    return camera_bridge.capture_url(get_settings().camera_sources.get(identity, identity))


def safe_camera_name(source: str) -> str:
    value = str(source)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else "camera-" + hashlib.sha256(value.encode()).hexdigest()[:16]


def public_camera_source(source: str) -> str:
    """Hide credentials in historical database rows until administrators migrate them."""
    return safe_camera_name(source) if source else ""


def public_camera_devices(devices: list[dict]) -> list[dict]:
    """Return only configured IDs and trusted bridge devices, never private endpoints."""
    from app.services import camera_bridge
    settings = get_settings()
    allowed_bridge = {d["source"] for d in camera_bridge.devices()} if camera_bridge.enabled() else set()
    result = {}
    for device in devices:
        source = str(device.get("source", ""))
        if source in settings.camera_sources or source in allowed_bridge:
            public_id = public_camera_source(source)
            result[public_id] = {**device, "source": public_id}
    for identity in settings.camera_sources:
        if identity not in result:
            result[identity] = {"source": identity, "label": identity, "type": "configured"}
    return list(result.values())
